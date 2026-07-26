"""Accelerated, interactive production window built on the stateful engine.

The deployed Streamlit process uses this module to advance the same deterministic
simulation and SafePause lifecycle as the production-shaped pipeline.  Only the
wall-clock cadence is accelerated; each engine tick still represents fifteen
operational minutes.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Literal

from .ai_decision import recommend_ai_intervention
from .demand_profile import intraday_demand_factor
from .ingestion import calculate_heat_index
from .models import (
    DecisionConstraints,
    DriverActionPrediction,
    DriverDecision,
    RecommendationResult,
    SafePauseProposal,
    ZoneSnapshot,
)
from .repository import DemandForecast, ForecastPoint
from .services.decision_service import CityPlanRow, CityWidePlan, UnavailableZone
from .services.preventive_planning import (
    build_accelerated_forecast_input,
    build_predictive_city_plan,
    project_city_forecast,
)
from .simulation.engine import (
    GENERATOR_VERSION,
    advance_tick,
    initialize_state,
    load_zone_priors,
)
from .simulation.checkpoint import (
    FORMAT_VERSION as CHECKPOINT_FORMAT_VERSION,
    decode_checkpoint,
    encode_checkpoint,
)
from .simulation.models import (
    ACTIVE_STATUSES,
    DriverState,
    PauseControl,
    SimulationState,
    TickResult,
    ZonePrior,
)
from .simulation.randomness import canonical_checksum, stable_int
from .simulation.scenario import ScenarioFixture, load_scenario

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WINDOW_DIRECTORY = (
    ROOT
    / "data"
    / "scenarios"
    / "hanoi_heatwave_v1"
    / "production_window"
)
DEFAULT_WINDOW_MANIFEST = DEFAULT_WINDOW_DIRECTORY / "manifest.json"
DEFAULT_WINDOW_CHECKPOINT = DEFAULT_WINDOW_DIRECTORY / "start_state.json.gz"
RISK_MODEL_VERSION = "deterministic-action-risk-v1"
PRODUCTION_EVIDENCE_VERSION = "production-evidence-v2-zone-weather"
DEFAULT_HORIZON_MINUTES = 120


@dataclass(frozen=True)
class ProductionWindow:
    scenario_version: str
    generator_version: str
    seed: int
    start_tick: int
    decision_tick: int
    end_tick: int
    selected_zone_ids: tuple[str, ...]
    source_state_checksum: str
    checkpoint_format_version: str
    checkpoint_payload_sha256: str
    checkpoint_compressed_size: int

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProductionWindow:
        selected_zone_ids = value["selected_zone_ids"]
        if not isinstance(selected_zone_ids, list) or not all(
            isinstance(item, str) for item in selected_zone_ids
        ):
            raise ValueError("production window selected_zone_ids must be strings")
        return cls(
            scenario_version=str(value["scenario_version"]),
            generator_version=str(value["generator_version"]),
            seed=int(value["seed"]),
            start_tick=int(value["start_tick"]),
            decision_tick=int(value["decision_tick"]),
            end_tick=int(value["end_tick"]),
            selected_zone_ids=tuple(selected_zone_ids),
            source_state_checksum=str(value["source_state_checksum"]),
            checkpoint_format_version=str(value["checkpoint_format_version"]),
            checkpoint_payload_sha256=str(value["checkpoint_payload_sha256"]),
            checkpoint_compressed_size=int(value["checkpoint_compressed_size"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_version": self.scenario_version,
            "generator_version": self.generator_version,
            "seed": self.seed,
            "start_tick": self.start_tick,
            "decision_tick": self.decision_tick,
            "end_tick": self.end_tick,
            "selected_zone_ids": list(self.selected_zone_ids),
            "source_state_checksum": self.source_state_checksum,
            "checkpoint_format_version": self.checkpoint_format_version,
            "checkpoint_payload_sha256": self.checkpoint_payload_sha256,
            "checkpoint_compressed_size": self.checkpoint_compressed_size,
        }


@dataclass(frozen=True)
class ProductionEvidence:
    result: TickResult
    zones: tuple[ZoneSnapshot, ...]
    forecasts: tuple[DemandForecast, ...]
    predictions: tuple[DriverActionPrediction, ...]
    recommendations: tuple[tuple[str, RecommendationResult], ...]
    city_plan: CityWidePlan
    zone_risk: tuple[tuple[str, float], ...]

    def recommendation_for(self, zone_id: str) -> RecommendationResult | None:
        return dict(self.recommendations).get(zone_id)

    def forecast_for(self, zone_id: str) -> DemandForecast | None:
        return next(
            (forecast for forecast in self.forecasts if forecast.zone_id == zone_id),
            None,
        )


@dataclass(frozen=True)
class WindowCandidate:
    seed: int
    tick_index: int
    selected_zone_ids: tuple[str, ...]
    selected_drivers: int
    expected_risk_events_prevented: float
    max_exposed_4h: int
    state: SimulationState


def load_production_window(path: Path = DEFAULT_WINDOW_MANIFEST) -> ProductionWindow:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load production window: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("production window manifest must be an object")
    window = ProductionWindow.from_dict(value)
    if not 0 <= window.start_tick < window.decision_tick < window.end_tick <= 95:
        raise ValueError("production window tick bounds are invalid")
    if window.generator_version != GENERATOR_VERSION:
        raise ValueError("production window generator version is stale")
    if window.checkpoint_format_version != CHECKPOINT_FORMAT_VERSION:
        raise ValueError("production window checkpoint format is stale")
    return window


def load_warm_state(
    window: ProductionWindow,
    path: Path = DEFAULT_WINDOW_CHECKPOINT,
) -> SimulationState:
    """Load and verify the immutable state immediately before ``start_tick``."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot load production window checkpoint: {exc}") from exc
    if len(payload) != window.checkpoint_compressed_size:
        raise ValueError("production window checkpoint size mismatch")
    state = decode_checkpoint(
        payload,
        expected_payload_sha256=window.checkpoint_payload_sha256,
        expected_state_checksum=window.source_state_checksum,
    )
    expected_minute = window.start_tick * 15
    if (
        state.scenario_version != window.scenario_version
        or state.generator_version != window.generator_version
        or state.seed != window.seed
        or state.minute_index != expected_minute
    ):
        raise ValueError("production window checkpoint identity mismatch")
    return state


