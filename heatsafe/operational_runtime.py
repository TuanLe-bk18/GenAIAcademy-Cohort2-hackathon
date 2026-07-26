"""Shared simulated activation and durable accelerated-clock adapters.

The module contains no dispatch integration.  Current mode records a projected
decision only.  Accelerated mode queues controls into the existing durable
simulation boundary and advances through the fenced repository/checkpoint path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .models import (
    InterventionEvent,
    PredictiveCityPlan,
    PredictiveZonePlanRow,
    ProjectedZoneOutcome,
    SafePauseProposal,
    SimulatedControlReceipt,
)
from .simulation.control import BigQueryControlWriter
from .simulation.models import PauseControl, TickResult
from .simulation.randomness import canonical_checksum
from .simulation.repository import (
    LeaseConflict,
    Publication,
    SimulationRepository,
    SimulationRepositoryError,
    SimulationRun,
    replay_to_tick,
    validate_persisted_tick_clock,
)


class OperationalRuntimeError(RuntimeError):
    """Raised when a durable operational invariant is violated."""


class SimulatedAuditStore(Protocol):
    def approve(self, proposal: SafePauseProposal) -> InterventionEvent: ...


class AcceleratedControlQueue(Protocol):
    def queue_plan(
        self,
        plan: PredictiveCityPlan,
        proposals: tuple[SafePauseProposal, ...],
        *,
        execution_id: str,
    ) -> tuple[PauseControl, ...]: ...


def _selected_rows(
    plan: PredictiveCityPlan,
) -> tuple[PredictiveZonePlanRow, ...]:
    selected = set(plan.selected_zone_ids)
    rows = tuple(row for row in plan.rows if row.zone_id in selected)
    if {row.zone_id for row in rows} != selected:
        raise OperationalRuntimeError(
            "selected district IDs do not resolve to exact plan rows"
        )
    if any(row.best_window is None for row in rows):
        raise OperationalRuntimeError(
            "selected district is missing its executable proposal"
        )
    return rows


def _selected_proposals(
    plan: PredictiveCityPlan,
) -> tuple[SafePauseProposal, ...]:
    return tuple(
        row.best_window.proposal
        for row in _selected_rows(plan)
        if row.best_window is not None
    )


def controls_from_predictive_plan(
    plan: PredictiveCityPlan,
    proposals: tuple[SafePauseProposal, ...] | None = None,
) -> tuple[PauseControl, ...]:
    """Convert selected proposals using the durable source tick index."""
    if plan.evidence_lineage.tick_index is None:
        raise OperationalRuntimeError(
            "accelerated controls require a durable source tick index"
        )
    proposals = proposals or _selected_proposals(plan)
    controls: list[PauseControl] = []
    for proposal in proposals:
        if (
            proposal.source_snapshot_id != plan.evidence_lineage.snapshot_id
            or proposal.source_tick_id != plan.evidence_lineage.tick_id
            or proposal.simulation_run_id
            != plan.evidence_lineage.simulation_run_id
        ):
            raise OperationalRuntimeError(
                "proposal lineage does not match the accelerated plan"
            )
        grouped: dict[tuple[int, int], list] = {}
        for decision in proposal.driver_decisions:
            grouped.setdefault(
                (
                    decision.pause_start_delay_minutes,
                    decision.pause_duration_minutes,
                ),
                [],
            ).append(decision)
        for (delay, duration), decisions in sorted(grouped.items()):
            control_event_id = canonical_checksum(
                (
                    plan.portfolio_id,
                    proposal.proposal_id,
                    plan.evidence_lineage.snapshot_id,
                )
            )[:32]
            controls.append(
                PauseControl(
                    control_id=canonical_checksum(
                        (control_event_id, delay, duration)
                    )[:32],
                    control_event_id=control_event_id,
                    proposal_id=proposal.proposal_id,
                    driver_ids=tuple(
                        sorted(item.driver_id_hash for item in decisions)
                    ),
                    requested_minute=(
                        (plan.evidence_lineage.tick_index + 1) * 15 + delay
                    ),
                    pause_duration_minutes=duration,
                    max_start_delay_minutes=45,
                    pause_start_delay_minutes=delay,
                    baseline_risk_by_driver=tuple(
                        sorted(
                            (
                                item.driver_id_hash,
                                float(item.baseline_risk),
                            )
                            for item in decisions
                        )
                    ),
                    action_risk_by_driver=tuple(
                        sorted(
                            (
                                item.driver_id_hash,
                                float(item.action_risk),
                            )
                            for item in decisions
                        )
                    ),
                )
            )
    return tuple(sorted(controls, key=lambda item: item.control_id))


@dataclass(frozen=True)
class RepositoryControlQueue:
    """Test/local adapter over the repository's idempotent control store."""

    repository: object

    def queue_plan(
        self,
        plan: PredictiveCityPlan,
        proposals: tuple[SafePauseProposal, ...],
        *,
        execution_id: str,
    ) -> tuple[PauseControl, ...]:
        if not execution_id:
            raise OperationalRuntimeError("control execution identity is required")
        controls = controls_from_predictive_plan(plan, proposals)
        self.repository.queue_controls(controls)
        return controls


