"""Deterministic Phase 3 persistence boundary for the HeatSafe replay.

The in-memory implementation is the executable contract used by tests.  The
BigQuery adapter deliberately emits one fenced multi-statement query per tick;
the disposable-dataset probe remains the only proof of provider behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import hashlib
import json
from typing import Any, Callable, Protocol
from uuid import uuid4

from .engine import (
    advance_tick,
    initialize_state,
    load_scenario,
    load_zone_priors,
    weather_at,
)
from .execution_policy import TickExecutionInputs, plan_tick_execution
from .models import PauseControl, SimulationState, TickResult
from .checkpoint import (
    CheckpointError,
    CheckpointMetadata,
    CheckpointStore,
    checkpoint_object_name,
    decode_checkpoint,
    encode_checkpoint,
)
from .randomness import canonical_checksum
from .telemetry import component_span, emit_component


# Phase 4 adds two control/intervention statements to the fenced publisher.
# Provider evidence measured 241,172,480 billed bytes before the old 250 MB cap
# stopped the remaining statements, so 350 MB is the smallest rounded cap with
# operational headroom for the complete transaction.
MAXIMUM_BYTES_BILLED = 350_000_000
TICK_COUNT = 96


class SimulationRepositoryError(RuntimeError):
    """Base error for a fail-closed repository operation."""


class LeaseConflict(SimulationRepositoryError):
    """Raised when a caller did not win the conditional tick lease."""


class RunConflict(SimulationRepositoryError):
    """Raised when a scenario already has an active run."""


@dataclass(frozen=True, slots=True)
class SimulationRun:
    run_id: str
    scenario_id: str
    scenario_version: str
    seed: int
    status: str
    start_time: datetime
    last_published_tick_index: int | None = None
    last_completed_tick_index: int | None = None
    pending_score_tick_id: str | None = None
    risk_model_version: str | None = None
    forecast_context_version: str | None = None
    forecast_context_seeded_at: datetime | None = None
    forecast_context_point_count: int | None = None


@dataclass(frozen=True, slots=True)
class PersistedTick:
    run_id: str
    scenario_id: str
    tick_id: str
    tick_index: int
    simulation_time: datetime
    snapshot_id: str
    status: str = "PENDING"
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    input_checksum: str | None = None
    output_checksum: str | None = None
    error_code: str | None = None
    input_manifest_json: str | None = None
    input_manifest_checksum: str | None = None
    input_frozen_at: datetime | None = None
    checkpoint_object_name: str | None = None
    checkpoint_format_version: str | None = None
    checkpoint_generation: int | None = None
    checkpoint_compressed_size: int | None = None
    checkpoint_expanded_size: int | None = None
    checkpoint_payload_sha256: str | None = None
    checkpoint_state_checksum: str | None = None
    state_mode: str | None = None
    execution_mode: str | None = None
    execution_reason_codes_json: str | None = None
    low_risk_streak: int | None = None
    recovery_streak: int | None = None
    scoring_outcome: str | None = None
    forecast_source_tick_id: str | None = None
    forecast_source_snapshot_id: str | None = None
    forecast_source_prediction_run_id: str | None = None
    forecast_generated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TickLease:
    tick_id: str
    owner: str
    fencing_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Publication:
    tick: PersistedTick
    result: TickResult
    driver_rows: tuple[dict[str, object], ...]
    zone_rows: tuple[dict[str, object], ...]
    order_rows: tuple[dict[str, object], ...]
    weather_rows: tuple[dict[str, object], ...]
    operation_rows: tuple[dict[str, object], ...]
    demand_rows: tuple[dict[str, object], ...]
    driver_history_rows: tuple[dict[str, object], ...]
    intervention_rows: tuple[dict[str, object], ...]
    consumption_rows: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class TickInputManifest:
    frozen_at: datetime
    controls: tuple[PauseControl, ...]
    checksum: str
    json: str


class SimulationRepository(Protocol):
    def start(self, *, scenario_id: str, scenario_version: str, seed: int) -> SimulationRun: ...
    def status(self, scenario_id: str) -> SimulationRun | None: ...
    def pause(self, scenario_id: str) -> SimulationRun: ...
    def resume(self, scenario_id: str) -> SimulationRun: ...
    def acquire_tick_lease(self, run_id: str, tick_id: str, owner: str) -> TickLease: ...
    def publish_tick(self, run_id: str, tick_id: str, owner: str) -> Publication: ...
    def mark_scored(self, run_id: str, tick_id: str) -> PersistedTick: ...
    def finalize_score(self, run_id: str, tick_id: str, *, succeeded: bool) -> SimulationRun: ...
    def acknowledge_scoring_commit(
        self, run_id: str, tick_id: str, prediction_run_id: str
    ) -> SimulationRun: ...
    def verify_checkpoints(self, run_id: str) -> dict[str, object]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _tick_id(run_id: str, tick_index: int) -> str:
    payload = f"simulation-tick:{run_id}:{tick_index}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def _snapshot_id(run_id: str, tick_index: int) -> str:
    payload = f"simulation-snapshot:{run_id}:{tick_index}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def _event_time(state: SimulationState, minute: int | None) -> datetime | None:
    return None if minute is None else state.start_time + timedelta(minutes=minute)


def _fixture_epoch(scenario_id: str, scenario_version: str) -> datetime:
    fixture = load_scenario(scenario_version)
    fixture_scenario_id = fixture.manifest["scenario_id"]
    if scenario_id != fixture_scenario_id:
        raise SimulationRepositoryError(
            f"scenario {scenario_id!r} does not match fixture "
            f"scenario {fixture_scenario_id!r}"
        )
    epoch = fixture.weather[0]["local_time"]
    if not isinstance(epoch, datetime) or epoch.utcoffset() is None:
        raise SimulationRepositoryError(
            "scenario fixture epoch must be timezone-aware"
        )
    return epoch


def _validate_run_tick_clock(
    run: SimulationRun,
    tick: PersistedTick,
    *,
    fixture: Any,
    result: TickResult | None = None,
) -> None:
    fixture_scenario_id = fixture.manifest["scenario_id"]
    fixture_epoch = fixture.weather[0]["local_time"]
    expected_tick_time = fixture_epoch + timedelta(
        minutes=tick.tick_index * 15
    )
    if (
        run.scenario_id != fixture_scenario_id
        or run.start_time != fixture_epoch
        or tick.simulation_time != expected_tick_time
    ):
        raise SimulationRepositoryError(
            "replay clock drift between fixture, run, and tick ledger"
        )
    if result is not None and (
        result.simulation_time != expected_tick_time
        or result.state.start_time != fixture_epoch
        or result.state.scenario_version != run.scenario_version
        or result.state.seed != run.seed
        or result.state.minute_index != (tick.tick_index + 1) * 15
    ):
        raise SimulationRepositoryError(
            "replay clock drift between tick ledger and engine result"
        )


def _validate_checkpoint_state(
    run: SimulationRun,
    checkpoint_tick: PersistedTick,
    state: SimulationState,
    *,
    fixture: Any,
) -> None:
    expected_minute = (checkpoint_tick.tick_index + 1) * 15
    if (
        state.scenario_version != run.scenario_version
        or state.seed != run.seed
        or state.start_time != fixture.weather[0]["local_time"]
        or state.minute_index != expected_minute
    ):
        raise SimulationRepositoryError(
            "checkpoint replay clock or run identity mismatch"
        )


def validate_publication_clock(
    run: SimulationRun, publication: Publication
) -> None:
    fixture = load_scenario(run.scenario_version)
    _validate_run_tick_clock(
        run, publication.tick, fixture=fixture, result=publication.result
    )


def replay_to_tick(
    run: SimulationRun,
    tick_index: int,
    *,
    controls: tuple[PauseControl, ...] = (),
) -> tuple[SimulationState, TickResult]:
    """Rebuild the deterministic Phase 2 state without an opaque state blob."""
    if not 0 <= tick_index < TICK_COUNT:
        raise SimulationRepositoryError("tick index is outside the 96-tick replay")
    fixture = load_scenario(run.scenario_version)
    zones = load_zone_priors()
    state = initialize_state(seed=run.seed, fixture=fixture, zones=zones)
    with component_span(
        "checkpoint_replay_delta",
        row_count=tick_index,
    ) as replay_span:
        if tick_index == 0:
            replay_span.mark("NO_OP")
        for _ in range(tick_index):
            state = advance_tick(
                state, fixture=fixture, zones=zones, controls=controls
            ).state
    with component_span("advance_tick"):
        result = advance_tick(
            state, fixture=fixture, zones=zones, controls=controls
        )
    return state, result


def _control_to_document(control: PauseControl) -> dict[str, object]:
    return {
        "control_id": control.control_id,
        "driver_ids": list(control.driver_ids),
        "requested_minute": control.requested_minute,
        "pause_duration_minutes": control.pause_duration_minutes,
        "max_start_delay_minutes": control.max_start_delay_minutes,
        "control_event_id": control.control_event_id,
        "proposal_id": control.proposal_id,
        "pause_start_delay_minutes": control.pause_start_delay_minutes,
        "baseline_risk_by_driver": [
            [driver_id, risk.hex()]
            for driver_id, risk in control.baseline_risk_by_driver
        ],
        "action_risk_by_driver": [
            [driver_id, risk.hex()]
            for driver_id, risk in control.action_risk_by_driver
        ],
    }


def _manifest_from_json(raw: str) -> TickInputManifest:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SimulationRepositoryError("stored input manifest is invalid") from exc
    if not isinstance(document, dict) or set(document) != {
        "controls",
        "frozen_at",
        "version",
    } or document["version"] != 1:
        raise SimulationRepositoryError("stored input manifest has unknown fields")
    try:
        frozen_at = datetime.fromisoformat(document["frozen_at"])
        controls = tuple(
            PauseControl(
                control_id=item["control_id"],
                driver_ids=tuple(item["driver_ids"]),
                requested_minute=int(item["requested_minute"]),
                pause_duration_minutes=int(item["pause_duration_minutes"]),
                max_start_delay_minutes=int(item["max_start_delay_minutes"]),
                control_event_id=item["control_event_id"],
                proposal_id=item["proposal_id"],
                pause_start_delay_minutes=int(item["pause_start_delay_minutes"]),
                baseline_risk_by_driver=tuple(
                    (driver_id, float.fromhex(risk))
                    for driver_id, risk in item["baseline_risk_by_driver"]
                ),
                action_risk_by_driver=tuple(
                    (driver_id, float.fromhex(risk))
                    for driver_id, risk in item["action_risk_by_driver"]
                ),
            )
            for item in document["controls"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SimulationRepositoryError("stored input manifest is invalid") from exc
    if frozen_at.tzinfo is None:
        raise SimulationRepositoryError("stored input manifest time is naive")
    normalized = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return TickInputManifest(
        frozen_at=frozen_at,
        controls=controls,
        checksum=hashlib.sha256(normalized.encode()).hexdigest(),
        json=normalized,
    )


def _new_manifest(
    controls: tuple[PauseControl, ...], frozen_at: datetime
) -> TickInputManifest:
    document = {
        "version": 1,
        "frozen_at": frozen_at.isoformat(timespec="microseconds"),
        "controls": [_control_to_document(control) for control in controls],
    }
    raw = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return TickInputManifest(
        frozen_at=frozen_at,
        controls=controls,
        checksum=hashlib.sha256(raw.encode()).hexdigest(),
        json=raw,
    )


def publication_rows(
    run: SimulationRun,
    tick: PersistedTick,
    result: TickResult,
    *,
    controls: tuple[PauseControl, ...] = (),
) -> Publication:
    """Map engine truth into lineage-complete BigQuery row dictionaries."""
    state = result.state
    priors = {zone.zone_id: zone for zone in load_zone_priors()}
    driver_rows = tuple(
        {
            "simulation_run_id": run.run_id,
            "scenario_id": run.scenario_id,
            "driver_id_hash": driver.driver_id_hash,
            "last_tick_id": tick.tick_id,
            "event_time": result.simulation_time,
            "zone_id": driver.zone_id,
            "latitude": driver.latitude,
            "longitude": driver.longitude,
            "status": driver.status.value,
            "shift_started_at": None,
            "shift_ends_at": None,
            "current_order_id": driver.current_order_id,
            "current_intervention_id": driver.current_intervention_id,
            "online_minutes_24h": int(driver.schedule_bits).bit_count(),
            "trips_60m": driver.trips_60m,
            "distance_km_60m": driver.distance_km_60m,
            "workload_intensity": 0.0,
            "continuous_exposure_minutes": driver.continuous_exposure_minutes,
            "heat_dose_120m": driver.heat_dose_120m,
            "rest_minutes_120m": driver.rest_minutes_120m,
            "hydration_gap_minutes": driver.hydration_gap_minutes,
            "route_heat_load": 0.0,
            "acclimatization_class": driver.acclimatization_class.value,
            "earnings_60m_vnd": driver.earnings_60m_vnd,
            "platform_contribution_60m_vnd": driver.platform_contribution_60m_vnd,
            "generator_version": state.generator_version,
            "is_simulated": True,
            "updated_at": _utc_now(),
        }
        for driver in state.drivers
    )
    zone_rows = tuple(
        {
            "scenario_id": run.scenario_id,
            "snapshot_id": tick.snapshot_id,
            "zone_id": zone.zone_id,
            "name": priors[zone.zone_id].name,
            "latitude": priors[zone.zone_id].latitude,
            "longitude": priors[zone.zone_id].longitude,
            "observed_at": result.simulation_time,
            "weather_observed_at": result.weather.event_time,
            "operations_observed_at": result.simulation_time,
            "refreshed_at": _utc_now(),
            "temperature_c": result.weather.temperature_c,
            "humidity_percent": result.weather.humidity_percent,
            "heat_index_c": result.weather.heat_index_c,
            "active_drivers": zone.active_drivers,
            "fresh_drivers": zone.fresh_drivers,
            "exposed_2h": zone.exposed_2h,
            "exposed_4h": zone.exposed_4h,
            "forecast_requests_30m": zone.requests_15m * 2,
            "avg_platform_contribution_vnd": priors[zone.zone_id].avg_platform_contribution_vnd,
            "avg_driver_earnings_vnd": priors[zone.zone_id].avg_driver_earnings_vnd,
            "coolstop_name": priors[zone.zone_id].coolstop_name,
            "coolstop_latitude": priors[zone.zone_id].coolstop_latitude,
            "coolstop_longitude": priors[zone.zone_id].coolstop_longitude,
            "online_drivers": zone.online_drivers,
            "idle_drivers": zone.idle_drivers,
            "to_pickup_drivers": zone.to_pickup_drivers,
            "on_trip_drivers": zone.on_trip_drivers,
            "to_coolstop_drivers": zone.to_coolstop_drivers,
            "paused_drivers": zone.paused_drivers,
            "exposed_2_to_4h": zone.exposed_2_to_4h,
            "requests_15m": zone.requests_15m,
            "matched_15m": zone.matched_15m,
            "completed_15m": zone.completed_15m,
            "cancelled_15m": zone.cancelled_15m,
            "unfulfilled_15m": zone.unfulfilled_15m,
            "fulfillment_rate": zone.fulfillment_rate,
            "median_wait_minutes": None,
            "p90_wait_minutes": None,
            "simulation_run_id": run.run_id,
            "tick_id": tick.tick_id,
            "generator_version": state.generator_version,
            "weather_is_simulated": True,
            "operations_is_simulated": True,
            "source": "Deterministic HeatSafe simulation",
        }
        for zone in result.zones
    )
    orders_by_id = {order.order_id: order for order in state.orders}
    order_rows = tuple(
        {
            "event_id": event.event_id,
            "simulation_run_id": run.run_id,
            "tick_id": tick.tick_id,
            "scenario_id": run.scenario_id,
            "order_id": event.order_id,
            "event_time": _event_time(state, event.event_minute),
            "event_type": event.event_type.value,
            "status": orders_by_id[event.order_id].status.value if event.order_id in orders_by_id else event.event_type.value,
            "driver_id_hash": event.driver_id_hash,
            "origin_zone_id": event.zone_id,
            "destination_zone_id": event.zone_id,
            "zone_id": event.zone_id,
            "requested_at": _event_time(state, event.event_minute),
            "accepted_at": None,
            "pickup_at": None,
            "dropoff_at": None,
            "cancelled_at": None,
            "distance_km": None,
            "estimated_duration_minutes": None,
            "actual_duration_minutes": None,
            "wait_minutes": None,
            "fare_vnd": None,
            "driver_pay_vnd": None,
            "platform_contribution_vnd": None,
            "generator_version": state.generator_version,
            "is_simulated": True,
        }
        for event in state.events
    )
    weather_rows = tuple(
        {
            "scenario_id": run.scenario_id, "snapshot_id": tick.snapshot_id,
            "zone_id": zone.zone_id, "name": priors[zone.zone_id].name,
            "latitude": priors[zone.zone_id].latitude, "longitude": priors[zone.zone_id].longitude,
            "temperature_c": result.weather.temperature_c,
            "humidity_percent": result.weather.humidity_percent,
            "heat_index_c": result.weather.heat_index_c,
            "observed_at": result.weather.event_time, "ingested_at": _utc_now(),
            "source": "Deterministic HeatSafe simulation", "raw_gcs_uri": "gs://heatsafe-simulated/phase3",
            "is_simulated": True, "simulation_run_id": run.run_id, "tick_id": tick.tick_id,
            "source_observed_at": result.weather.event_time, "source_next_observed_at": result.weather.event_time,
            "source_interpolation_fraction": 0.0, "source_temperature_c": result.weather.temperature_c,
            "temperature_adjustment_c": 0.0, "station_peak_anchor_c": 41.1,
            "apparent_temperature_c": result.weather.heat_index_c,
            "wind_speed_mps": result.weather.wind_speed_mps, "wind_gust_mps": None,
            "precipitation_mm": result.weather.precipitation_mm,
            "cloud_cover_pct": result.weather.cloud_cover_pct,
            "shortwave_radiation_wm2": result.weather.shortwave_radiation_wm2,
            "utci_c": None, "derivation_version": "stateful-replay-v1",
            "generator_version": state.generator_version,
        }
        for zone in result.zones
    )
    operation_rows = tuple(
        {
            "scenario_id": run.scenario_id, "snapshot_id": tick.snapshot_id,
            "zone_id": zone.zone_id, "observed_at": result.simulation_time,
            "active_drivers": zone.active_drivers, "fresh_drivers": zone.fresh_drivers,
            "exposed_2h": zone.exposed_2h, "exposed_4h": zone.exposed_4h,
            "forecast_requests_30m": zone.requests_15m * 2,
            "avg_platform_contribution_vnd": priors[zone.zone_id].avg_platform_contribution_vnd,
            "avg_driver_earnings_vnd": priors[zone.zone_id].avg_driver_earnings_vnd,
            "is_simulated": True, "simulation_run_id": run.run_id, "tick_id": tick.tick_id,
            "online_drivers": zone.online_drivers, "idle_drivers": zone.idle_drivers,
            "to_pickup_drivers": zone.to_pickup_drivers, "on_trip_drivers": zone.on_trip_drivers,
            "to_coolstop_drivers": zone.to_coolstop_drivers, "paused_drivers": zone.paused_drivers,
            "exposed_2_to_4h": zone.exposed_2_to_4h, "requests_15m": zone.requests_15m,
            "matched_15m": zone.matched_15m, "completed_15m": zone.completed_15m,
            "cancelled_15m": zone.cancelled_15m, "unfulfilled_15m": zone.unfulfilled_15m,
            "median_wait_minutes": None, "p90_wait_minutes": None,
            "fulfillment_rate": zone.fulfillment_rate, "generator_version": state.generator_version,
        }
        for zone in result.zones
    )
    demand_rows = tuple(
        {"scenario_id": run.scenario_id, "zone_id": zone.zone_id,
         "interval_start": result.simulation_time, "requests": zone.requests_15m,
         "is_simulated": True, "simulation_run_id": run.run_id,
         "tick_id": tick.tick_id, "generator_version": state.generator_version}
        for zone in result.zones
    )
    driver_history_rows = tuple(
        {"state_id": canonical_checksum((run.run_id, tick.tick_id, driver.driver_id_hash))[:32],
         "scenario_id": run.scenario_id, "event_time": result.simulation_time,
         "driver_id_hash": driver.driver_id_hash, "zone_id": driver.zone_id,
         "heat_index_c": result.weather.heat_index_c, "humidity_percent": result.weather.humidity_percent,
         "continuous_exposure_minutes": driver.continuous_exposure_minutes,
         "trips_60m": driver.trips_60m, "distance_km_60m": driver.distance_km_60m,
         "rest_minutes_120m": driver.rest_minutes_120m,
         "hydration_gap_minutes": driver.hydration_gap_minutes, "route_heat_load": 0.0,
         "workload_intensity": 0.0, "is_simulated": True, "simulation_run_id": run.run_id,
         "tick_id": tick.tick_id, "driver_status": driver.status.value,
         "heat_dose_120m": driver.heat_dose_120m,
         "acclimatization_class": driver.acclimatization_class.value,
         "current_order_id": driver.current_order_id,
         "current_intervention_id": driver.current_intervention_id,
         "earnings_60m_vnd": driver.earnings_60m_vnd,
         "platform_contribution_60m_vnd": driver.platform_contribution_60m_vnd,
         "generator_version": state.generator_version}
        for driver in state.drivers
    )
    intervention_rows = tuple(
        {
            "event_id": canonical_checksum(
                (
                    run.run_id,
                    tick.tick_id,
                    intervention.intervention_id,
                    intervention.status.value,
                    intervention.completed_rest_minutes,
                )
            )[:32],
            "simulation_run_id": run.run_id,
            "tick_id": tick.tick_id,
            "scenario_id": run.scenario_id,
            "intervention_id": intervention.intervention_id,
            "proposal_id": intervention.proposal_id,
            "driver_id_hash": intervention.driver_id_hash,
            "zone_id": intervention.zone_id,
            "event_time": result.simulation_time,
            "event_type": intervention.status.value,
            "pause_start_delay_minutes": intervention.pause_start_delay_minutes,
            "planned_duration_minutes": intervention.planned_duration_minutes,
            "completed_rest_minutes": intervention.completed_rest_minutes,
            "coolstop_name": priors[intervention.zone_id].coolstop_name,
            "baseline_risk_probability": intervention.baseline_risk_probability,
            "action_risk_probability": intervention.action_risk_probability,
            "earnings_delta_vnd": None,
            "is_simulated": True,
            "generator_version": state.generator_version,
        }
        for intervention in state.interventions
    )
    earliest_by_control_event: dict[str, int] = {}
    for control in controls:
        event_id = control.control_event_id or control.control_id
        earliest_by_control_event[event_id] = min(
            control.requested_minute,
            earliest_by_control_event.get(event_id, control.requested_minute),
        )
    due_control_events = {
        event_id
        for event_id, requested_minute in earliest_by_control_event.items()
        if state.minute_index - 15 <= requested_minute < state.minute_index
    }
    consumption_rows = tuple(
        {
            "consumption_id": canonical_checksum(
                (event_id, run.run_id, tick.tick_id)
            )[:32],
            "control_event_id": event_id,
            "scenario_id": run.scenario_id,
            "simulation_run_id": run.run_id,
            "consumed_by_tick_id": tick.tick_id,
            "outcome": "APPLIED",
            "recorded_at": _utc_now(),
            "rejection_reason": None,
            "generator_version": state.generator_version,
            "is_simulated": True,
        }
        for event_id in sorted(due_control_events)
    )
    return Publication(
        tick=tick, result=result, driver_rows=driver_rows, zone_rows=zone_rows,
        order_rows=order_rows, weather_rows=weather_rows, operation_rows=operation_rows,
        demand_rows=demand_rows, driver_history_rows=driver_history_rows,
        intervention_rows=intervention_rows,
        consumption_rows=consumption_rows,
    )


@dataclass
class InMemorySimulationRepository:
    """Reference implementation used by CLI tests and crash/retry simulations."""

    now: Callable[[], datetime] = _utc_now
    lease_seconds: int = 360
    runs: dict[str, SimulationRun] = field(default_factory=dict)
    scenario_runs: dict[str, str] = field(default_factory=dict)
    ticks: dict[str, PersistedTick] = field(default_factory=dict)
    published: dict[str, Publication] = field(default_factory=dict)
    controls: dict[str, PauseControl] = field(default_factory=dict)
    checkpoint_store: CheckpointStore | None = None
    state_mode: str = "oracle"
    manifests: dict[str, TickInputManifest] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state_mode not in {"oracle", "checkpoint"}:
            raise ValueError("state_mode must be oracle or checkpoint")

    def queue_controls(self, controls: tuple[PauseControl, ...]) -> None:
        for control in controls:
            existing = self.controls.get(control.control_id)
            if existing is not None and existing != control:
                raise SimulationRepositoryError("control identity payload changed")
            self.controls[control.control_id] = control

    def _controls_for_run(
        self,
        run: SimulationRun,
        tick: PersistedTick | None = None,
        *,
        authorization_time: datetime | None = None,
    ) -> tuple[PauseControl, ...]:
        return tuple(
            self.controls[key]
            for key in sorted(self.controls)
        )

    def freeze_tick_inputs(
        self, run: SimulationRun, tick: PersistedTick
    ) -> TickInputManifest:
        existing = self.manifests.get(tick.tick_id)
        if existing is not None:
            return existing
        if tick.input_manifest_json:
            manifest = _manifest_from_json(tick.input_manifest_json)
            if (
                tick.input_manifest_checksum
                and tick.input_manifest_checksum != manifest.checksum
            ):
                raise SimulationRepositoryError(
                    "stored input manifest checksum mismatch"
                )
        else:
            frozen_at = self.now()
            with component_span("controls_load") as controls_span:
                controls = self._controls_for_run(
                    run, tick, authorization_time=frozen_at
                )
                controls_span.set(row_count=len(controls))
            manifest = _new_manifest(controls, frozen_at)
            self.ticks[tick.tick_id] = replace(
                tick,
                input_manifest_json=manifest.json,
                input_manifest_checksum=manifest.checksum,
                input_frozen_at=manifest.frozen_at,
                input_checksum=manifest.checksum,
                state_mode=self.state_mode,
            )
        self.manifests[tick.tick_id] = manifest
        return manifest

    def _checkpoint_metadata(
        self, tick: PersistedTick
    ) -> CheckpointMetadata | None:
        required = (
            tick.checkpoint_object_name,
            tick.checkpoint_format_version,
            tick.checkpoint_generation,
            tick.checkpoint_compressed_size,
            tick.checkpoint_expanded_size,
            tick.checkpoint_payload_sha256,
            tick.checkpoint_state_checksum,
        )
        if all(value is None for value in required):
            return None
        if any(value is None for value in required):
            raise SimulationRepositoryError("checkpoint metadata is incomplete")
        return CheckpointMetadata(
            object_name=str(tick.checkpoint_object_name),
            format_version=str(tick.checkpoint_format_version),
            generation=int(tick.checkpoint_generation),
            compressed_size=int(tick.checkpoint_compressed_size),
            expanded_size=int(tick.checkpoint_expanded_size),
            payload_sha256=str(tick.checkpoint_payload_sha256),
            state_checksum=str(tick.checkpoint_state_checksum),
        )

    def _restore_predecessor(
        self, run: SimulationRun, tick: PersistedTick
    ) -> tuple[SimulationState | None, int]:
        if self.checkpoint_store is None or tick.tick_index == 0:
            return None, -1
        candidates = sorted(
            (
                candidate
                for candidate in self.ticks.values()
                if candidate.run_id == run.run_id
                and candidate.tick_index < tick.tick_index
                and candidate.status == "SUCCEEDED"
                and candidate.checkpoint_object_name
            ),
            key=lambda candidate: candidate.tick_index,
            reverse=True,
        )
        for candidate in candidates:
            try:
                metadata = self._checkpoint_metadata(candidate)
                assert metadata is not None
                with component_span("checkpoint_restore"):
                    data = self.checkpoint_store.get(metadata)
                    state = decode_checkpoint(
                        data,
                        expected_payload_sha256=metadata.payload_sha256,
                        expected_state_checksum=metadata.state_checksum,
                    )
                    _validate_checkpoint_state(
                        run,
                        candidate,
                        state,
                        fixture=load_scenario(run.scenario_version),
                    )
                return state, candidate.tick_index
            except (CheckpointError, SimulationRepositoryError):
                emit_component(
                    "checkpoint_restore",
                    elapsed_ms=0,
                    outcome="SKIPPED",
                    error_code="CHECKPOINT_FALLBACK",
                    row_count=candidate.tick_index,
                )
                continue
        return None, -1

    def compute_from_manifest(
        self,
        run: SimulationRun,
        tick: PersistedTick,
        manifest: TickInputManifest,
        *,
        base_state: SimulationState | None = None,
        restored_tick_index: int | None = None,
    ) -> tuple[SimulationState, TickResult]:
        fixture = load_scenario(run.scenario_version)
        zones = load_zone_priors()
        _validate_run_tick_clock(run, tick, fixture=fixture)
        if restored_tick_index is None and self.state_mode == "checkpoint":
            state, restored_tick_index = self._restore_predecessor(run, tick)
        elif restored_tick_index is None:
            state, restored_tick_index = None, -1
        else:
            state = base_state
        assert restored_tick_index is not None
        if state is None:
            state = initialize_state(seed=run.seed, fixture=fixture, zones=zones)
            restored_tick_index = -1
        elif restored_tick_index >= 0:
            restored_tick = next(
                (
                    candidate
                    for candidate in self.ticks.values()
                    if candidate.run_id == run.run_id
                    and candidate.tick_index == restored_tick_index
                ),
                None,
            )
            if restored_tick is None:
                raise SimulationRepositoryError(
                    "restored checkpoint tick is missing from the run ledger"
                )
            _validate_checkpoint_state(
                run, restored_tick, state, fixture=fixture
            )
        with component_span(
            "checkpoint_replay_delta",
            row_count=tick.tick_index - restored_tick_index - 1,
        ) as replay_span:
            replay_count = tick.tick_index - restored_tick_index - 1
            if replay_count == 0:
                replay_span.mark("NO_OP")
            for replay_index in range(
                restored_tick_index + 1, tick.tick_index
            ):
                historical = next(
                    (
                        candidate
                        for candidate in self.ticks.values()
                        if candidate.run_id == run.run_id
                        and candidate.tick_index == replay_index
                    ),
                    None,
                )
                historical_manifest = (
                    self.manifests.get(historical.tick_id)
                    if historical is not None
                    else None
                )
                if (
                    historical_manifest is None
                    and historical is not None
                    and historical.input_manifest_json
                ):
                    historical_manifest = _manifest_from_json(
                        historical.input_manifest_json
                    )
                if historical_manifest is None:
                    if self.state_mode == "checkpoint":
                        raise SimulationRepositoryError(
                            "recovery requires every intermediate frozen input manifest"
                        )
                    historical_manifest = manifest
                state = advance_tick(
                    state,
                    fixture=fixture,
                    zones=zones,
                    controls=historical_manifest.controls,
                ).state
        with component_span("advance_tick"):
            result = advance_tick(
                state,
                fixture=fixture,
                zones=zones,
                controls=manifest.controls,
            )
        _validate_run_tick_clock(
            run, tick, fixture=fixture, result=result
        )
        return state, result

    def store_checkpoint(
        self,
        run: SimulationRun,
        tick: PersistedTick,
        manifest: TickInputManifest,
        state: SimulationState,
    ) -> CheckpointMetadata | None:
        if self.checkpoint_store is None:
            return None
        with component_span("checkpoint_encode"):
            encoded = encode_checkpoint(state)
        name = checkpoint_object_name(
            run_id=run.run_id,
            tick_index=tick.tick_index,
            input_checksum=manifest.checksum,
        )
        with component_span("checkpoint_upload"):
            metadata = self.checkpoint_store.put(name, encoded)
        return metadata

    def plan_execution(
        self,
        run: SimulationRun,
        tick: PersistedTick,
        manifest: TickInputManifest,
        result: TickResult,
    ):
        from heatsafe.risk import heat_tier

        fixture = load_scenario(run.scenario_version)
        lookahead = max(
            (
                weather_at(fixture, min(95 * 15, result.state.minute_index + delta))
                .heat_index_c
                for delta in (15, 30)
            ),
            default=result.weather.heat_index_c,
        )
        predecessor = next(
            (
                candidate
                for candidate in self.ticks.values()
                if candidate.run_id == run.run_id
                and candidate.tick_index == tick.tick_index - 1
            ),
            None,
        )
        active_interventions = sum(
            item.status.value not in {"COMPLETED", "CANCELLED"}
            for item in result.state.interventions
        )
        forecast_source_tick = (
            predecessor.forecast_source_tick_id if predecessor else None
        )
        forecast_source_index = next(
            (
                candidate.tick_index
                for candidate in self.ticks.values()
                if candidate.run_id == run.run_id
                and candidate.tick_id == forecast_source_tick
            ),
            tick.tick_index,
        )
        return plan_tick_execution(
            TickExecutionInputs(
                current_tier=heat_tier(result.weather.heat_index_c),
                lookahead_tier=heat_tier(lookahead),
                exposed_2h=sum(zone.exposed_2h for zone in result.zones),
                exposed_4h=sum(zone.exposed_4h for zone in result.zones),
                pending_controls=len(manifest.controls),
                active_interventions=active_interventions,
                previous_mode=(
                    predecessor.execution_mode
                    if predecessor and predecessor.execution_mode
                    else "FULL"
                ),
                low_risk_streak=(
                    predecessor.low_risk_streak
                    if predecessor and predecessor.low_risk_streak is not None
                    else 0
                ),
                recovery_streak=(
                    predecessor.recovery_streak
                    if predecessor and predecessor.recovery_streak is not None
                    else 0
                ),
                persisted_scoring_failure=tick.status == "SCORE_FAILED",
                forecast_available=bool(
                    predecessor
                    and predecessor.forecast_source_tick_id
                    and predecessor.forecast_source_prediction_run_id
                ),
                full_ticks_since_generation=max(
                    0, tick.tick_index - forecast_source_index
                ),
            )
        )

    def start(self, *, scenario_id: str, scenario_version: str, seed: int) -> SimulationRun:
        active = self.status(scenario_id)
        if active and active.status in {"RUNNING", "PAUSED"}:
            raise RunConflict(f"scenario {scenario_id} already has an active run")
        simulation_start = _fixture_epoch(scenario_id, scenario_version)
        run_id = uuid4().hex
        run = SimulationRun(
            run_id,
            scenario_id,
            scenario_version,
            seed,
            "RUNNING",
            simulation_start,
        )
        self.runs[run_id] = run
        self.scenario_runs[scenario_id] = run_id
        for index in range(TICK_COUNT):
            tick = PersistedTick(
                run_id, scenario_id, _tick_id(run_id, index), index,
                run.start_time + timedelta(minutes=index * 15), _snapshot_id(run_id, index),
            )
            self.ticks[tick.tick_id] = tick
        return run

    def status(self, scenario_id: str) -> SimulationRun | None:
        run_id = self.scenario_runs.get(scenario_id)
        return self.runs.get(run_id) if run_id else None

    def pause(self, scenario_id: str) -> SimulationRun:
        run = self._require_run_for_scenario(scenario_id)
        if run.status == "COMPLETED":
            raise SimulationRepositoryError("a completed run cannot be paused")
        run = self._replace_run(run, status="PAUSED")
        return run

    def resume(self, scenario_id: str) -> SimulationRun:
        run = self._require_run_for_scenario(scenario_id)
        if run.status != "PAUSED":
            raise SimulationRepositoryError("only a paused run can be resumed")
        return self._replace_run(run, status="RUNNING")

    def acquire_tick_lease(self, run_id: str, tick_id: str, owner: str) -> TickLease:
        run = self._require_run(run_id)
        if run.status != "RUNNING":
            raise SimulationRepositoryError("only a running simulation can acquire a tick")
        tick = self._require_tick(run_id, tick_id)
        if run.pending_score_tick_id and run.pending_score_tick_id != tick_id:
            raise SimulationRepositoryError(
                "the pending score must be finalized before a later tick can run"
            )
        if tick.status in {"SNAPSHOT_READY", "SCORED", "SCORE_FAILED", "SUCCEEDED"}:
            return TickLease(tick_id, owner, "already-published", self.now())
        now = self.now()
        if tick.lease_expires_at and tick.lease_expires_at > now and tick.lease_owner != owner:
            raise LeaseConflict("a fresh lease is already owned by another caller")
        token = uuid4().hex
        expires = now + timedelta(seconds=self.lease_seconds)
        self.ticks[tick_id] = replace(
            tick, status="LEASED", lease_owner=token, lease_expires_at=expires
        )
        return TickLease(tick_id, owner, token, expires)

    def publish_tick(self, run_id: str, tick_id: str, owner: str) -> Publication:
        run = self._require_run(run_id)
        tick = self._require_tick(run_id, tick_id)
        if tick.status in {"SNAPSHOT_READY", "SCORED", "SCORE_FAILED", "SUCCEEDED"}:
            # A restarted worker reloads ticks from durable storage but does not
            # carry the previous process's in-memory Publication cache.  Rebuild
            # the deterministic projection for the caller without issuing writes.
            publication = self.published.get(tick_id)
            if publication is None:
                if self.state_mode == "checkpoint":
                    base_state, restored_tick_index = self._restore_predecessor(
                        run, tick
                    )
                else:
                    base_state, restored_tick_index = None, -1
                manifest = self.freeze_tick_inputs(run, tick)
                _, result = self.compute_from_manifest(
                    run,
                    tick,
                    manifest,
                    base_state=base_state,
                    restored_tick_index=restored_tick_index,
                )
                with component_span("publication_projection"):
                    publication = publication_rows(
                        run, tick, result, controls=manifest.controls
                    )
                self.published[tick_id] = publication
            return publication
        if tick.status != "LEASED" or not tick.lease_owner or tick.lease_expires_at is None:
            raise LeaseConflict("tick must have a current lease before publication")
        if tick.lease_expires_at <= self.now():
            raise LeaseConflict("tick lease expired before publication")
        if owner != tick.lease_owner:
            raise LeaseConflict("publication requires the exact fencing token")
        if self.state_mode == "checkpoint":
            base_state, restored_tick_index = self._restore_predecessor(run, tick)
        else:
            base_state, restored_tick_index = None, -1
        with component_span("input_freeze"):
            manifest = self.freeze_tick_inputs(run, tick)
        tick = self._require_tick(run_id, tick_id)
        state, result = self.compute_from_manifest(
            run,
            tick,
            manifest,
            base_state=base_state,
            restored_tick_index=restored_tick_index,
        )
        execution = self.plan_execution(run, tick, manifest, result)
        checkpoint = self.store_checkpoint(
            run, tick, manifest, result.state
        )
        return self.commit_publication(
            run, tick, manifest, result, checkpoint, execution
        )

    def commit_publication(
        self,
        run: SimulationRun,
        tick: PersistedTick,
        manifest: TickInputManifest,
        result: TickResult,
        checkpoint: CheckpointMetadata | None,
        execution: Any,
    ) -> Publication:
        """Commit the in-memory mirror only after all prior boundaries succeed."""
        predecessor = next(
            (
                candidate
                for candidate in self.ticks.values()
                if candidate.run_id == run.run_id
                and candidate.tick_index == tick.tick_index - 1
            ),
            None,
        )
        updated = replace(
            tick,
            status="SNAPSHOT_READY",
            input_checksum=manifest.checksum,
            output_checksum=result.checksum,
            error_code="MODEL_INPUT_OOD" if result.model_input_ood else None,
            checkpoint_object_name=(
                checkpoint.object_name if checkpoint else None
            ),
            checkpoint_format_version=(
                checkpoint.format_version if checkpoint else None
            ),
            checkpoint_generation=(
                checkpoint.generation if checkpoint else None
            ),
            checkpoint_compressed_size=(
                checkpoint.compressed_size if checkpoint else None
            ),
            checkpoint_expanded_size=(
                checkpoint.expanded_size if checkpoint else None
            ),
            checkpoint_payload_sha256=(
                checkpoint.payload_sha256 if checkpoint else None
            ),
            checkpoint_state_checksum=(
                checkpoint.state_checksum if checkpoint else None
            ),
            state_mode=self.state_mode,
            execution_mode=execution.mode,
            execution_reason_codes_json=json.dumps(
                list(execution.reason_codes), separators=(",", ":")
            ),
            low_risk_streak=execution.next_low_risk_streak,
            recovery_streak=execution.next_recovery_streak,
            forecast_source_tick_id=(
                tick.tick_id
                if execution.generate_forecast
                else (
                    predecessor.forecast_source_tick_id
                    if execution.reuse_forecast and predecessor
                    else None
                )
            ),
            forecast_source_snapshot_id=(
                tick.snapshot_id
                if execution.generate_forecast
                else (
                    predecessor.forecast_source_snapshot_id
                    if execution.reuse_forecast and predecessor
                    else None
                )
            ),
            forecast_source_prediction_run_id=(
                predecessor.forecast_source_prediction_run_id
                if execution.reuse_forecast and predecessor
                else None
            ),
        )
        with component_span("publication_projection"):
            publication = publication_rows(
                run, updated, result, controls=manifest.controls
            )
        self.ticks[tick.tick_id] = updated
        self.published[tick.tick_id] = publication
        self._replace_run(
            run,
            last_published_tick_index=tick.tick_index,
            pending_score_tick_id=tick.tick_id,
        )
        return publication

    def finalize_score(self, run_id: str, tick_id: str, *, succeeded: bool) -> SimulationRun:
        run = self._require_run(run_id)
        tick = self._require_tick(run_id, tick_id)
        if tick.status == "SUCCEEDED":
            return run
        if tick.status == "SCORE_FAILED" and not succeeded:
            return run
        if tick.status not in {"SNAPSHOT_READY", "SCORED", "SCORE_FAILED"} or run.pending_score_tick_id != tick_id:
            raise SimulationRepositoryError(
                "only the pending SNAPSHOT_READY/SCORED tick can be finalized"
            )
        if not succeeded:
            self.ticks[tick_id] = replace(tick, status="SCORE_FAILED")
            return run
        self.ticks[tick_id] = replace(tick, status="SUCCEEDED")
        return self._replace_run(
            run,
            status="COMPLETED" if tick.tick_index == TICK_COUNT - 1 else "RUNNING",
            last_completed_tick_index=tick.tick_index,
            pending_score_tick_id=None,
        )

    def mark_scored(self, run_id: str, tick_id: str) -> PersistedTick:
        run = self._require_run(run_id)
        tick = self._require_tick(run_id, tick_id)
        if run.pending_score_tick_id != tick_id:
            raise SimulationRepositoryError("only the pending tick can be marked scored")
        if tick.status == "SCORED":
            return tick
        if tick.status not in {"SNAPSHOT_READY", "SCORE_FAILED"}:
            raise SimulationRepositoryError(
                "only SNAPSHOT_READY/SCORE_FAILED can be marked scored"
            )
        scored = replace(
            tick,
            status="SCORED",
            scoring_outcome=(
                "SKIPPED_LOW_RISK"
                if tick.execution_mode in {"MONITOR", "RECOVERY"}
                else "SCORED"
            ),
        )
        self.ticks[tick_id] = scored
        return scored

    def record_scoring_lineage(
        self, run_id: str, tick_id: str, prediction_run_id: str
    ) -> PersistedTick:
        tick = self._require_tick(run_id, tick_id)
        if tick.forecast_source_tick_id == tick.tick_id:
            tick = replace(
                tick,
                forecast_source_prediction_run_id=prediction_run_id,
                forecast_generated_at=self.now(),
            )
            self.ticks[tick_id] = tick
        return tick

    def acknowledge_scoring_commit(
        self, run_id: str, tick_id: str, prediction_run_id: str
    ) -> SimulationRun:
        """Mirror a scorer transaction that already finalized durable cursors."""
        run = self._require_run(run_id)
        tick = self._require_tick(run_id, tick_id)
        if run.pending_score_tick_id != tick_id:
            raise SimulationRepositoryError(
                "durable scoring commit must match the pending tick"
            )
        changes: dict[str, object] = {
            "status": "SUCCEEDED",
            "scoring_outcome": (
                "SKIPPED_LOW_RISK"
                if tick.execution_mode in {"MONITOR", "RECOVERY"}
                else "SCORED"
            ),
        }
        if tick.forecast_source_tick_id == tick.tick_id:
            changes.update(
                {
                    "forecast_source_prediction_run_id": prediction_run_id,
                    "forecast_generated_at": self.now(),
                }
            )
        self.ticks[tick_id] = replace(tick, **changes)
        return self._replace_run(
            run,
            status=(
                "COMPLETED" if tick.tick_index == TICK_COUNT - 1 else run.status
            ),
            last_completed_tick_index=tick.tick_index,
            pending_score_tick_id=None,
        )

    def verify_checkpoints(self, run_id: str) -> dict[str, object]:
        self._require_run(run_id)
        if self.checkpoint_store is None:
            raise SimulationRepositoryError("checkpoint store is not configured")
        valid: list[str] = []
        invalid: list[dict[str, str]] = []
        committed_names: set[str] = set()
        for tick in sorted(
            (
                item
                for item in self.ticks.values()
                if item.run_id == run_id and item.checkpoint_object_name
            ),
            key=lambda item: item.tick_index,
        ):
            try:
                metadata = self._checkpoint_metadata(tick)
                assert metadata is not None
                committed_names.add(metadata.object_name)
                data = self.checkpoint_store.get(metadata)
                decode_checkpoint(
                    data,
                    expected_payload_sha256=metadata.payload_sha256,
                    expected_state_checksum=metadata.state_checksum,
                )
                valid.append(tick.tick_id)
            except (CheckpointError, SimulationRepositoryError) as exc:
                invalid.append(
                    {"tick_id": tick.tick_id, "error": type(exc).__name__}
                )
        names = set(self.checkpoint_store.list_names(f"runs/{run_id}/"))
        return {
            "run_id": run_id,
            "valid_tick_ids": valid,
            "invalid": invalid,
            "orphan_object_names": sorted(names - committed_names),
            "deletion_performed": False,
        }

    def _require_run(self, run_id: str) -> SimulationRun:
        if run_id not in self.runs:
            raise SimulationRepositoryError("unknown simulation run")
        return self.runs[run_id]

    def _require_run_for_scenario(self, scenario_id: str) -> SimulationRun:
        run = self.status(scenario_id)
        if run is None:
            raise SimulationRepositoryError("scenario has no simulation run")
        return run

    def _require_tick(self, run_id: str, tick_id: str) -> PersistedTick:
        tick = self.ticks.get(tick_id)
        if tick is None or tick.run_id != run_id:
            raise SimulationRepositoryError("tick does not belong to this run")
        return tick

    def _replace_run(self, run: SimulationRun, **changes: object) -> SimulationRun:
        updated = replace(run, **changes)
        self.runs[run.run_id] = updated
        return updated


class BigQuerySimulationRepository(InMemorySimulationRepository):
    """BigQuery adapter with an in-memory semantic mirror for deterministic tests.

    Production callers pass an authenticated BigQuery client.  Unit tests pass a
    recording fake and verify the fenced script/byte cap without provider writes.
    """

    def __init__(
        self,
        client: Any,
        *,
        dataset: str,
        staging_dataset: str | None = None,
        now: Callable[[], datetime] = _utc_now,
        lease_seconds: int = 360,
        checkpoint_store: CheckpointStore | None = None,
        state_mode: str = "oracle",
        staging_workers: int = 1,
    ):
        super().__init__(
            now=now,
            lease_seconds=lease_seconds,
            checkpoint_store=checkpoint_store,
            state_mode=state_mode,
        )
        self.client = client
        self.dataset = dataset
        self.staging_dataset = staging_dataset or dataset
        self._controls_loaded = False
        self._rejected_control_receipts: tuple[dict[str, object], ...] = ()
        if not 1 <= staging_workers <= 4:
            raise ValueError("staging_workers must be in 1..4")
        self.staging_workers = staging_workers
        self._schema_cache: dict[str, object] = {}

    def freeze_tick_inputs(
        self, run: SimulationRun, tick: PersistedTick
    ) -> TickInputManifest:
        if tick.input_manifest_json:
            return super().freeze_tick_inputs(run, tick)
        frozen_at = self.now()
        with component_span("controls_load") as controls_span:
            controls = self._controls_for_run(
                run, tick, authorization_time=frozen_at
            )
            controls_span.set(row_count=len(controls))
        manifest = _new_manifest(controls, frozen_at)
        self._query(
            f"""