def state_before_tick(
    *,
    seed: int,
    tick_index: int,
    fixture: ScenarioFixture | None = None,
    zones: tuple[ZonePrior, ...] | None = None,
) -> SimulationState:
    """Return the deterministic state immediately before ``tick_index``."""
    if not 0 <= tick_index <= 95:
        raise ValueError("tick_index must be in 0..95")
    fixture = fixture or load_scenario("hanoi_heatwave_v1")
    zones = zones or load_zone_priors()
    state = initialize_state(seed=seed, fixture=fixture, zones=zones)
    for _ in range(tick_index):
        state = advance_tick(state, fixture=fixture, zones=zones).state
    return state


def _snapshot_id(result: TickResult) -> str:
    return canonical_checksum(
        (
            result.state.run_id,
            result.tick_index,
            result.simulation_time,
            PRODUCTION_EVIDENCE_VERSION,
        )
    )[:32]


def _forecast(
    result: TickResult,
    zone: ZonePrior,
    *,
    horizon_minutes: int,
) -> DemandForecast:
    intervals = max(1, min(16, math.ceil(horizon_minutes / 15)))
    zone_seed = stable_int(zone.zone_id, bits=64)
    start = result.simulation_time
    current_factor = max(0.08, intraday_demand_factor(start, zone_seed))
    current_projection = next(item for item in result.zones if item.zone_id == zone.zone_id)
    recent_15m = max(1, current_projection.requests_15m)
    points = []
    for index in range(intervals):
        at = start + timedelta(minutes=15 * (index + 1))
        factor = intraday_demand_factor(at, zone_seed)
        predicted = max(0, round(recent_15m * factor / current_factor))
        uncertainty = 0.12 + min(0.08, index * 0.01)
        points.append(
            ForecastPoint(
                forecast_at=at,
                predicted_requests=predicted,
                lower_bound=max(0, round(predicted * (1 - uncertainty))),
                upper_bound=round(predicted * (1 + uncertainty)),
            )
        )
    snapshot_id = _snapshot_id(result)
    return DemandForecast(
        zone_id=zone.zone_id,
        horizon_minutes=intervals * 15,
        predicted_requests=sum(point.predicted_requests for point in points),
        source="Production window · deterministic demand forecast",
        status="READY",
        points=tuple(points),
        forecast_source_tick_id=f"tick-{result.tick_index:02d}",
        forecast_source_snapshot_id=snapshot_id,
        generated_at=result.simulation_time,
    )


