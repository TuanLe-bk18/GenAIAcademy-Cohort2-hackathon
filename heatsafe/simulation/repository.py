"""Deterministic Phase 3 persistence boundary for the HeatSafe replay.

The in-memory implementation is the executable contract used by tests.  The
BigQuery adapter deliberately emits one fenced multi-statement query per tick;
the disposable-dataset probe remains the only proof of provider behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any, Callable, Protocol
from uuid import uuid4

from .engine import advance_tick, initialize_state, load_scenario, load_zone_priors
from .models import PauseControl, SimulationState, TickResult
from .randomness import canonical_checksum


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


class SimulationRepository(Protocol):
    def start(self, *, scenario_id: str, scenario_version: str, seed: int) -> SimulationRun: ...
    def status(self, scenario_id: str) -> SimulationRun | None: ...
    def pause(self, scenario_id: str) -> SimulationRun: ...
    def resume(self, scenario_id: str) -> SimulationRun: ...
    def acquire_tick_lease(self, run_id: str, tick_id: str, owner: str) -> TickLease: ...
    def publish_tick(self, run_id: str, tick_id: str, owner: str) -> Publication: ...
    def mark_scored(self, run_id: str, tick_id: str) -> PersistedTick: ...
    def finalize_score(self, run_id: str, tick_id: str, *, succeeded: bool) -> SimulationRun: ...


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
    for _ in range(tick_index):
        state = advance_tick(
            state, fixture=fixture, zones=zones, controls=controls
        ).state
    return state, advance_tick(
        state, fixture=fixture, zones=zones, controls=controls
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

    def queue_controls(self, controls: tuple[PauseControl, ...]) -> None:
        for control in controls:
            existing = self.controls.get(control.control_id)
            if existing is not None and existing != control:
                raise SimulationRepositoryError("control identity payload changed")
            self.controls[control.control_id] = control

    def _controls_for_run(
        self, run: SimulationRun, tick: PersistedTick | None = None
    ) -> tuple[PauseControl, ...]:
        return tuple(
            self.controls[key]
            for key in sorted(self.controls)
        )

    def start(self, *, scenario_id: str, scenario_version: str, seed: int) -> SimulationRun:
        active = self.status(scenario_id)
        if active and active.status in {"RUNNING", "PAUSED"}:
            raise RunConflict(f"scenario {scenario_id} already has an active run")
        run_id = uuid4().hex
        run = SimulationRun(run_id, scenario_id, scenario_version, seed, "RUNNING", self.now())
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
                _, result = replay_to_tick(
                    run, tick.tick_index, controls=self._controls_for_run(run, tick)
                )
                publication = publication_rows(
                    run, tick, result, controls=self._controls_for_run(run, tick)
                )
                self.published[tick_id] = publication
            return publication
        if tick.status != "LEASED" or not tick.lease_owner or tick.lease_expires_at is None:
            raise LeaseConflict("tick must have a current lease before publication")
        if tick.lease_expires_at <= self.now():
            raise LeaseConflict("tick lease expired before publication")
        if owner != tick.lease_owner:
            raise LeaseConflict("publication requires the exact fencing token")
        state, result = replay_to_tick(
            run, tick.tick_index, controls=self._controls_for_run(run, tick)
        )
        updated = replace(
            tick,
            status="SNAPSHOT_READY",
            input_checksum=canonical_checksum((run.run_id, tick.tick_index, state.minute_index)),
            output_checksum=result.checksum,
            error_code="MODEL_INPUT_OOD" if result.model_input_ood else None,
        )
        publication = publication_rows(
            run, updated, result, controls=self._controls_for_run(run, updated)
        )
        self.ticks[tick_id] = updated
        self.published[tick_id] = publication
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
        scored = replace(tick, status="SCORED")
        self.ticks[tick_id] = scored
        return scored

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
    ):
        super().__init__(now=now, lease_seconds=lease_seconds)
        self.client = client
        self.dataset = dataset
        self.staging_dataset = staging_dataset or dataset
        self._controls_loaded = False
        self._rejected_control_receipts: tuple[dict[str, object], ...] = ()

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
            f"""SELECT simulation_run_id, scenario_id, scenario_version, seed, status,
                       simulation_start_at, last_published_tick_index,
                       last_completed_tick_index, pending_score_tick_id
                FROM `{self.dataset}.simulation_runs`
                WHERE scenario_id = @scenario_id
                ORDER BY created_at DESC LIMIT 1""",
            {"scenario_id": scenario_id},
        )
        if not rows:
            return None
        row = dict(rows[0])
        run = SimulationRun(
            row["simulation_run_id"], row["scenario_id"], row["scenario_version"],
            int(row["seed"]), row["status"], row["simulation_start_at"],
            row.get("last_published_tick_index"), row.get("last_completed_tick_index"),
            row.get("pending_score_tick_id"),
        )
        self.runs[run.run_id] = run
        self.scenario_runs[scenario_id] = run.run_id
        self._load_ticks(run)
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
ASSERT (SELECT COUNT(*) FROM `{self.dataset}.simulation_ticks`
  WHERE simulation_run_id = @run_id AND tick_id = @tick_id
    AND status = 'LEASED' AND lease_owner = @lease_owner
    AND lease_expires_at > CURRENT_TIMESTAMP()) = 1;
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
        self, run: SimulationRun, tick: PersistedTick | None = None
    ) -> tuple[PauseControl, ...]:
        if self._controls_loaded:
            return super()._controls_for_run(run, tick)
        from .control import (
            ControlValidationError,
            canonical_proposal_checksum,
            validate_control_payload,
        )

        rows = self._query(
            f"""