UPDATE `{self.dataset}.simulation_ticks`
SET input_manifest_json = IFNULL(input_manifest_json, PARSE_JSON(@manifest_json)),
    input_manifest_checksum = IFNULL(input_manifest_checksum, @manifest_checksum),
    input_frozen_at = IFNULL(input_frozen_at, @input_frozen_at),
    input_checksum = IFNULL(input_checksum, @manifest_checksum),
    state_mode = IFNULL(state_mode, @state_mode)
WHERE simulation_run_id = @run_id AND tick_id = @tick_id
  AND status = 'LEASED' AND lease_owner = @lease_owner
  AND lease_expires_at > CURRENT_TIMESTAMP()
  AND (
    input_manifest_checksum IS NULL
    OR input_manifest_checksum = @manifest_checksum
  );
ASSERT @@row_count = 1;
""",
            {
                "run_id": run.run_id,
                "tick_id": tick.tick_id,
                "lease_owner": tick.lease_owner,
                "manifest_json": manifest.json,
                "manifest_checksum": manifest.checksum,
                "input_frozen_at": manifest.frozen_at,
                "state_mode": self.state_mode,
            },
        )
        self.ticks[tick.tick_id] = replace(
            tick,
            input_manifest_json=manifest.json,
            input_manifest_checksum=manifest.checksum,
            input_frozen_at=manifest.frozen_at,
            input_checksum=manifest.checksum,
            state_mode=self.state_mode,
        )
        self.manifests[tick.tick_id] = manifest
        return manifest

    def start(self, *, scenario_id: str, scenario_version: str, seed: int) -> SimulationRun:
        run = super().start(
            scenario_id=scenario_id, scenario_version=scenario_version, seed=seed
        )
        self._query(
            f"""