def _zone_snapshot(
    result: TickResult,
    zone: ZonePrior,
    forecast: DemandForecast,
    *,
    temperature_offset_c: float,
) -> ZoneSnapshot:
    projection = next(item for item in result.zones if item.zone_id == zone.zone_id)
    forecast_30m = sum(point.predicted_requests for point in forecast.points[:2])
    zone_temperature_c = round(
        result.weather.temperature_c + temperature_offset_c,
        4,
    )
    return ZoneSnapshot(
        zone_id=zone.zone_id,
        name=zone.name,
        latitude=zone.latitude,
        longitude=zone.longitude,
        temperature_c=zone_temperature_c,
        humidity_percent=result.weather.humidity_percent,
        heat_index_c=calculate_heat_index(
            zone_temperature_c,
            result.weather.humidity_percent,
        ),
        observed_at=result.simulation_time,
        scenario_id="heatwave",
        snapshot_id=_snapshot_id(result),
        weather_observed_at=result.simulation_time,
        operations_observed_at=result.simulation_time,
        active_drivers=projection.active_drivers,
        fresh_drivers=projection.fresh_drivers,
        exposed_2h=projection.exposed_2h,
        exposed_4h=projection.exposed_4h,
        forecast_requests_30m=forecast_30m,
        avg_platform_contribution_vnd=zone.avg_platform_contribution_vnd,
        avg_driver_earnings_vnd=zone.avg_driver_earnings_vnd,
        coolstop_name=zone.coolstop_name,
        coolstop_latitude=zone.coolstop_latitude,
        coolstop_longitude=zone.coolstop_longitude,
        source="Stateful production engine · synthetic zone microclimate offset",
        weather_is_simulated=True,
        operations_is_simulated=True,
        simulation_run_id=result.state.run_id,
        tick_id=f"tick-{result.tick_index:02d}",
        generator_version=(
            f"{result.state.generator_version}+{PRODUCTION_EVIDENCE_VERSION}"
        ),
    )


def _top_factors(driver: DriverState) -> tuple[str, ...]:
    values = (
        ("continuous exposure", float(driver.continuous_exposure_minutes)),
        ("heat dose", float(driver.heat_dose_120m)),
        ("hydration gap", float(driver.hydration_gap_minutes)),
        ("recent distance", float(driver.distance_km_60m)),
    )
    return tuple(name for name, _ in sorted(values, key=lambda item: -item[1])[:3])