@dataclass(frozen=True)
class BigQueryAcceleratedControlQueue:
    """Durable adapter over the existing trusted BigQuery control writer."""

    writer: BigQueryControlWriter

    def queue_plan(
        self,
        plan: PredictiveCityPlan,
        proposals: tuple[SafePauseProposal, ...],
        *,
        execution_id: str,
    ) -> tuple[PauseControl, ...]:
        lineage = plan.evidence_lineage
        if (
            not execution_id
            or lineage.simulation_run_id is None
            or lineage.tick_id is None
        ):
            raise OperationalRuntimeError(
                "durable run/tick and execution identity are required"
            )
        queued = self.writer.queue_many(
            proposal_ids=tuple(
                proposal.proposal_id for proposal in proposals
            ),
            run_id=lineage.simulation_run_id,
            source_tick_id=lineage.tick_id,
            source_snapshot_id=lineage.snapshot_id,
            request_execution_id=execution_id,
        )
        return tuple(
            control
            for item in queued
            for control in item.pause_controls
        )


def _projected_outcomes(
    plan: PredictiveCityPlan,
    *,
    activated: bool,
) -> tuple[ProjectedZoneOutcome, ...]:
    selected = set(plan.selected_zone_ids) if activated else set()
    outcomes: list[ProjectedZoneOutcome] = []
    for row in plan.rows:
        horizon_60 = next(
            item for item in row.horizons if item.minutes_ahead == 60
        )
        horizon_120 = next(
            item for item in row.horizons if item.minutes_ahead == 120
        )
        window = row.best_window if row.zone_id in selected else None
        mandatory_selected = (
            window.proposal.mandatory_selected_drivers
            if window is not None
            else 0
        )
        current_mandatory_after = max(
            0, horizon_60.mandatory_now - mandatory_selected
        )
        baseline_60 = horizon_60.mandatory_now + horizon_60.expected_crossers
        baseline_120 = (
            horizon_120.mandatory_now + horizon_120.expected_crossers
        )
        outcomes.append(
            ProjectedZoneOutcome(
                zone_id=row.zone_id,
                baseline_mandatory_60m=round(baseline_60, 6),
                projected_mandatory_60m=round(
                    current_mandatory_after
                    + (
                        window.projected_mandatory_after_60m
                        if window is not None
                        else horizon_60.expected_crossers
                    ),
                    6,
                ),
                baseline_mandatory_120m=round(baseline_120, 6),
                projected_mandatory_120m=round(
                    current_mandatory_after
                    + (
                        window.projected_mandatory_after_120m
                        if window is not None
                        else horizon_120.expected_crossers
                    ),
                    6,
                ),
                baseline_risk_60m=horizon_60.baseline_expected_risk,
                residual_risk_60m=(
                    window.residual_risk_60m
                    if window is not None
                    else horizon_60.baseline_expected_risk
                ),
                baseline_risk_120m=horizon_120.baseline_expected_risk,
                residual_risk_120m=(
                    window.residual_risk_120m
                    if window is not None
                    else horizon_120.baseline_expected_risk
                ),
            )
        )
    return tuple(outcomes)


def _refresh(
    repository: SimulationRepository,
    scenario_id: str,
) -> SimulationRun | None:
    return repository.refresh_status(scenario_id)


def _accelerated_plan_is_current(
    plan: PredictiveCityPlan,
    repository: SimulationRepository,
) -> bool:
    lineage = plan.evidence_lineage
    if (
        lineage.simulation_run_id is None
        or lineage.tick_id is None
        or lineage.tick_index is None
    ):
        return False
    run = _refresh(repository, lineage.scenario_id)
    if (
        run is None
        or run.run_id != lineage.simulation_run_id
        or run.last_completed_tick_index != lineage.tick_index
    ):
        return False
    tick = getattr(repository, "ticks", {}).get(lineage.tick_id)
    if (
        tick is None
        or tick.run_id != run.run_id
        or tick.tick_index != lineage.tick_index
        or tick.snapshot_id != lineage.snapshot_id
        or tick.status != "SUCCEEDED"
        or tick.simulation_time != lineage.observed_at
    ):
        return False
    validate_persisted_tick_clock(run, tick)
    return True