BEGIN TRANSACTION;
ASSERT (SELECT COUNT(*) FROM `{self.dataset}.simulation_runs`
  WHERE scenario_id = @scenario_id AND status IN ('RUNNING', 'PAUSED')) = 0;
MERGE `{self.dataset}.simulation_scenario_locks` target
USING (SELECT @scenario_id scenario_id) source ON target.scenario_id = source.scenario_id
WHEN MATCHED THEN UPDATE SET active_simulation_run_id = @run_id,
  generation = target.generation + 1, updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (scenario_id, active_simulation_run_id, generation, updated_at)
  VALUES (@scenario_id, @run_id, 1, CURRENT_TIMESTAMP());
INSERT INTO `{self.dataset}.simulation_runs`
  (simulation_run_id, scenario_id, scenario_version, seed, status,
   simulation_start_at, simulation_end_at, next_simulation_at, tick_minutes,
   speed_multiplier, config_json, created_at, updated_at, is_simulated)
VALUES (@run_id, @scenario_id, @scenario_version, @seed, 'RUNNING', @start_time,
  TIMESTAMP_ADD(@start_time, INTERVAL 24 HOUR), @start_time, 15, 1.0,
  JSON '{{}}', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), TRUE);
INSERT INTO `{self.dataset}.simulation_ticks`
  (simulation_run_id, scenario_id, tick_id, tick_index, simulation_time,
   snapshot_id, status, generator_version, is_simulated)