def _predictions(result: TickResult, snapshot_id: str) -> tuple[DriverActionPrediction, ...]:
    predictions: list[DriverActionPrediction] = []
    prediction_run_id = canonical_checksum(
        (result.state.run_id, result.tick_index, snapshot_id, RISK_MODEL_VERSION)
    )[:24]
    for driver in result.state.drivers:
        if driver.status not in ACTIVE_STATUSES:
            continue
        exposure = float(driver.continuous_exposure_minutes)
        rest = float(driver.rest_minutes_120m)
        heat_dose = float(driver.heat_dose_120m)
        baseline = 1.0 / (
            1.0
            + math.exp(
                -(-3.2 + exposure / 125.0 + heat_dose / 90.0 - rest / 80.0)
            )
        )
        # Drivers far below both policy and model thresholds cannot become
        # eligible in this exact-snapshot decision, so omit their eight action
        # variants from the in-process evidence payload.
        if baseline < 0.35 and exposure < 240:
            continue
        factors = _top_factors(driver)
        for duration in (15, 30):
            for delay in (0, 15, 30, 45):
                benefit = (duration / 30.0) * math.exp(-delay / 60.0) * 0.18
                predictions.append(
                    DriverActionPrediction(
                        driver_id_hash=driver.driver_id_hash,
                        zone_id=driver.zone_id,
                        snapshot_id=snapshot_id,
                        prediction_run_id=f"sim-{prediction_run_id}",
                        model_version=RISK_MODEL_VERSION,
                        exposure_minutes=driver.continuous_exposure_minutes,
                        baseline_risk=round(baseline, 6),
                        action_risk=round(max(0.0, baseline - benefit), 6),
                        pause_start_delay_minutes=delay,
                        pause_duration_minutes=duration,
                        top_factors=factors,
                    )
                )
    return tuple(predictions)


def build_production_evidence(
    result: TickResult,
    *,
    fixture: ScenarioFixture | None = None,
    zones: tuple[ZonePrior, ...] | None = None,
    constraints: DecisionConstraints | None = None,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
) -> ProductionEvidence:
    """Build exact-tick local decision evidence without provider I/O."""
    fixture = fixture or load_scenario("hanoi_heatwave_v1")
    zones = zones or load_zone_priors()
    constraints = constraints or DecisionConstraints(horizon_minutes=horizon_minutes)
    temperature_offsets = fixture.manifest["zone_weather_offsets"]
    forecasts = tuple(
        _forecast(result, zone, horizon_minutes=constraints.horizon_minutes)
        for zone in zones
    )
    snapshots = tuple(
        _zone_snapshot(
            result,
            zone,
            forecast,
            temperature_offset_c=float(temperature_offsets[zone.zone_id]),
        )
        for zone, forecast in zip(zones, forecasts)
    )
    predictions = _predictions(result, _snapshot_id(result))
    by_zone: dict[str, tuple[DriverActionPrediction, ...]] = {
        zone.zone_id: tuple(
            item for item in predictions if item.zone_id == zone.zone_id
        )
        for zone in zones
    }
    recommendations: list[tuple[str, RecommendationResult]] = []
    rows: list[CityPlanRow] = []
    unavailable: list[UnavailableZone] = []
    zone_risk: list[tuple[str, float]] = []
    for snapshot, forecast in zip(snapshots, forecasts):
        zone_predictions = by_zone[snapshot.zone_id]
        baseline_by_driver = {
            item.driver_id_hash: item.baseline_risk for item in zone_predictions
        }
        zone_risk.append((snapshot.zone_id, round(sum(baseline_by_driver.values()), 3)))
        recommendation = recommend_ai_intervention(
            snapshot,
            zone_predictions,
            demand_by_interval=tuple(point.predicted_requests for point in forecast.points),
            upper_demand_by_interval=tuple(point.upper_bound for point in forecast.points),
            budget_cap_vnd=constraints.budget_cap_vnd,
            sponsor_per_driver_vnd=constraints.sponsor_per_driver_vnd,
        )
        recommendations.append((snapshot.zone_id, recommendation))
        if recommendation.recommended is None:
            unavailable.append(
                UnavailableZone(
                    zone_id=snapshot.zone_id,
                    zone_name=snapshot.name,
                    reason_code=recommendation.status,
                    message=recommendation.message,
                )
            )
        else:
            rows.append(
                CityPlanRow(
                    zone=snapshot,
                    forecast=forecast,
                    predictions=zone_predictions,
                    recommendation=recommendation,
                )
            )
    rows.sort(
        key=lambda row: (
            -row.proposal.expected_risk_events_prevented,
            -row.proposal.selected_drivers,
            row.zone_id,
        )
    )
    return ProductionEvidence(
        result=result,
        zones=snapshots,
        forecasts=forecasts,
        predictions=predictions,
        recommendations=tuple(recommendations),
        city_plan=CityWidePlan(
            rows=tuple(rows),
            unavailable_zones=tuple(unavailable),
            constraints=constraints,
        ),
        zone_risk=tuple(zone_risk),
    )


