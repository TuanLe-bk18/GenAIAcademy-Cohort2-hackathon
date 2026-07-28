from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .simulation.models import PauseControl


@dataclass(frozen=True)
class DecisionConstraints:
    """Normalized controls shared by selected-zone and city-wide decisions."""

    horizon_minutes: int = 240
    budget_cap_vnd: int = 5_000_000
    sponsor_per_driver_vnd: int = 8_000

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "horizon_minutes",
            max(15, min(240, int(self.horizon_minutes))),
        )
        object.__setattr__(self, "budget_cap_vnd", max(0, int(self.budget_cap_vnd)))
        object.__setattr__(
            self,
            "sponsor_per_driver_vnd",
            max(0, int(self.sponsor_per_driver_vnd)),
        )

    def normalized(self) -> DecisionConstraints:
        """Return a constructor-normalized copy for explicit boundary handling."""
        return DecisionConstraints(
            horizon_minutes=self.horizon_minutes,
            budget_cap_vnd=self.budget_cap_vnd,
            sponsor_per_driver_vnd=self.sponsor_per_driver_vnd,
        )


@dataclass(frozen=True)
class ZoneSnapshot:
    zone_id: str
    name: str
    latitude: float
    longitude: float
    temperature_c: float
    humidity_percent: float
    heat_index_c: float
    observed_at: datetime
    scenario_id: str
    snapshot_id: str
    weather_observed_at: datetime
    operations_observed_at: datetime
    active_drivers: int
    fresh_drivers: int
    exposed_2h: int
    exposed_4h: int
    forecast_requests_30m: int
    avg_platform_contribution_vnd: int
    avg_driver_earnings_vnd: int
    coolstop_name: str
    coolstop_latitude: float
    coolstop_longitude: float
    source: str
    weather_is_simulated: bool
    operations_is_simulated: bool
    simulation_run_id: str | None = None
    tick_id: str | None = None
    generator_version: str | None = None

    @property
    def is_simulated(self) -> bool:
        return self.weather_is_simulated or self.operations_is_simulated

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["observed_at"] = self.observed_at.isoformat()
        value["weather_observed_at"] = self.weather_observed_at.isoformat()
        value["operations_observed_at"] = self.operations_observed_at.isoformat()
        value["is_simulated"] = self.is_simulated
        return value


@dataclass(frozen=True)
class PauseWave:
    wave: int
    start_minute: int
    end_minute: int
    selected_drivers: int
    high_priority_drivers: int
    medium_priority_drivers: int


@dataclass(frozen=True)
class DriverActionPrediction:
    driver_id_hash: str
    zone_id: str
    snapshot_id: str
    prediction_run_id: str
    model_version: str
    exposure_minutes: int
    baseline_risk: float
    action_risk: float
    pause_start_delay_minutes: int
    pause_duration_minutes: int
    top_factors: tuple[str, ...] = ()

    @property
    def risk_reduction(self) -> float:
        return max(0.0, self.baseline_risk - self.action_risk)


@dataclass(frozen=True)
class DriverCurrentFeature:
    """Snapshot-matched driver features used by preventive projection."""

    scenario_id: str
    snapshot_id: str
    observed_at: datetime
    driver_id_hash: str
    zone_id: str
    heat_index_c: float
    humidity_percent: float
    continuous_exposure_minutes: int
    trips_60m: int
    distance_km_60m: float
    rest_minutes_120m: int
    hydration_gap_minutes: int
    route_heat_load: float
    workload_intensity: float
    is_simulated: bool
    simulation_run_id: str | None = None
    tick_id: str | None = None
    driver_status: str = "ACTIVE"
    heat_dose_120m: float = 0.0
    acclimatization_class: str | None = None
    generator_version: str | None = None


@dataclass(frozen=True)
class ForecastEvidenceLineage:
    mode: str
    scenario_id: str
    snapshot_id: str
    observed_at: datetime
    prediction_run_ids: tuple[str, ...]
    model_versions: tuple[str, ...]
    simulation_run_id: str | None = None
    tick_id: str | None = None
    tick_index: int | None = None
    scenario_version: str | None = None
    generator_version: str | None = None


@dataclass(frozen=True)
class ForecastDemandPoint:
    minutes_ahead: int
    median_requests: int
    upper_requests: int


@dataclass(frozen=True)
class HeatForecastEvidence:
    minutes_ahead: int
    temperature_c: float
    humidity_percent: float
    heat_index_c: float
    provenance: str
    model_version: str | None = None


@dataclass(frozen=True)
class ForecastDriverAction:
    pause_start_delay_minutes: int
    pause_duration_minutes: int
    action_risk: float
    top_factors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForecastDriverInput:
    driver_id_hash: str
    zone_id: str
    status: str
    continuous_exposure_minutes: int
    rest_minutes_120m: int
    hydration_gap_minutes: int
    heat_dose_120m: float
    baseline_risk: float
    actions: tuple[ForecastDriverAction, ...] = ()


