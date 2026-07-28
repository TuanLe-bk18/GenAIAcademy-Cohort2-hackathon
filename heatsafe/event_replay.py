"""Deterministic rolling SafePause policy for the display-only Event Replay."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .ai_decision import MANDATORY_PRIORITY_TIER, PROJECTED_MANDATORY_PRIORITY_TIER
from .models import DecisionConstraints, PredictiveCityPlan, SafePauseProposal
from .production_mode import ProductionSession, controls_from_proposals
from .services.preventive_planning import (
    MANDATORY_EXPOSURE_MINUTES,
    build_accelerated_forecast_input,
    build_predictive_city_plan,
    project_city_forecast,
)
from .simulation.models import ACTIVE_STATUSES, TickResult


@dataclass(frozen=True)
class RollingReplayEvent:
    tick: int
    time_label: str
    outcome: str
    portfolio_id: str
    proposal_ids: tuple[str, ...]
    new_driver_count: int
    new_mandatory_count: int
    new_preventive_count: int
    cumulative_driver_count: int
    incremental_p95_cost_vnd: int
    cumulative_p95_cost_vnd: int
    budget_remaining_vnd: int
    mandatory_covered: int
    mandatory_uncovered: int


@dataclass(frozen=True)
class RollingReplayEvaluation:
    plan: PredictiveCityPlan
    proposals: tuple[SafePauseProposal, ...]
    event: RollingReplayEvent


class RollingEventReplayController:
    """Evaluate a narrow, non-duplicating SafePause supplement every tick.

    The controller uses a 15-minute action horizon, reserves selected drivers
    immediately (including delayed execution), and treats each plan P95 as a
    conservative addition to the replay-wide budget ledger.
    """

    def __init__(
        self,
        constraints: DecisionConstraints,
        *,
        mandatory_budget_reserve_ratio: float = 0.20,
    ) -> None:
        if not 0 <= mandatory_budget_reserve_ratio < 1:
            raise ValueError("mandatory budget reserve ratio must be in [0, 1)")
        self.constraints = constraints.normalized()
        self.mandatory_budget_reserve_vnd = round(
            self.constraints.budget_cap_vnd * mandatory_budget_reserve_ratio
        )
        self.reserved_driver_ids: set[str] = set()
        self.preventive_driver_ids: set[str] = set()
        self.started_preventive_driver_ids: set[str] = set()
        self.cumulative_p95_cost_vnd = 0
        self.events: list[RollingReplayEvent] = []

    @property
    def budget_remaining_vnd(self) -> int:
        return max(
            0,
            self.constraints.budget_cap_vnd - self.cumulative_p95_cost_vnd,
        )

    def observe(self, result: TickResult) -> None:
        self.started_preventive_driver_ids.update(
            intervention.driver_id_hash
            for intervention in result.state.interventions
            if intervention.driver_id_hash in self.preventive_driver_ids
            and intervention.started_minute is not None
        )

    def evaluate_and_queue(
        self,
        session: ProductionSession,
        *,
        queue_controls: bool = True,
    ) -> RollingReplayEvaluation:
        has_unreserved_mandatory = any(
            driver.driver_id_hash not in self.reserved_driver_ids
            and driver.status in ACTIVE_STATUSES
            and driver.continuous_exposure_minutes >= MANDATORY_EXPOSURE_MINUTES
            for driver in session.actual_result.state.drivers
        )
        planning_budget_vnd = self.budget_remaining_vnd
        if not has_unreserved_mandatory:
            planning_budget_vnd = max(
                0,
                planning_budget_vnd - self.mandatory_budget_reserve_vnd,
            )
        remaining_constraints = replace(
            self.constraints,
            budget_cap_vnd=planning_budget_vnd,
        )
        forecast_input = build_accelerated_forecast_input(
            session.actual_result,
            fixture=session.fixture,
            zones=session.zones,
        )
        plan = build_predictive_city_plan(
            project_city_forecast(forecast_input),
            remaining_constraints,
            preventive_horizon_minutes=15,
            reserved_driver_ids=frozenset(self.reserved_driver_ids),
            actionable_only=True,
            include_preventive=not has_unreserved_mandatory,
            candidate_start_delays=(0,),
            candidate_waves=(1,),
        )
        proposals = tuple(
            row.best_window.proposal
            for row in plan.rows
            if row.zone_id in plan.selected_zone_ids and row.best_window is not None
        )
        selected_ids = {
            decision.driver_id_hash
            for proposal in proposals
            for decision in proposal.driver_decisions
        }
        overlap = selected_ids & self.reserved_driver_ids
        if overlap:
            raise RuntimeError("rolling replay selected an already reserved driver")

        outcome = "NO_INCREMENTAL_ACTION"
        activated = False
        if plan.status == "EVIDENCE_UNAVAILABLE":
            outcome = "EVIDENCE_UNAVAILABLE"
        elif proposals:
            controls = controls_from_proposals(
                proposals,
                source_tick_index=session.current_tick,
            )
            if not controls:
                raise RuntimeError("rolling replay plan produced no controls")
            if queue_controls:
                session.queue_controls(controls)
            self.reserved_driver_ids.update(selected_ids)
            self.cumulative_p95_cost_vnd += plan.p95_reserved_cost_vnd
            activated = True
            outcome = (
                "PARTIAL_SAFETY_CAPACITY_BREACH"
                if plan.status == "SAFETY_CAPACITY_BREACH"
                else "ACTIVATED"
                if not self.events
                else "SUPPLEMENTED"
            )
        elif plan.status == "SAFETY_CAPACITY_BREACH":
            outcome = "SAFETY_CAPACITY_BREACH"

        decisions = tuple(
            decision
            for proposal in proposals
            for decision in proposal.driver_decisions
        )
        if activated:
            self.preventive_driver_ids.update(
                decision.driver_id_hash
                for decision in decisions
                if decision.priority_tier == PROJECTED_MANDATORY_PRIORITY_TIER
            )

        event = RollingReplayEvent(
            tick=session.current_tick,
            time_label=session.actual_result.simulation_time.strftime("%H:%M"),
            outcome=outcome,
            portfolio_id=plan.portfolio_id,
            proposal_ids=(
                tuple(proposal.proposal_id for proposal in proposals)
                if activated
                else ()
            ),
            new_driver_count=len(selected_ids) if activated else 0,
            new_mandatory_count=(
                sum(
                    decision.priority_tier == MANDATORY_PRIORITY_TIER
                    for decision in decisions
                )
                if activated
                else 0
            ),
            new_preventive_count=(
                sum(
                    decision.priority_tier == PROJECTED_MANDATORY_PRIORITY_TIER
                    for decision in decisions
                )
                if activated
                else 0
            ),
            cumulative_driver_count=len(self.reserved_driver_ids),
            incremental_p95_cost_vnd=(
                plan.p95_reserved_cost_vnd if activated else 0
            ),
            cumulative_p95_cost_vnd=self.cumulative_p95_cost_vnd,
            budget_remaining_vnd=self.budget_remaining_vnd,
            mandatory_covered=plan.mandatory_now_covered,
            mandatory_uncovered=plan.mandatory_now_uncovered,
        )
        self.events.append(event)
        return RollingReplayEvaluation(plan=plan, proposals=proposals, event=event)


__all__ = [
    "RollingEventReplayController",
    "RollingReplayEvaluation",
    "RollingReplayEvent",
]