def controls_from_proposals(
    proposals: Iterable[SafePauseProposal],
    *,
    source_tick_index: int | None = None,
) -> tuple[PauseControl, ...]:
    """Convert exact-snapshot proposals into deterministic engine controls.

    Durable tick IDs are opaque checksums, so a caller that already owns the
    authoritative simulation state must supply its numeric tick index.  The
    legacy ``tick-<n>`` fallback remains only for older production evidence.
    """
    if source_tick_index is not None and source_tick_index < 0:
        raise ValueError("source tick index must be non-negative")
    controls: list[PauseControl] = []
    for proposal in proposals:
        if not proposal.within_guardrails or proposal.source_tick_id is None:
            continue
        proposal_tick_index = source_tick_index
        if proposal_tick_index is None:
            try:
                proposal_tick_index = int(proposal.source_tick_id.rsplit("-", 1)[1])
            except (IndexError, ValueError) as exc:
                raise ValueError(
                    "opaque proposal source tick requires source_tick_index"
                ) from exc
        grouped: dict[tuple[int, int], list[DriverDecision]] = {}
        for decision in proposal.driver_decisions:
            grouped.setdefault(
                (
                    decision.pause_start_delay_minutes,
                    decision.pause_duration_minutes,
                ),
                [],
            ).append(decision)
        for (delay, duration), decisions in sorted(grouped.items()):
            control_id = canonical_checksum(
                (proposal.proposal_id, proposal.source_snapshot_id, delay, duration)
            )[:32]
            controls.append(
                PauseControl(
                    control_id=control_id,
                    control_event_id=canonical_checksum(
                        (proposal.proposal_id, proposal.source_snapshot_id)
                    )[:32],
                    proposal_id=proposal.proposal_id,
                    driver_ids=tuple(
                        sorted(str(item.driver_id_hash) for item in decisions)
                    ),
                    requested_minute=(proposal_tick_index + 1) * 15 + delay,
                    pause_duration_minutes=duration,
                    max_start_delay_minutes=45,
                    pause_start_delay_minutes=delay,
                    baseline_risk_by_driver=tuple(
                        sorted(
                            (str(item.driver_id_hash), float(item.baseline_risk))
                            for item in decisions
                        )
                    ),
                    action_risk_by_driver=tuple(
                        sorted(
                            (str(item.driver_id_hash), float(item.action_risk))
                            for item in decisions
                        )
                    ),
                )
            )
    return tuple(sorted(controls, key=lambda item: item.control_id))


def find_production_window(
    *,
    seeds: tuple[int, ...] = (42, 77, 91),
    search_start_tick: int = 24,
    search_end_tick: int = 80,
    pre_roll_ticks: int = 8,
    post_roll_ticks: int = 8,
    max_selected_zones: int | None = None,
    fixture: ScenarioFixture | None = None,
    zones: tuple[ZonePrior, ...] | None = None,
    constraints: DecisionConstraints | None = None,
) -> WindowCandidate:
    """Find the strongest feasible multi-zone decision window deterministically."""
    fixture = fixture or load_scenario("hanoi_heatwave_v1")
    zones = zones or load_zone_priors()
    constraints = constraints or DecisionConstraints(horizon_minutes=120)
    if max_selected_zones is not None:
        raise ValueError(
            "fixed selected-zone limits are no longer supported; use the city portfolio"
        )
    candidates: list[WindowCandidate] = []
    for seed in seeds:
        state = initialize_state(seed=seed, fixture=fixture, zones=zones)
        for tick_index in range(search_end_tick + 1):
            result = advance_tick(state, fixture=fixture, zones=zones)
            state = result.state
            if tick_index < search_start_tick:
                continue
            evidence = build_production_evidence(
                result,
                fixture=fixture,
                zones=zones,
                constraints=constraints,
            )
            predictive_plan = build_predictive_city_plan(
                project_city_forecast(
                    build_accelerated_forecast_input(
                        result,
                        fixture=fixture,
                        zones=zones,
                    )
                ),
                constraints,
            )
            selected_rows = tuple(
                row
                for row in predictive_plan.rows
                if row.zone_id in predictive_plan.selected_zone_ids
                and row.best_window is not None
            )
            if not selected_rows:
                continue
            candidates.append(
                WindowCandidate(
                    seed=seed,
                    tick_index=tick_index,
                    selected_zone_ids=tuple(row.zone_id for row in selected_rows),
                    selected_drivers=sum(
                        row.best_window.proposal.selected_drivers
                        for row in selected_rows
                        if row.best_window is not None
                    ),
                    expected_risk_events_prevented=round(
                        sum(
                            row.expected_risk_prevented
                            for row in selected_rows
                        ),
                        3,
                    ),
                    max_exposed_4h=max(zone.exposed_4h for zone in evidence.zones),
                    state=result.state,
                )
            )
    if not candidates:
        raise RuntimeError("no feasible SafePause production window was found")
    viable = [
        candidate
        for candidate in candidates
        if candidate.tick_index - pre_roll_ticks >= 0
        and candidate.tick_index + post_roll_ticks <= 95
    ]
    if not viable:
        raise RuntimeError("no feasible candidate has enough pre/post roll")
    return min(
        viable,
        key=lambda item: (
            -len(item.selected_zone_ids),
            -item.expected_risk_events_prevented,
            -item.selected_drivers,
            item.tick_index,
            item.seed,
        ),
    )