SELECT @run_id, @scenario_id,
  SUBSTR(LOWER(TO_HEX(SHA256(CONCAT('simulation-tick:', @run_id, ':', CAST(index AS STRING))))), 1, 32),
  index, TIMESTAMP_ADD(@start_time, INTERVAL 15 * index MINUTE),
  SUBSTR(LOWER(TO_HEX(SHA256(CONCAT('simulation-snapshot:', @run_id, ':', CAST(index AS STRING))))), 1, 32),
  'PENDING', @generator_version, TRUE
FROM UNNEST(GENERATE_ARRAY(0, 95)) index;
COMMIT TRANSACTION;
""",
            {
                "run_id": run.run_id,
                "scenario_id": scenario_id,
                "scenario_version": scenario_version,
                "seed": seed,
                "start_time": run.start_time,
                "generator_version": "stateful-replay-v1",
            },
        )
        return run

    def status(self, scenario_id: str) -> SimulationRun | None:
        cached = super().status(scenario_id)
        if cached is not None:
            return cached
        rows: list[Any] = self._query(
            f"""WITH selected_run AS (
                  SELECT simulation_run_id, scenario_id, scenario_version,
                         seed, status, simulation_start_at,
                         last_published_tick_index,
                         last_completed_tick_index, pending_score_tick_id,
                         risk_model_version, forecast_context_version,
                         forecast_context_seeded_at,
                         forecast_context_point_count
                  FROM `{self.dataset}.simulation_runs`
                  WHERE scenario_id = @scenario_id
                  ORDER BY created_at DESC LIMIT 1
                )
                SELECT simulation_run.*,
                       ARRAY_AGG(
                         STRUCT(
                           tick.simulation_run_id, tick.scenario_id,
                           tick.tick_id, tick.tick_index,
                           tick.simulation_time, tick.snapshot_id, tick.status,
                           tick.lease_owner, tick.lease_expires_at,
                           tick.input_checksum, tick.output_checksum,
                           tick.error_code,
                           IF(
                             tick.input_manifest_json IS NULL,
                             NULL,
                             TO_JSON_STRING(tick.input_manifest_json)
                           ) AS input_manifest_json,
                           tick.input_manifest_checksum,
                           tick.input_frozen_at,
                           tick.checkpoint_object_name,
                           tick.checkpoint_format_version,
                           tick.checkpoint_generation,
                           tick.checkpoint_compressed_size,
                           tick.checkpoint_expanded_size,
                           tick.checkpoint_payload_sha256,
                           tick.checkpoint_state_checksum,
                           tick.state_mode, tick.execution_mode,
                           IF(
                             tick.execution_reason_codes_json IS NULL,
                             NULL,
                             TO_JSON_STRING(
                               tick.execution_reason_codes_json
                             )
                           ) AS execution_reason_codes_json,
                           tick.low_risk_streak, tick.recovery_streak,
                           tick.scoring_outcome,
                           tick.forecast_source_tick_id,
                           tick.forecast_source_snapshot_id,
                           tick.forecast_source_prediction_run_id,
                           tick.forecast_generated_at
                         )
                         ORDER BY tick.tick_index
                       ) AS persisted_ticks
                FROM selected_run AS simulation_run
                JOIN `{self.dataset}.simulation_ticks` AS tick
                  USING (simulation_run_id)
                GROUP BY
                  simulation_run.simulation_run_id,
                  simulation_run.scenario_id,
                  simulation_run.scenario_version, simulation_run.seed,
                  simulation_run.status, simulation_run.simulation_start_at,
                  simulation_run.last_published_tick_index,
                  simulation_run.last_completed_tick_index,
                  simulation_run.pending_score_tick_id,
                  simulation_run.risk_model_version,
                  simulation_run.forecast_context_version,
                  simulation_run.forecast_context_seeded_at,
                  simulation_run.forecast_context_point_count""",
            {"scenario_id": scenario_id},
        )
        if not rows:
            return None
        row = dict(rows[0])
        persisted_ticks = row.pop("persisted_ticks", None)
        run = SimulationRun(
            row["simulation_run_id"], row["scenario_id"], row["scenario_version"],
            int(row["seed"]), row["status"], row["simulation_start_at"],
            row.get("last_published_tick_index"), row.get("last_completed_tick_index"),
            row.get("pending_score_tick_id"),
            row.get("risk_model_version"), row.get("forecast_context_version"),
            row.get("forecast_context_seeded_at"),
            row.get("forecast_context_point_count"),
        )
        self.runs[run.run_id] = run
        self.scenario_runs[scenario_id] = run.run_id
        self._load_ticks(run, rows=persisted_ticks)
        return run

    def pause(self, scenario_id: str) -> SimulationRun:
        run = self._require_run_for_scenario(scenario_id)
        self._query(
            f"""UPDATE `{self.dataset}.simulation_runs` SET status = 'PAUSED',
                   updated_at = CURRENT_TIMESTAMP()
                WHERE simulation_run_id = @run_id AND status = 'RUNNING'""",
            {"run_id": run.run_id},
        )
        return super().pause(scenario_id)

    def resume(self, scenario_id: str) -> SimulationRun:
        run = self._require_run_for_scenario(scenario_id)
        self._query(
            f"""UPDATE `{self.dataset}.simulation_runs` SET status = 'RUNNING',
                   updated_at = CURRENT_TIMESTAMP()
                WHERE simulation_run_id = @run_id AND status = 'PAUSED'""",
            {"run_id": run.run_id},
        )
        return super().resume(scenario_id)

    def acquire_tick_lease(self, run_id: str, tick_id: str, owner: str) -> TickLease:
        lease = super().acquire_tick_lease(run_id, tick_id, owner)
        if lease.fencing_token == "already-published":
            return lease
        self._query(
            f"""
