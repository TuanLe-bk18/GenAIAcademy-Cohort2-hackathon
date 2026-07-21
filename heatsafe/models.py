from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


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

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["created_at"] = self.created_at.isoformat()
        value["source_snapshot_at"] = self.source_snapshot_at.isoformat()
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