def _receipt(
    plan: PredictiveCityPlan,
    *,
    choice: str,
    status: str,
    now: datetime,
    controls: tuple[PauseControl, ...] = (),
    intervention_ids: tuple[str, ...] = (),
    error_code: str | None = None,
) -> SimulatedControlReceipt:
    proposals = _selected_proposals(plan) if choice == "ACTIVATE" else ()
    return SimulatedControlReceipt(
        receipt_id=canonical_checksum(
            (plan.portfolio_id, choice, plan.evidence_lineage.snapshot_id)
        )[:32],
        portfolio_id=plan.portfolio_id,
        evidence_lineage=plan.evidence_lineage,
        selected_proposal_checksums=tuple(
            canonical_checksum(proposal.to_dict()) for proposal in proposals
        ),
        controls=controls,
        projected_outcomes=_projected_outcomes(
            plan,
            activated=status
            in {
                "SIMULATED_PROJECTED",
                "SIMULATED_QUEUED",
                "SIMULATED_APPLIED",
            },
        ),
        status=status,
        dispatch_status="NOT_APPLICABLE",
        created_at=now,
        approved_intervention_ids=intervention_ids,
        error_code=error_code,
    )


def activate_simulated_plan(
    plan: PredictiveCityPlan,
    *,
    audit_store: SimulatedAuditStore,
    current_snapshot_id: str | None = None,
    accelerated_repository: SimulationRepository | None = None,
    control_queue: AcceleratedControlQueue | None = None,
    execution_id: str = "",
    now: datetime | None = None,
) -> SimulatedControlReceipt:
    """Activate a plan without any real dispatch boundary."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    mode = plan.mode.upper()
    stale = now > plan.expires_at.astimezone(UTC)
    if mode == "CURRENT":
        stale = stale or current_snapshot_id != plan.evidence_lineage.snapshot_id
    elif mode == "ACCELERATED":
        stale = (
            stale
            or accelerated_repository is None
            or not _accelerated_plan_is_current(
                plan, accelerated_repository
            )
        )
    else:
        return _receipt(
            plan,
            choice="ACTIVATE",
            status="FAILED",
            now=now,
            error_code="UNSUPPORTED_MODE",
        )
    if stale:
        return _receipt(
            plan,
            choice="ACTIVATE",
            status="STALE_PLAN",
            now=now,
            error_code="STALE_PLAN",
        )

    try:
        events = tuple(
            audit_store.approve(proposal)
            for proposal in _selected_proposals(plan)
        )
        controls: tuple[PauseControl, ...] = ()
        status = "SIMULATED_PROJECTED"
        if mode == "ACCELERATED":
            if control_queue is None:
                raise OperationalRuntimeError(
                    "accelerated activation requires a durable control queue"
                )
            controls = control_queue.queue_plan(
                plan,
                _selected_proposals(plan),
                execution_id=execution_id,
            )
            status = "SIMULATED_QUEUED"
        return _receipt(
            plan,
            choice="ACTIVATE",
            status=status,
            now=now,
            controls=tuple(
                sorted(controls, key=lambda item: item.control_id)
            ),
            intervention_ids=tuple(
                sorted(event.intervention_id for event in events)
            ),
        )
    except Exception as exc:
        return _receipt(
            plan,
            choice="ACTIVATE",
            status="FAILED",
            now=now,
            error_code=type(exc).__name__,
        )


def continue_without_intervention(
    plan: PredictiveCityPlan,
    *,
    current_snapshot_id: str | None = None,
    accelerated_repository: SimulationRepository | None = None,
    now: datetime | None = None,
) -> SimulatedControlReceipt:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    stale = now > plan.expires_at.astimezone(UTC)
    if plan.mode.upper() == "CURRENT":
        stale = stale or current_snapshot_id != plan.evidence_lineage.snapshot_id
    elif plan.mode.upper() == "ACCELERATED":
        stale = (
            stale
            or accelerated_repository is None
            or not _accelerated_plan_is_current(
                plan, accelerated_repository
            )
        )
    else:
        return _receipt(
            plan,
            choice="CONTINUE",
            status="FAILED",
            now=now,
            error_code="UNSUPPORTED_MODE",
        )
    return _receipt(
        plan,
        choice="CONTINUE",
        status="STALE_PLAN" if stale else "CONTINUED",
        now=now,
        error_code="STALE_PLAN" if stale else None,
    )


@dataclass(frozen=True)
class AcceleratedAdvanceResult:
    status: str
    run_id: str
    tick_index: int
    tick_id: str | None
    simulation_time: datetime | None
    actual_checksum: str | None
    shadow_checksum: str | None
    actual: TickResult | None = None
    shadow: TickResult | None = None


@dataclass
class DurableAcceleratedRuntime:
    """Advance exactly one caller-observed next tick through durable authority."""

    repository: SimulationRepository
    scorer: object
    scenario_id: str = "heatwave"

    def _run(self) -> SimulationRun:
        run = _refresh(self.repository, self.scenario_id)
        if run is None:
            raise OperationalRuntimeError(
                "accelerated simulation has no durable run"
            )
        return run

    def advance(
        self,
        *,
        expected_tick_index: int,
        execution_id: str,
    ) -> AcceleratedAdvanceResult:
        if not execution_id:
            raise OperationalRuntimeError("tick execution identity is required")
        run = self._run()
        completed = (
            run.last_completed_tick_index
            if run.last_completed_tick_index is not None
            else -1
        )
        if expected_tick_index <= completed:
            return AcceleratedAdvanceResult(
                status="NO_OP_ALREADY_ADVANCED",
                run_id=run.run_id,
                tick_index=completed,
                tick_id=None,
                simulation_time=None,
                actual_checksum=None,
                shadow_checksum=None,
            )
        if expected_tick_index != completed + 1:
            raise OperationalRuntimeError(
                "accelerated catch-up must advance the next missing tick sequentially"
            )
        tick = next(
            (
                item
                for item in getattr(self.repository, "ticks", {}).values()
                if item.run_id == run.run_id
                and item.tick_index == expected_tick_index
            ),
            None,
        )
        if tick is None:
            raise OperationalRuntimeError("next durable tick is missing")
        validate_persisted_tick_clock(run, tick)
        try:
            lease = self.repository.acquire_tick_lease(
                run.run_id, tick.tick_id, execution_id
            )
        except LeaseConflict:
            return AcceleratedAdvanceResult(
                status="NO_OP_LEASE_HELD",
                run_id=run.run_id,
                tick_index=completed,
                tick_id=tick.tick_id,
                simulation_time=tick.simulation_time,
                actual_checksum=None,
                shadow_checksum=None,
            )
        publication: Publication = self.repository.publish_tick(
            run.run_id, tick.tick_id, lease.fencing_token
        )
        persisted = getattr(self.repository, "ticks")[tick.tick_id]
        if persisted.status != "SUCCEEDED":
            try:
                scoring = self.scorer.score(run, publication)
            except Exception:
                self.repository.finalize_score(
                    run.run_id, tick.tick_id, succeeded=False
                )
                raise
            if scoring.durably_finalized:
                self.repository.acknowledge_scoring_commit(
                    run.run_id,
                    tick.tick_id,
                    scoring.prediction_run_id,
                )
            else:
                self.repository.record_scoring_lineage(
                    run.run_id,
                    tick.tick_id,
                    scoring.prediction_run_id,
                )
                self.repository.mark_scored(run.run_id, tick.tick_id)
                self.repository.finalize_score(
                    run.run_id, tick.tick_id, succeeded=True
                )
        refreshed = self._run()
        if refreshed.last_completed_tick_index != expected_tick_index:
            raise SimulationRepositoryError(
                "durable completed cursor did not advance exactly one tick"
            )
        _, shadow = replay_to_tick(run, expected_tick_index, controls=())
        actual = publication.result
        if (
            actual.tick_index != expected_tick_index
            or actual.simulation_time != tick.simulation_time
            or actual.state.minute_index != (expected_tick_index + 1) * 15
            or shadow.simulation_time != actual.simulation_time
        ):
            raise OperationalRuntimeError(
                "actual/shadow operational clocks are not aligned to 15 minutes"
            )
        return AcceleratedAdvanceResult(
            status="ADVANCED",
            run_id=run.run_id,
            tick_index=expected_tick_index,
            tick_id=tick.tick_id,
            simulation_time=actual.simulation_time,
            actual_checksum=actual.checksum,
            shadow_checksum=shadow.checksum,
            actual=actual,
            shadow=shadow,
        )


__all__ = [
    "AcceleratedAdvanceResult",
    "BigQueryAcceleratedControlQueue",
    "DurableAcceleratedRuntime",
    "OperationalRuntimeError",
    "RepositoryControlQueue",
    "activate_simulated_plan",
    "continue_without_intervention",
    "controls_from_predictive_plan",
]