@dataclass(frozen=True)
class ForecastZoneInput:
    zone: ZoneSnapshot
    heat: tuple[HeatForecastEvidence, ...]
    demand: tuple[ForecastDemandPoint, ...]
    drivers: tuple[ForecastDriverInput, ...]


@dataclass(frozen=True)
class CurrentForecastInput:
    lineage: ForecastEvidenceLineage
    zones: tuple[ForecastZoneInput, ...]
    horizons: tuple[int, ...] = (0, 60, 120)


@dataclass(frozen=True)
class AcceleratedForecastInput:
    lineage: ForecastEvidenceLineage
    zones: tuple[ForecastZoneInput, ...]
    horizons: tuple[int, ...] = (0, 60, 120)


@dataclass(frozen=True)
class ForecastHorizon:
    minutes_ahead: int
    heat: HeatForecastEvidence
    demand_median: int
    demand_upper: int
    mandatory_now: int
    projected_mandatory: int
    watchlist: int
    expected_crossers: float
    online_continuation_probability: float
    baseline_expected_risk: float


@dataclass(frozen=True)
class DriverForecastHorizon:
    minutes_ahead: int
    crossing_probability: float
    online_probability: float
    projected_risk: float


@dataclass(frozen=True)
class DriverForecastProjection:
    driver_id_hash: str
    horizons: tuple[DriverForecastHorizon, ...]


@dataclass(frozen=True)
class ZoneForecastProjection:
    zone_id: str
    zone_name: str
    horizons: tuple[ForecastHorizon, ...]
    drivers: tuple[DriverForecastProjection, ...] = ()
    source: ForecastZoneInput | None = None


@dataclass(frozen=True)
class CityForecastProjection:
    lineage: ForecastEvidenceLineage
    zones: tuple[ZoneForecastProjection, ...]
    path_ids: tuple[str, ...]
    projection_version: str


@dataclass(frozen=True)
class InterventionWindow:
    start_delay_minutes: int
    end_delay_minutes: int
    proposal: SafePauseProposal
    path_costs_vnd: tuple[int, ...]
    expected_cost_vnd: int
    p95_reserved_cost_vnd: int
    projected_mandatory_after_60m: float
    projected_mandatory_after_120m: float
    residual_risk_60m: float
    residual_risk_120m: float


@dataclass(frozen=True)
class PredictiveZonePlanRow:
    zone_id: str
    zone_name: str
    horizons: tuple[ForecastHorizon, ...]
    current_raw_risk: float
    expected_risk_prevented: float
    best_window: InterventionWindow | None
    preventive_pauses: int
    severity_rank: int
    future_safety_rank: int
    opportunity_rank: int
    portfolio_status: str
    portfolio_reason: str
    path_costs_vnd: tuple[int, ...]


@dataclass(frozen=True)
class TimingOption:
    proposal_id: str = ""
    start_delay_minutes: int = 0
    start_time: datetime | None = None
    pause_minutes: int = 0
    waves: int = 0
    drivers_protected: int = 0
    projected_drivers_at_limit_120m: float = 0.0
    residual_risk_120m: float = 0.0
    expected_cost_vnd: int = 0
    high_demand_reserved_cost_vnd: int = 0
    expected_fulfillment_rate: float = 0.0
    high_demand_fulfillment_rate: float = 0.0
    expected_pickup_delay_minutes: float = 0.0
    high_demand_pickup_delay_minutes: float = 0.0
    expected_demand_requests: int = 0
    high_demand_requests: int = 0
    feasible: bool = False
    rejection_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ZoneOptimizationOptions:
    zone_id: str = ""
    selected_proposal_id: str | None = None
    timing_options: tuple[TimingOption, ...] = ()
    proposal_alternatives: tuple[TimingOption, ...] = ()


@dataclass(frozen=True)
class PortfolioTradeoffPoint:
    option_id: str = ""
    label: str = ""
    selected: bool = False
    feasible: bool = False
    selected_zone_ids: tuple[str, ...] = ()
    protected_drivers: int = 0
    urgent_drivers_covered: int = 0
    urgent_drivers_required: int = 0
    exposure_hours_avoided: float = 0.0
    projected_drivers_at_limit_120m: float = 0.0
    expected_cost_vnd: int = 0
    high_demand_reserved_cost_vnd: int = 0
    worst_area_pickup_delay_minutes: float = 0.0
    rejection_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CityOptimizationEvidence:
    evaluated_portfolio_count: int = 0
    budget_compliant_portfolio_count: int = 0
    selected_portfolio_id: str | None = None
    portfolio_options: tuple[PortfolioTradeoffPoint, ...] = ()
    zone_options: tuple[ZoneOptimizationOptions, ...] = ()


