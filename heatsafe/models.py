from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


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
    is_simulated: bool

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["observed_at"] = self.observed_at.isoformat()
        return value


@dataclass(frozen=True)
class SafePauseProposal:
    proposal_id: str
    zone_id: str
    zone_name: str
    created_at: datetime
    source_snapshot_at: datetime
    eligible_drivers: int
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
    projected_fulfillment_rate: float
    projected_eta_increase_minutes: float
    within_guardrails: bool
    guardrail_notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["created_at"] = self.created_at.isoformat()
        value["source_snapshot_at"] = self.source_snapshot_at.isoformat()
        value["guardrail_notes"] = list(self.guardrail_notes)
        return value


@dataclass(frozen=True)
class InterventionEvent:
    intervention_id: str
    proposal_id: str
    approved_at: datetime
    approved_by: str
    status: str
    proposal: SafePauseProposal

    def to_dict(self) -> dict[str, Any]:
        return {
            "intervention_id": self.intervention_id,
            "proposal_id": self.proposal_id,
            "approved_at": self.approved_at.isoformat(),
            "approved_by": self.approved_by,
            "status": self.status,
            "proposal": self.proposal.to_dict(),
        }