def build_window_manifest(
    candidate: WindowCandidate,
    *,
    pre_roll_ticks: int = 8,
    post_roll_ticks: int = 8,
    warm_state: SimulationState | None = None,
) -> ProductionWindow:
    start_tick = candidate.tick_index - pre_roll_ticks
    warm_state = warm_state or state_before_tick(
        seed=candidate.seed,
        tick_index=start_tick,
    )
    checkpoint = encode_checkpoint(warm_state)
    return ProductionWindow(
        scenario_version="hanoi_heatwave_v1",
        generator_version=GENERATOR_VERSION,
        seed=candidate.seed,
        start_tick=start_tick,
        decision_tick=candidate.tick_index,
        end_tick=candidate.tick_index + post_roll_ticks,
        selected_zone_ids=candidate.selected_zone_ids,
        source_state_checksum=checkpoint.state_checksum,
        checkpoint_format_version=CHECKPOINT_FORMAT_VERSION,
        checkpoint_payload_sha256=checkpoint.payload_sha256,
        checkpoint_compressed_size=len(checkpoint.data),
    )


def write_window_artifact(
    window: ProductionWindow,
    warm_state: SimulationState,
    *,
    directory: Path = DEFAULT_WINDOW_DIRECTORY,
) -> None:
    """Write deterministic local assets; caller owns discovery and review."""
    checkpoint = encode_checkpoint(warm_state)
    if (
        checkpoint.state_checksum != window.source_state_checksum
        or checkpoint.payload_sha256 != window.checkpoint_payload_sha256
        or len(checkpoint.data) != window.checkpoint_compressed_size
    ):
        raise ValueError("production window manifest does not match warm state")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "start_state.json.gz").write_bytes(checkpoint.data)
    (directory / "manifest.json").write_text(
        json.dumps(window.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


SessionStatus = Literal["READY", "RUNNING", "AWAITING_DECISION", "COMPLETED"]
SessionChoice = Literal["ACTIVATE", "CONTINUE"]


@dataclass
class ProductionSession:
    """Session-local accelerated window with a deterministic shadow baseline."""

    window: ProductionWindow
    warm_state: SimulationState
    fixture: ScenarioFixture
    zones: tuple[ZonePrior, ...]
    actual_state: SimulationState
    shadow_state: SimulationState
    actual_result: TickResult
    shadow_result: TickResult
    actual_history: list[TickResult]
    shadow_history: list[TickResult]
    status: SessionStatus
    choice: SessionChoice | None = None
    controls: tuple[PauseControl, ...] = ()
    decision_evidence: ProductionEvidence | None = None

    @classmethod
    def create(
        cls,
        *,
        window: ProductionWindow | None = None,
        warm_state: SimulationState | None = None,
        fixture: ScenarioFixture | None = None,
        zones: tuple[ZonePrior, ...] | None = None,
    ) -> ProductionSession:
        window = window or load_production_window()
        warm_state = warm_state or load_warm_state(window)
        fixture = fixture or load_scenario(window.scenario_version)
        zones = zones or load_zone_priors()
        first = advance_tick(warm_state, fixture=fixture, zones=zones)
        if first.tick_index != window.start_tick:
            raise ValueError("warm checkpoint does not advance to window start")
        status: SessionStatus = (
            "AWAITING_DECISION"
            if first.tick_index == window.decision_tick
            else "READY"
        )
        return cls(
            window=window,
            warm_state=warm_state,
            fixture=fixture,
            zones=zones,
            actual_state=first.state,
            shadow_state=first.state,
            actual_result=first,
            shadow_result=first,
            actual_history=[first],
            shadow_history=[first],
            status=status,
            decision_evidence=(
                build_production_evidence(first, fixture=fixture, zones=zones)
                if status == "AWAITING_DECISION"
                else None
            ),
        )

    @property
    def current_tick(self) -> int:
        return self.actual_result.tick_index

    def start(self) -> None:
        if self.status == "READY":
            self.status = "RUNNING"

    def pause(self) -> None:
        if self.status == "RUNNING":
            self.status = "READY"

    def choose(
        self,
        choice: SessionChoice,
        *,
        proposals: tuple[SafePauseProposal, ...] | None = None,
    ) -> None:
        if self.status != "AWAITING_DECISION" or self.decision_evidence is None:
            raise ValueError("production decision is not currently available")
        self.choice = choice
        if choice == "ACTIVATE":
            if proposals is None:
                shared_plan = build_predictive_city_plan(
                    project_city_forecast(
                        build_accelerated_forecast_input(
                            self.actual_result,
                            fixture=self.fixture,
                            zones=self.zones,
                        )
                    ),
                    self.decision_evidence.city_plan.constraints,
                )
                proposals = tuple(
                    row.best_window.proposal
                    for row in shared_plan.rows
                    if row.zone_id in shared_plan.selected_zone_ids
                    and row.best_window is not None
                )
            if not proposals:
                raise ValueError("production window has no selected proposals")
            self.controls = controls_from_proposals(
                proposals,
                source_tick_index=self.current_tick,
            )
            if not self.controls:
                raise ValueError("production proposal produced no controls")
        else:
            self.controls = ()
        self.status = "RUNNING"

    def advance(self) -> TickResult:
        if self.status == "AWAITING_DECISION":
            raise ValueError("choose Activate or Continue before advancing")
        if self.current_tick >= self.window.end_tick:
            self.status = "COMPLETED"
            return self.actual_result
        actual = advance_tick(
            self.actual_state,
            fixture=self.fixture,
            zones=self.zones,
            controls=self.controls if self.choice == "ACTIVATE" else (),
        )
        shadow = advance_tick(
            self.shadow_state,
            fixture=self.fixture,
            zones=self.zones,
        )
        self.actual_state = actual.state
        self.shadow_state = shadow.state
        self.actual_result = actual
        self.shadow_result = shadow
        self.actual_history.append(actual)
        self.shadow_history.append(shadow)
        if actual.tick_index < self.window.decision_tick and actual.state != shadow.state:
            raise RuntimeError("actual and shadow branches diverged before control")
        if actual.tick_index == self.window.decision_tick:
            self.decision_evidence = build_production_evidence(
                actual,
                fixture=self.fixture,
                zones=self.zones,
            )
            self.status = "AWAITING_DECISION"
        elif actual.tick_index >= self.window.end_tick:
            self.status = "COMPLETED"
        return actual

    def reset(self) -> None:
        reset = type(self).create(
            window=self.window,
            warm_state=self.warm_state,
            fixture=self.fixture,
            zones=self.zones,
        )
        self.__dict__.update(reset.__dict__)