@dataclass(frozen=True)
class PredictiveCityPlan:
    portfolio_id: str
    mode: str
    rows: tuple[PredictiveZonePlanRow, ...]
    selected_zone_ids: tuple[str, ...]
    expected_cost_vnd: int
    p95_reserved_cost_vnd: int
    budget_cap_vnd: int
    status: str
    evidence_lineage: ForecastEvidenceLineage
    forecast_version: str
    created_at: datetime
    expires_at: datetime
    mandatory_now_covered: int = 0
    mandatory_now_uncovered: int = 0
    optimization_evidence: CityOptimizationEvidence | None = None


@dataclass(frozen=True)
class ProjectedZoneOutcome:
    zone_id: str
    baseline_mandatory_60m: float
    projected_mandatory_60m: float
    baseline_mandatory_120m: float
    projected_mandatory_120m: float
    baseline_risk_60m: float
    residual_risk_60m: float
    baseline_risk_120m: float
    residual_risk_120m: float


@dataclass(frozen=True)
class SimulatedControlReceipt:
    receipt_id: str
    portfolio_id: str
    evidence_lineage: ForecastEvidenceLineage
    selected_proposal_checksums: tuple[str, ...]
    controls: tuple[PauseControl, ...]
    projected_outcomes: tuple[ProjectedZoneOutcome, ...]
    status: str
    dispatch_status: str
    created_at: datetime
    approved_intervention_ids: tuple[str, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class DriverDecision:
    driver_id_hash: str
    exposure_minutes: int
    baseline_risk: float
    action_risk: float
    pause_start_delay_minutes: int
    pause_duration_minutes: int
    top_factors: tuple[str, ...] = ()
    priority_tier: str = "MODEL_ELIGIBLE"
    risk_of_waiting: float = 0.0
    assignment_reason: str = ""

    @property
    def risk_reduction(self) -> float:
        return max(0.0, self.baseline_risk - self.action_risk)


@dataclass(frozen=True)
class SafePauseProposal:
    proposal_id: str
    zone_id: str
    zone_name: str
    created_at: datetime
    source_snapshot_at: datetime
    eligible_drivers: int
    high_priority_drivers: int
    medium_priority_drivers: int
    selected_drivers: int
    cohort_coverage: float
    pause_minutes: int
    waves: int
    planned_paused_driver_slots: int
    reassigned_trips: int
    missed_trips: int
    earnings_guard_cost_vnd: int
    partner_sponsorship_vnd: int
    lost_contribution_vnd: int
    net_platform_cost_vnd: int
    partner_hydration_value_vnd: int
    exposure_minutes_avoided: int
    risk_weighted_minutes_avoided: int
    simulation_horizon_minutes: int
    projected_fulfillment_rate: float
    projected_eta_increase_minutes: float
    p90_fulfillment_rate: float
    p90_eta_increase_minutes: float
    within_guardrails: bool
    guardrail_notes: tuple[str, ...]
    decision_reason: str
    wave_plan: tuple[PauseWave, ...]
    prediction_run_id: str | None = None
    model_version: str | None = None
    baseline_expected_risk_events: float = 0.0
    action_expected_risk_events: float = 0.0
    expected_risk_events_prevented: float = 0.0
    baseline_fulfillment_rate: float = 1.0
    baseline_stress_fulfillment_rate: float = 1.0
    driver_decisions: tuple[DriverDecision, ...] = ()
    mandatory_eligible_drivers: int = 0
    mandatory_selected_drivers: int = 0
    max_mandatory_delay_minutes: int = 0
    scenario_id: str | None = None
    source_snapshot_id: str | None = None
    simulation_run_id: str | None = None
    source_tick_id: str | None = None
    expires_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["created_at"] = self.created_at.isoformat()
        value["source_snapshot_at"] = self.source_snapshot_at.isoformat()
        value["expires_at"] = (
            self.expires_at.isoformat() if self.expires_at is not None else None
        )
        value["guardrail_notes"] = list(self.guardrail_notes)
        value["wave_plan"] = [asdict(wave) for wave in self.wave_plan]
        value["driver_decisions"] = [asdict(item) for item in self.driver_decisions]
        return value


@dataclass(frozen=True)
class RecommendationResult:
    status: str
    prediction_run_id: str | None
    model_version: str | None
    eligible_drivers: int
    baseline_expected_risk_events: float
    recommended: SafePauseProposal | None
    alternatives: tuple[SafePauseProposal, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class InterventionEvent:
    intervention_id: str
    proposal_id: str
    approved_at: datetime
    approved_by: str
    actor_type: str
    status: str
    dispatch_status: str
    proposal: SafePauseProposal

    def to_dict(self) -> dict[str, Any]:
        return {
            "intervention_id": self.intervention_id,
            "proposal_id": self.proposal_id,
            "approved_at": self.approved_at.isoformat(),
            "approved_by": self.approved_by,
            "actor_type": self.actor_type,
            "status": self.status,
            "dispatch_status": self.dispatch_status,
            "proposal": self.proposal.to_dict(),
        }