SELECT control.*, proposal.proposal_json, tick.tick_index,
       tick.simulation_time AS source_simulation_time
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
  AND control.status IN ('QUEUED', 'CONSUMED')
ORDER BY control.created_at, control.control_event_id
""",
            {"run_id": run.run_id},
        )
        rejected: list[dict[str, object]] = []
        for raw in rows:
            row = dict(raw)
            if row["status"] == "QUEUED" and (
                row["authorization_expires_at"] <= self.now()
                or (
                    tick is not None
                    and tick.simulation_time > row["valid_until_simulation_at"]
                )
            ):
                outcome = (
                    "EXPIRED"
                    if row["authorization_expires_at"] <= self.now()
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
                    "recorded_at": self.now(),
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
                if row["status"] == "CONSUMED"
                else self.now()
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
        return super()._controls_for_run(run, tick)

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
        driver_stage = self._stage_rows("driver", tick.tick_id, publication.driver_rows)
        zone_stage = self._stage_rows("zone", tick.tick_id, publication.zone_rows)
        order_stage = self._stage_rows("order", tick.tick_id, publication.order_rows)
        weather_stage = self._stage_rows("weather", tick.tick_id, publication.weather_rows)
        operation_stage = self._stage_rows("operation", tick.tick_id, publication.operation_rows)
        demand_stage = self._stage_rows("demand", tick.tick_id, publication.demand_rows)
        history_stage = self._stage_rows("history", tick.tick_id, publication.driver_history_rows)
        intervention_stage = self._stage_rows(
            "intervention", tick.tick_id, publication.intervention_rows
        )
        consumption_stage = self._stage_rows(
            "consumption", tick.tick_id, publication.consumption_rows
        )
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
    AND control.status = 'QUEUED'
    AND (
      (source.outcome = 'APPLIED'
       AND control.authorization_expires_at > CURRENT_TIMESTAMP()
       AND @simulation_time BETWEEN control.valid_from_simulation_at
                                AND control.valid_until_simulation_at)
      OR (source.outcome = 'EXPIRED'
          AND control.authorization_expires_at <= CURRENT_TIMESTAMP())
      OR (source.outcome = 'REJECTED'
          AND @simulation_time > control.valid_until_simulation_at)
    )
) = (SELECT COUNT(*) FROM `{consumption_stage}`);
MERGE `{self.dataset}.simulation_control_consumptions` target
USING `{consumption_stage}` source
ON target.consumption_id = source.consumption_id
WHEN NOT MATCHED THEN INSERT ROW;
UPDATE `{self.dataset}.simulation_control_events` control
SET status = IF(source.outcome = 'APPLIED', 'CONSUMED', source.outcome)
FROM `{consumption_stage}` source
WHERE control.control_event_id = source.control_event_id
  AND control.simulation_run_id = @run_id;
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
WHEN NOT MATCHED THEN INSERT ROW;
{intervention_sql}
{consumption_sql}
UPDATE `{self.dataset}.simulation_runs`
SET last_published_tick_index = @tick_index, pending_score_tick_id = @tick_id,
    updated_at = CURRENT_TIMESTAMP()
WHERE simulation_run_id = @run_id;
UPDATE `{self.dataset}.simulation_ticks`
SET status = 'SNAPSHOT_READY', output_checksum = @output_checksum,
    error_code = @model_input_error
WHERE simulation_run_id = @run_id AND tick_id = @tick_id;
COMMIT TRANSACTION;
"""
        self._query(script, {
            "run_id": run_id, "tick_id": tick_id, "tick_index": tick.tick_index,
            "simulation_time": tick.simulation_time,
            "lease_owner": tick.lease_owner,
            "output_checksum": tick.output_checksum,
            "model_input_error": (
                "MODEL_INPUT_OOD" if publication.result.model_input_ood else None
            ),
        })
        return publication

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
            return table_id
        from google.cloud import bigquery

        target_schema = self.client.get_table(f"{self.dataset}.{target_table}").schema
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
        self.client.load_table_from_json(json_rows, table_id, job_config=config).result()
        table = self.client.get_table(table_id)
        table.expires = self.now() + timedelta(hours=1)
        self.client.update_table(table, ["expires"])
        return table_id

    def _load_ticks(self, run: SimulationRun) -> None:
        rows: list[Any] = self._query(
            f"""SELECT simulation_run_id, scenario_id, tick_id, tick_index,
                       simulation_time, snapshot_id, status, lease_owner,
                       lease_expires_at, input_checksum, output_checksum,
                       error_code
                FROM `{self.dataset}.simulation_ticks`
                WHERE simulation_run_id = @run_id ORDER BY tick_index""",
            {"run_id": run.run_id},
        )
        for raw in rows:
            row = dict(raw)
            row["run_id"] = row.pop("simulation_run_id")
            tick = PersistedTick(**row)
            self.ticks[tick.tick_id] = tick

    def _query(self, sql: str, params: dict[str, Any]) -> list[Any]:
        from google.cloud import bigquery

        def parameter(name: str, value: Any):
            kind = "BOOL" if isinstance(value, bool) else "INT64" if isinstance(value, int) else "TIMESTAMP" if isinstance(value, datetime) else "STRING"
            return bigquery.ScalarQueryParameter(name, kind, value)

        configuration = bigquery.QueryJobConfig(
            query_parameters=[parameter(name, value) for name, value in params.items()],
            maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
            labels={"app": "heatsafe", "component": "simulation_publisher"},
        )
        result = self.client.query(sql, job_config=configuration).result()
        if result is None:
            return []
        try:
            return list(result)
        except TypeError:
            return []