UPDATE `{self.dataset}.simulation_ticks`
SET status = 'LEASED', lease_owner = @lease_owner,
    lease_expires_at = @lease_expires_at, started_at = CURRENT_TIMESTAMP()
WHERE simulation_run_id = @run_id AND tick_id = @tick_id
  AND (status = 'PENDING' OR lease_expires_at <= CURRENT_TIMESTAMP());
ASSERT @@row_count = 1;
""",
            {
                "run_id": run_id,
                "tick_id": tick_id,
                "lease_owner": lease.fencing_token,
                "lease_expires_at": lease.expires_at,
            },
        )
        return lease

    def _controls_for_run(
        self,
        run: SimulationRun,
        tick: PersistedTick | None = None,
        *,
        authorization_time: datetime | None = None,
    ) -> tuple[PauseControl, ...]:
        if self._controls_loaded:
            return super()._controls_for_run(
                run, tick, authorization_time=authorization_time
            )
        from .control import (
            ControlValidationError,
            canonical_proposal_checksum,
            validate_control_payload,
        )

        presence = self._query(
            f"""
SELECT EXISTS (
  SELECT 1
  FROM `{self.dataset}.simulation_control_events`
  WHERE simulation_run_id = @run_id
) AS has_controls
""",
            {"run_id": run.run_id},
        )
        if presence and not bool(dict(presence[0])["has_controls"]):
            self._controls_loaded = True
            self._rejected_control_receipts = ()
            return ()

        rows = self._query(
            f"""
SELECT control.*, proposal.proposal_json, tick.tick_index,
       tick.simulation_time AS source_simulation_time,
       EXISTS (
         SELECT 1
         FROM `{self.dataset}.simulation_control_consumptions` applied
         WHERE applied.control_event_id = control.control_event_id
           AND applied.simulation_run_id = control.simulation_run_id
           AND applied.outcome = 'APPLIED'
       ) AS was_applied
FROM `{self.dataset}.simulation_control_events` control
JOIN `{self.dataset}.intervention_proposals` proposal
  ON proposal.proposal_id = control.proposal_id
 AND proposal.simulation_run_id = control.simulation_run_id
 AND proposal.source_tick_id = control.source_tick_id
 AND proposal.source_snapshot_id = control.source_snapshot_id
JOIN `{self.dataset}.simulation_ticks` tick
  ON tick.simulation_run_id = control.simulation_run_id
 AND tick.tick_id = control.source_tick_id
WHERE control.simulation_run_id = @run_id
  AND (
    control.status = 'CONSUMED'
    OR EXISTS (
      SELECT 1
      FROM `{self.dataset}.simulation_control_consumptions` applied
      WHERE applied.control_event_id = control.control_event_id
        AND applied.simulation_run_id = control.simulation_run_id
        AND applied.outcome = 'APPLIED'
    )
    OR (
      control.status IN ('AUTHORIZED', 'QUEUED')
      AND NOT EXISTS (
        SELECT 1
        FROM `{self.dataset}.simulation_control_consumptions` receipt
        WHERE receipt.control_event_id = control.control_event_id
          AND receipt.simulation_run_id = control.simulation_run_id
          AND receipt.outcome IN ('APPLIED', 'EXPIRED', 'REJECTED')
      )
    )
  )
ORDER BY control.created_at, control.control_event_id
""",
            {"run_id": run.run_id},
        )
        rejected: list[dict[str, object]] = []
        frozen_at = authorization_time or self.now()
        for raw in rows:
            row = dict(raw)
            if (
                row["status"] in {"AUTHORIZED", "QUEUED"}
                and not row.get("was_applied")
                and (
                row["authorization_expires_at"] <= frozen_at
                or (
                    tick is not None
                    and tick.simulation_time > row["valid_until_simulation_at"]
                )
                )
            ):
                outcome = (
                    "EXPIRED"
                    if row["authorization_expires_at"] <= frozen_at
                    else "REJECTED"
                )
                rejected.append({
                    "consumption_id": canonical_checksum(
                        (row["control_event_id"], run.run_id, outcome)
                    )[:32],
                    "control_event_id": row["control_event_id"],
                    "scenario_id": run.scenario_id,
                    "simulation_run_id": run.run_id,
                    "consumed_by_tick_id": tick.tick_id if tick else None,
                    "outcome": outcome,
                    "recorded_at": frozen_at,
                    "rejection_reason": (
                        "AUTHORIZATION_EXPIRED"
                        if outcome == "EXPIRED"
                        else "SIMULATION_WINDOW_EXPIRED"
                    ),
                    "generator_version": "stateful-replay-v1",
                    "is_simulated": True,
                })
                continue
            payload = row["proposal_json"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            validation_now = (
                row["created_at"]
                if row.get("was_applied") or row["status"] == "CONSUMED"
                else frozen_at
            )
            try:
                queued = validate_control_payload(
                    payload,
                    scenario_id=row["scenario_id"],
                    run_id=run.run_id,
                    source_tick_id=row["source_tick_id"],
                    source_snapshot_id=row["source_snapshot_id"],
                    source_tick_index=int(row["tick_index"]),
                    now=validation_now,
                    simulation_time=row["source_simulation_time"],
                    max_selected_drivers=int(row["max_selected_drivers"]),
                )
            except ControlValidationError as exc:
                raise SimulationRepositoryError(
                    f"trusted control validation failed: {exc}"
                ) from exc
            if queued.control_event_id != row["control_event_id"]:
                raise SimulationRepositoryError("control identity does not match payload")
            if canonical_proposal_checksum(payload) != row["proposal_payload_checksum"]:
                raise SimulationRepositoryError("control proposal payload changed")
            self.queue_controls(queued.pause_controls)
        self._controls_loaded = True
        self._rejected_control_receipts = tuple(rejected)
        return super()._controls_for_run(
            run, tick, authorization_time=frozen_at
        )

    def finalize_score(self, run_id: str, tick_id: str, *, succeeded: bool) -> SimulationRun:
        self._require_run(run_id)
        tick = self._require_tick(run_id, tick_id)
        if tick.status != "SUCCEEDED":
            self._query(
                f"""
BEGIN TRANSACTION;
UPDATE `{self.dataset}.simulation_ticks`
SET status = IF(@succeeded, 'SUCCEEDED', 'SCORE_FAILED'),
    finished_at = CURRENT_TIMESTAMP()
WHERE simulation_run_id = @run_id AND tick_id = @tick_id
  AND status IN ('SNAPSHOT_READY', 'SCORED', 'SCORE_FAILED');
UPDATE `{self.dataset}.simulation_runs`
SET last_completed_tick_index = IF(@succeeded, @tick_index, last_completed_tick_index),
    pending_score_tick_id = IF(@succeeded, NULL, pending_score_tick_id),
    next_simulation_at = IF(
      @succeeded,
      TIMESTAMP_ADD(simulation_start_at, INTERVAL 15 * (@tick_index + 1) MINUTE),
      next_simulation_at
    ),
    status = IF(@succeeded AND @tick_index = 95, 'COMPLETED', status),
    updated_at = CURRENT_TIMESTAMP()
WHERE simulation_run_id = @run_id AND pending_score_tick_id = @tick_id;
COMMIT TRANSACTION;
""",
                {"run_id": run_id, "tick_id": tick_id, "tick_index": tick.tick_index, "succeeded": succeeded},
            )
        return super().finalize_score(run_id, tick_id, succeeded=succeeded)

    def record_scoring_lineage(
        self, run_id: str, tick_id: str, prediction_run_id: str
    ) -> PersistedTick:
        tick = super().record_scoring_lineage(
            run_id, tick_id, prediction_run_id
        )
        if tick.forecast_source_tick_id == tick.tick_id:
            self._query(
                f"""
UPDATE `{self.dataset}.simulation_ticks`
SET forecast_source_prediction_run_id = @prediction_run_id,
    forecast_generated_at = CURRENT_TIMESTAMP()
WHERE simulation_run_id = @run_id AND tick_id = @tick_id
  AND forecast_source_tick_id = tick_id
  AND (
    forecast_source_prediction_run_id IS NULL
    OR forecast_source_prediction_run_id = @prediction_run_id
  )
""",
                {
                    "run_id": run_id,
                    "tick_id": tick_id,
                    "prediction_run_id": prediction_run_id,
                },
            )
        return tick

    def publish_tick(self, run_id: str, tick_id: str, owner: str) -> Publication:
        persisted_status = self._require_tick(run_id, tick_id).status
        publication = super().publish_tick(run_id, tick_id, owner)
        if persisted_status in {
            "SNAPSHOT_READY", "SCORED", "SCORE_FAILED", "SUCCEEDED"
        }:
            return publication
        if self._rejected_control_receipts:
            publication = replace(
                publication,
                consumption_rows=(
                    publication.consumption_rows
                    + self._rejected_control_receipts
                ),
            )
            self.published[tick_id] = publication
        tick = publication.tick
        staged = self._stage_many(
            tick.tick_id,
            {
                "driver": publication.driver_rows,
                "zone": publication.zone_rows,
                "order": publication.order_rows,
                "weather": publication.weather_rows,
                "operation": publication.operation_rows,
                "demand": publication.demand_rows,
                "history": publication.driver_history_rows,
                "intervention": publication.intervention_rows,
                "consumption": publication.consumption_rows,
            },
        )
        driver_stage = staged["driver"]
        zone_stage = staged["zone"]
        order_stage = staged["order"]
        weather_stage = staged["weather"]
        operation_stage = staged["operation"]
        demand_stage = staged["demand"]
        history_stage = staged["history"]
        intervention_stage = staged["intervention"]
        consumption_stage = staged["consumption"]
        intervention_sql = (
            f"""
MERGE `{self.dataset}.driver_intervention_events` target
USING `{intervention_stage}` source ON target.event_id = source.event_id
WHEN NOT MATCHED THEN INSERT ROW;
"""
            if publication.intervention_rows
            else ""
        )
        consumption_sql = (
            f"""
ASSERT (
  SELECT COUNT(*) FROM `{self.dataset}.simulation_control_events` control
  JOIN `{consumption_stage}` source
    ON source.control_event_id = control.control_event_id
  WHERE control.simulation_run_id = @run_id
    AND control.status IN ('AUTHORIZED', 'QUEUED')
    AND (
      (source.outcome = 'APPLIED'
       AND control.authorization_expires_at > @input_frozen_at
       AND @simulation_time BETWEEN control.valid_from_simulation_at
                                AND control.valid_until_simulation_at)
      OR (source.outcome = 'EXPIRED'
          AND control.authorization_expires_at <= @input_frozen_at)
      OR (source.outcome = 'REJECTED'
          AND @simulation_time > control.valid_until_simulation_at)
    )
) = (SELECT COUNT(*) FROM `{consumption_stage}`);
MERGE `{self.dataset}.simulation_control_consumptions` target
USING `{consumption_stage}` source
ON target.consumption_id = source.consumption_id
WHEN NOT MATCHED THEN INSERT ROW;
"""
            if publication.consumption_rows
            else ""
        )
        script = f"""
BEGIN TRANSACTION;
-- Fence publication with the exact current token and unexpired lease.
ASSERT (SELECT COUNT(*) FROM `{self.dataset}.simulation_ticks`
  WHERE simulation_run_id = @run_id AND tick_id = @tick_id
    AND status = 'LEASED' AND lease_owner = @lease_owner
    AND lease_expires_at > CURRENT_TIMESTAMP()) = 1;
-- Staging rows are loaded before this query and expire automatically.
MERGE `{self.dataset}.driver_simulation_state` target
USING `{driver_stage}` source
ON target.simulation_run_id = source.simulation_run_id
 AND target.driver_id_hash = source.driver_id_hash
WHEN MATCHED THEN UPDATE SET last_tick_id = source.last_tick_id, updated_at = source.updated_at
  , event_time = source.event_time, zone_id = source.zone_id,
  latitude = source.latitude, longitude = source.longitude,
  status = source.status, current_order_id = source.current_order_id,
  current_intervention_id = source.current_intervention_id,
  online_minutes_24h = source.online_minutes_24h,
  trips_60m = source.trips_60m, distance_km_60m = source.distance_km_60m,
  workload_intensity = source.workload_intensity,
  continuous_exposure_minutes = source.continuous_exposure_minutes,
  heat_dose_120m = source.heat_dose_120m,
  rest_minutes_120m = source.rest_minutes_120m,
  hydration_gap_minutes = source.hydration_gap_minutes,
  route_heat_load = source.route_heat_load,
  acclimatization_class = source.acclimatization_class,
  earnings_60m_vnd = source.earnings_60m_vnd,
  platform_contribution_60m_vnd = source.platform_contribution_60m_vnd
WHEN NOT MATCHED THEN INSERT ROW;
MERGE `{self.dataset}.zone_snapshots_current` target
USING `{zone_stage}` source
ON target.scenario_id = source.scenario_id AND target.zone_id = source.zone_id
WHEN MATCHED THEN UPDATE SET snapshot_id = source.snapshot_id, tick_id = source.tick_id,
  simulation_run_id = source.simulation_run_id, observed_at = source.observed_at,
  temperature_c = source.temperature_c,
  humidity_percent = source.humidity_percent,
  heat_index_c = source.heat_index_c,
  active_drivers = source.active_drivers,
  fresh_drivers = source.fresh_drivers,
  exposed_2h = source.exposed_2h,
  exposed_4h = source.exposed_4h,
  forecast_requests_30m = source.forecast_requests_30m,
  online_drivers = source.online_drivers,
  idle_drivers = source.idle_drivers,
  to_pickup_drivers = source.to_pickup_drivers,
  on_trip_drivers = source.on_trip_drivers,
  to_coolstop_drivers = source.to_coolstop_drivers,
  paused_drivers = source.paused_drivers,
  exposed_2_to_4h = source.exposed_2_to_4h,
  requests_15m = source.requests_15m,
  matched_15m = source.matched_15m,
  completed_15m = source.completed_15m,
  cancelled_15m = source.cancelled_15m,
  unfulfilled_15m = source.unfulfilled_15m,
  fulfillment_rate = source.fulfillment_rate,
  refreshed_at = source.refreshed_at
WHEN NOT MATCHED THEN INSERT ROW;
MERGE `{self.dataset}.order_events` target
USING `{order_stage}` source ON target.event_id = source.event_id
WHEN NOT MATCHED THEN INSERT ROW;
MERGE `{self.dataset}.weather_observations` target
USING `{weather_stage}` source
ON target.simulation_run_id = source.simulation_run_id AND target.tick_id = source.tick_id
 AND target.zone_id = source.zone_id
WHEN NOT MATCHED THEN INSERT ROW;
MERGE `{self.dataset}.zone_operations` target
USING `{operation_stage}` source
ON target.simulation_run_id = source.simulation_run_id AND target.tick_id = source.tick_id
 AND target.zone_id = source.zone_id
WHEN NOT MATCHED THEN INSERT ROW;
MERGE `{self.dataset}.demand_history` target
USING `{demand_stage}` source
ON target.simulation_run_id = source.simulation_run_id AND target.tick_id = source.tick_id
 AND target.zone_id = source.zone_id
WHEN NOT MATCHED THEN INSERT ROW;
MERGE `{self.dataset}.driver_state_history` target
USING `{history_stage}` source ON target.state_id = source.state_id
  AND target.event_time = @simulation_time
WHEN NOT MATCHED THEN INSERT ROW;
{intervention_sql}
{consumption_sql}
UPDATE `{self.dataset}.simulation_runs`
SET last_published_tick_index = @tick_index, pending_score_tick_id = @tick_id,
    updated_at = CURRENT_TIMESTAMP()
WHERE simulation_run_id = @run_id;
UPDATE `{self.dataset}.simulation_ticks`
SET status = 'SNAPSHOT_READY', output_checksum = @output_checksum,
    error_code = @model_input_error,
    input_manifest_json = PARSE_JSON(@input_manifest_json),
    input_manifest_checksum = @input_manifest_checksum,
    input_frozen_at = @input_frozen_at,
    input_checksum = @input_manifest_checksum,
    checkpoint_object_name = @checkpoint_object_name,
    checkpoint_format_version = @checkpoint_format_version,
    checkpoint_generation = @checkpoint_generation,
    checkpoint_compressed_size = @checkpoint_compressed_size,
    checkpoint_expanded_size = @checkpoint_expanded_size,
    checkpoint_payload_sha256 = @checkpoint_payload_sha256,
    checkpoint_state_checksum = @checkpoint_state_checksum,
    state_mode = @state_mode,
    execution_mode = @execution_mode,
    execution_reason_codes_json = PARSE_JSON(@execution_reason_codes_json),
    low_risk_streak = @low_risk_streak,
    recovery_streak = @recovery_streak,
    forecast_source_tick_id = @forecast_source_tick_id,
    forecast_source_snapshot_id = @forecast_source_snapshot_id,
    forecast_source_prediction_run_id = @forecast_source_prediction_run_id
WHERE simulation_run_id = @run_id AND tick_id = @tick_id;
COMMIT TRANSACTION;
"""
        self._query(
            script,
            {
                "run_id": run_id,
                "tick_id": tick_id,
                "tick_index": tick.tick_index,
                "simulation_time": tick.simulation_time,
                "lease_owner": tick.lease_owner,
                "output_checksum": tick.output_checksum,
                "input_manifest_json": tick.input_manifest_json,
                "input_manifest_checksum": tick.input_manifest_checksum,
                "input_frozen_at": tick.input_frozen_at,
                "checkpoint_object_name": tick.checkpoint_object_name,
                "checkpoint_format_version": tick.checkpoint_format_version,
                "checkpoint_generation": tick.checkpoint_generation,
                "checkpoint_compressed_size": tick.checkpoint_compressed_size,
                "checkpoint_expanded_size": tick.checkpoint_expanded_size,
                "checkpoint_payload_sha256": tick.checkpoint_payload_sha256,
                "checkpoint_state_checksum": tick.checkpoint_state_checksum,
                "state_mode": tick.state_mode,
                "execution_mode": tick.execution_mode,
                "execution_reason_codes_json": tick.execution_reason_codes_json,
                "low_risk_streak": tick.low_risk_streak,
                "recovery_streak": tick.recovery_streak,
                "forecast_source_tick_id": tick.forecast_source_tick_id,
                "forecast_source_snapshot_id": tick.forecast_source_snapshot_id,
                "forecast_source_prediction_run_id": (
                    tick.forecast_source_prediction_run_id
                ),
                "model_input_error": (
                    "MODEL_INPUT_OOD" if publication.result.model_input_ood else None
                ),
            },
            component="publication_commit",
        )
        return publication

    def _stage_many(
        self,
        tick_id: str,
        groups: dict[str, tuple[dict[str, object], ...]],
    ) -> dict[str, str]:
        if self.staging_workers == 1:
            return {
                kind: self._stage_rows(kind, tick_id, rows)
                for kind, rows in groups.items()
            }
        with ThreadPoolExecutor(
            max_workers=self.staging_workers,
            thread_name_prefix="heatsafe-stage",
        ) as pool:
            futures = {
                kind: pool.submit(
                    copy_context().run,
                    self._stage_rows,
                    kind,
                    tick_id,
                    rows,
                )
                for kind, rows in groups.items()
            }
            # Reading every future propagates every failure before publication.
            return {kind: future.result() for kind, future in futures.items()}

    def _stage_rows(
        self, kind: str, tick_id: str, rows: tuple[dict[str, object], ...]
    ) -> str:
        """Load a per-tick staging table with one-hour expiry before publication."""
        target_tables = {
            "driver": "driver_simulation_state",
            "zone": "zone_snapshots_current",
            "order": "order_events",
            "weather": "weather_observations",
            "operation": "zone_operations",
            "demand": "demand_history",
            "history": "driver_state_history",
            "intervention": "driver_intervention_events",
            "consumption": "simulation_control_consumptions",
        }
        target_table = target_tables[kind]
        table_id = f"{self.staging_dataset}.__simulation_stage_{kind}_{tick_id}"
        if not rows:
            with component_span(
                f"staging_load_{kind}", row_count=0
            ) as empty_span:
                empty_span.mark("NO_OP")
            return table_id
        from google.cloud import bigquery

        target_schema = self._schema_cache.get(target_table)
        if target_schema is None:
            with component_span("staging_schema_lookup"):
                target_schema = self.client.get_table(
                    f"{self.dataset}.{target_table}"
                ).schema
            self._schema_cache[target_table] = target_schema
        config = bigquery.LoadJobConfig(
            schema=target_schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            labels={"app": "heatsafe", "component": "simulation_staging"},
        )
        json_rows = [
            {
                name: value.isoformat() if isinstance(value, datetime) else value
                for name, value in row.items()
            }
            for row in rows
        ]
        with component_span(
            f"staging_load_{kind}", row_count=len(json_rows)
        ) as load_span:
            job = self.client.load_table_from_json(
                json_rows, table_id, job_config=config
            )
            load_span.attach_job(job)
            job.result()
        return table_id

    def _load_ticks(
        self,
        run: SimulationRun,
        *,
        rows: list[Any] | tuple[Any, ...] | None = None,
    ) -> None:
        if rows is None:
            rows = self._query(
                f"""SELECT simulation_run_id, scenario_id, tick_id, tick_index,
                       simulation_time, snapshot_id, status, lease_owner,
                       lease_expires_at, input_checksum, output_checksum,
                       error_code,
                       IF(
                         input_manifest_json IS NULL,
                         NULL,
                         TO_JSON_STRING(input_manifest_json)
                       ) AS input_manifest_json,
                       input_manifest_checksum, input_frozen_at,
                       checkpoint_object_name, checkpoint_format_version,
                       checkpoint_generation, checkpoint_compressed_size,
                       checkpoint_expanded_size, checkpoint_payload_sha256,
                       checkpoint_state_checksum, state_mode, execution_mode,
                       IF(
                         execution_reason_codes_json IS NULL,
                         NULL,
                         TO_JSON_STRING(execution_reason_codes_json)
                       ) AS execution_reason_codes_json,
                       low_risk_streak, recovery_streak,
                       scoring_outcome, forecast_source_tick_id,
                       forecast_source_snapshot_id,
                       forecast_source_prediction_run_id, forecast_generated_at
                FROM `{self.dataset}.simulation_ticks`
                WHERE simulation_run_id = @run_id ORDER BY tick_index""",
                {"run_id": run.run_id},
            )
        for raw in rows:
            row = dict(raw)
            row["run_id"] = row.pop("simulation_run_id")
            tick = PersistedTick(**row)
            self.ticks[tick.tick_id] = tick

    def _query(
        self,
        sql: str,
        params: dict[str, Any],
        *,
        component: str | None = None,
    ) -> list[Any]:
        from google.cloud import bigquery

        def parameter(name: str, value: Any):
            int_names = {
                "checkpoint_generation",
                "checkpoint_compressed_size",
                "checkpoint_expanded_size",
                "low_risk_streak",
                "recovery_streak",
            }
            timestamp_names = {"input_frozen_at"}
            kind = (
                "BOOL"
                if isinstance(value, bool)
                else "INT64"
                if isinstance(value, int) or name in int_names
                else "TIMESTAMP"
                if isinstance(value, datetime) or name in timestamp_names
                else "STRING"
            )
            return bigquery.ScalarQueryParameter(name, kind, value)

        configuration = bigquery.QueryJobConfig(
            query_parameters=[parameter(name, value) for name, value in params.items()],
            maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
            labels={"app": "heatsafe", "component": "simulation_publisher"},
        )
        if component is None:
            result = self.client.query(sql, job_config=configuration).result()
        else:
            with component_span(component) as query_span:
                job = self.client.query(sql, job_config=configuration)
                query_span.attach_job(job)
                result = job.result()
        if result is None:
            return []
        try:
            return list(result)
        except TypeError:
            return []
