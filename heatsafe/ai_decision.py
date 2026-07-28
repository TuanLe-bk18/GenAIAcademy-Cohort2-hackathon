from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

from .currency import USD_TO_VND
from .models import (
    DriverActionPrediction,
    DriverDecision,
    PauseWave,
    RecommendationResult,
    SafePauseProposal,
    ZoneSnapshot,
)
from .safepause import TRIPS_PER_DRIVER_30M, _simulate_scenario

MIN_BASELINE_RISK = 0.35
MIN_ACTION_RISK_REDUCTION = 0.05
MAX_FULFILLMENT_DEGRADATION = 0.02
MAX_ETA_INCREASE_MINUTES = 2.0
ACTION_DELAYS = (0, 15, 30, 45)
ACTION_DURATIONS = (15, 30)
MANDATORY_EXPOSURE_MINUTES = 240
MANDATORY_PRIORITY_TIER = "MANDATORY_4H"
MODEL_PRIORITY_TIER = "MODEL_ELIGIBLE"
PROJECTED_MANDATORY_PRIORITY_TIER = "PROJECTED_MANDATORY"


def _prediction_index(
    predictions: tuple[DriverActionPrediction, ...],
) -> tuple[dict[str, float], dict[tuple[str, int, int], DriverActionPrediction]]:
    baseline: dict[str, float] = {}
    actions: dict[tuple[str, int, int], DriverActionPrediction] = {}
    for item in predictions:
        baseline[item.driver_id_hash] = item.baseline_risk
        actions[
            (item.driver_id_hash, item.pause_start_delay_minutes, item.pause_duration_minutes)
        ] = item
    return baseline, actions


def _wave_sizes(selected: int, waves: int) -> list[int]:
    base, remainder = divmod(selected, waves)
    return [base + (1 if index < remainder else 0) for index in range(waves)]


def _wait_cost(
    actions: dict[tuple[str, int, int], DriverActionPrediction],
    driver_id: str,
    delay: int,
    duration: int,
) -> float:
    immediate = actions.get((driver_id, 0, duration))
    assigned = actions.get((driver_id, delay, duration))
    if immediate is None or assigned is None:
        return 0.0
    return max(0.0, assigned.action_risk - immediate.action_risk)


def _next_wave_cost(
    actions: dict[tuple[str, int, int], DriverActionPrediction],
    driver_id: str,
    delay: int,
    next_delay: int | None,
    duration: int,
) -> float:
    if next_delay is None:
        return 0.0
    current = actions.get((driver_id, delay, duration))
    delayed = actions.get((driver_id, next_delay, duration))
    if current is None or delayed is None:
        return 0.0
    return max(0.0, delayed.action_risk - current.action_risk)


def _build_candidate(
    zone: ZoneSnapshot,
    *,
    eligible_ids: list[str],
    selected_count: int,
    pause_minutes: int,
    waves: int,
    baseline_risk: dict[str, float],
    actions: dict[tuple[str, int, int], DriverActionPrediction],
    demand_by_interval: tuple[int, ...],
    upper_demand_by_interval: tuple[int, ...],
    budget_cap_vnd: int,
    sponsor_per_driver_vnd: int,
    prediction_run_id: str,
    model_version: str,
    mandatory_ids: set[str] | None = None,
    preventive_ids: set[str] | None = None,
    start_delay_minutes: int = 0,
    min_action_reduction: float = MIN_ACTION_RISK_REDUCTION,
) -> SafePauseProposal | None:
    mandatory_ids = set(mandatory_ids or ())
    preventive_ids = set(preventive_ids or ()) - mandatory_ids
    if not mandatory_ids.issubset(eligible_ids):
        return None
    if not preventive_ids.issubset(eligible_ids):
        return None
    if start_delay_minutes not in ACTION_DELAYS:
        return None
    selected_count = min(selected_count, len(eligible_ids))
    if selected_count < len(mandatory_ids):
        return None
    available_wave_delays = tuple(
        delay for delay in ACTION_DELAYS if delay >= start_delay_minutes
    )
    if waves > len(available_wave_delays):
        return None
    waves = max(1, min(waves, len(available_wave_delays), selected_count))
    exposure_by_driver = {
        driver_id: next(
            (
                item.exposure_minutes
                for key, item in actions.items()
                if key[0] == driver_id
            ),
            0,
        )
        for driver_id in eligible_ids
    }
    mandatory_remaining = sorted(
        mandatory_ids,
        key=lambda driver_id: (
            -baseline_risk[driver_id],
            -exposure_by_driver[driver_id],
            driver_id,
        ),
    )
    optional_remaining = set(eligible_ids) - mandatory_ids
    decisions: list[DriverDecision] = []
    plan: list[PauseWave] = []

    for wave_index, size in enumerate(_wave_sizes(selected_count, waves)):
        delay = available_wave_delays[wave_index]
        next_delay = (
            available_wave_delays[wave_index + 1]
            if wave_index + 1 < waves
            else None
        )
        selected_wave: list[tuple[DriverActionPrediction, str]] = []

        while mandatory_remaining and len(selected_wave) < size:
            driver_id = mandatory_remaining.pop(0)
            item = actions.get((driver_id, delay, pause_minutes))
            if item is None:
                return None
            selected_wave.append((item, MANDATORY_PRIORITY_TIER))

        ranked_optional = sorted(
            (
                actions[(driver_id, delay, pause_minutes)]
                for driver_id in optional_remaining
                if (driver_id, delay, pause_minutes) in actions
            ),
            key=lambda item: (
                item.driver_id_hash not in preventive_ids,
                -_next_wave_cost(
                    actions,
                    item.driver_id_hash,
                    delay,
                    next_delay,
                    pause_minutes,
                ),
                -item.baseline_risk,
                -item.risk_reduction,
                -item.exposure_minutes,
                item.driver_id_hash,
            ),
        )
        optional_slots = size - len(selected_wave)
        selected_optional = [
            item
            for item in ranked_optional
            if item.driver_id_hash in preventive_ids
            or item.risk_reduction >= min_action_reduction
        ][:optional_slots]
        selected_wave.extend(
            (
                item,
                (
                    PROJECTED_MANDATORY_PRIORITY_TIER
                    if item.driver_id_hash in preventive_ids
                    else MODEL_PRIORITY_TIER
                ),
            )
            for item in selected_optional
        )
        if len(selected_wave) != size:
            return None
        high = sum(tier == MANDATORY_PRIORITY_TIER for _, tier in selected_wave)
        plan.append(
            PauseWave(
                wave=wave_index + 1,
                start_minute=delay,
                end_minute=delay + pause_minutes,
                selected_drivers=size,
                high_priority_drivers=high,
                medium_priority_drivers=size - high,
            )
        )
        for item, priority_tier in selected_wave:
            optional_remaining.discard(item.driver_id_hash)
            wait_cost = _wait_cost(
                actions,
                item.driver_id_hash,
                delay,
                pause_minutes,
            )
            if priority_tier == MANDATORY_PRIORITY_TIER:
                assignment_reason = (
                    f"Mandatory safety rule: continuous exposure is at least "
                    f"{MANDATORY_EXPOSURE_MINUTES} minutes; assigned to the earliest "
                    "available wave."
                )
            elif priority_tier == PROJECTED_MANDATORY_PRIORITY_TIER:
                assignment_reason = (
                    "Preventive priority: projected probability of crossing the "
                    "240-minute continuous-exposure threshold is at least 50%."
                )
            else:
                assignment_reason = (
                    f"Model-eligible; waiting until this wave adds {wait_cost:.1%} "
                    "predicted risk versus an immediate pause."
                )
            decisions.append(
                DriverDecision(
                    driver_id_hash=item.driver_id_hash,
                    exposure_minutes=item.exposure_minutes,
                    baseline_risk=item.baseline_risk,
                    action_risk=item.action_risk,
                    pause_start_delay_minutes=delay,
                    pause_duration_minutes=pause_minutes,
                    top_factors=item.top_factors,
                    priority_tier=priority_tier,
                    risk_of_waiting=wait_cost,
                    assignment_reason=assignment_reason,
                )
            )

    if mandatory_remaining:
        return None

    horizon_minutes = max(120, plan[-1].end_minute + 30)
    baseline_p50 = _simulate_scenario(zone, demand_by_interval, (), horizon_minutes)
    baseline_stress = _simulate_scenario(zone, upper_demand_by_interval, (), horizon_minutes)
    action_p50 = _simulate_scenario(zone, demand_by_interval, tuple(plan), horizon_minutes)
    action_stress = _simulate_scenario(
        zone, upper_demand_by_interval, tuple(plan), horizon_minutes
    )

    incremental_unfulfilled = max(
        0, math.ceil(action_p50.action_backlog - baseline_p50.action_backlog)
    )
    capacity_to_reassign = round(
        selected_count * TRIPS_PER_DRIVER_30M * pause_minutes / 30
    )
    earnings_guard = round(
        selected_count * zone.avg_driver_earnings_vnd * pause_minutes / 30
    )
    lost_contribution = incremental_unfulfilled * zone.avg_platform_contribution_vnd
    partner_credit = selected_count * max(0, sponsor_per_driver_vnd)
    net_cost = max(0, earnings_guard + lost_contribution - partner_credit)
    hydration_value = selected_count * 12_000

    violations: list[str] = []
    if net_cost > budget_cap_vnd:
        violations.append(
                    f"Platform cost exceeds ${budget_cap_vnd / USD_TO_VND:,.2f}"
                )
    if (
        baseline_stress.fulfillment_rate - action_stress.fulfillment_rate
        > MAX_FULFILLMENT_DEGRADATION
    ):
        violations.append("Upper-demand fulfillment degradation exceeds 2.0 pp")
    if action_stress.eta_increase_minutes > MAX_ETA_INCREASE_MINUTES:
        violations.append("Upper-demand ETA increase exceeds 2.0 min")
    if not violations:
        violations.append("Meets incremental cost, fulfillment and ETA guardrails")

    baseline_events = sum(baseline_risk.values())
    prevented = sum(item.risk_reduction for item in decisions)
    action_events = max(0.0, baseline_events - prevented)
    high_selected = sum(
        item.priority_tier == MANDATORY_PRIORITY_TIER for item in decisions
    )
    medium_selected = selected_count - high_selected
    fingerprint = ":".join(
        (
            prediction_run_id,
            zone.zone_id,
            str(selected_count),
            str(pause_minutes),
            str(waves),
            str(start_delay_minutes),
            str(budget_cap_vnd),
            str(sponsor_per_driver_vnd),
            ",".join(item.driver_id_hash for item in decisions),
        )
    )
    created_at = datetime.now(UTC)
    return SafePauseProposal(
        proposal_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"heatsafe:ai:{fingerprint}")),
        zone_id=zone.zone_id,
        zone_name=zone.name,
        created_at=created_at,
        source_snapshot_at=zone.observed_at,
        eligible_drivers=len(eligible_ids),
        high_priority_drivers=high_selected,
        medium_priority_drivers=medium_selected,
        selected_drivers=selected_count,
        cohort_coverage=selected_count / len(eligible_ids),
        pause_minutes=pause_minutes,
        waves=waves,
        planned_paused_driver_slots=max(wave.selected_drivers for wave in plan),
        reassigned_trips=capacity_to_reassign,
        missed_trips=incremental_unfulfilled,
        earnings_guard_cost_vnd=earnings_guard,
        partner_sponsorship_vnd=partner_credit,
        lost_contribution_vnd=lost_contribution,
        net_platform_cost_vnd=net_cost,
        partner_hydration_value_vnd=hydration_value,
        exposure_minutes_avoided=selected_count * pause_minutes,
        risk_weighted_minutes_avoided=round(
            sum(item.risk_reduction * item.pause_duration_minutes for item in decisions)
        ),
        simulation_horizon_minutes=horizon_minutes,
        projected_fulfillment_rate=round(action_p50.fulfillment_rate, 4),
        projected_eta_increase_minutes=round(action_p50.eta_increase_minutes, 1),
        p90_fulfillment_rate=round(action_stress.fulfillment_rate, 4),
        p90_eta_increase_minutes=round(action_stress.eta_increase_minutes, 1),
        within_guardrails=len(violations) == 1 and violations[0].startswith("Meets"),
        guardrail_notes=tuple(violations),
        decision_reason=(
            f"Safety-first policy covers {high_selected}/{len(mandatory_ids)} drivers "
            f"with at least {MANDATORY_EXPOSURE_MINUTES} minutes of continuous exposure; "
            "remaining slots prioritize the predicted cost of waiting and baseline risk."
        ),
        wave_plan=tuple(plan),
        prediction_run_id=prediction_run_id,
        model_version=model_version,
        baseline_expected_risk_events=round(baseline_events, 3),
        action_expected_risk_events=round(action_events, 3),
        expected_risk_events_prevented=round(prevented, 3),
        baseline_fulfillment_rate=round(baseline_p50.fulfillment_rate, 4),
        baseline_stress_fulfillment_rate=round(baseline_stress.fulfillment_rate, 4),
        driver_decisions=tuple(decisions),
        mandatory_eligible_drivers=len(mandatory_ids),
        mandatory_selected_drivers=high_selected,
        max_mandatory_delay_minutes=max(
            (
                item.pause_start_delay_minutes
                for item in decisions
                if item.priority_tier == MANDATORY_PRIORITY_TIER
            ),
            default=0,
        ),
        scenario_id=zone.scenario_id,
        source_snapshot_id=zone.snapshot_id,
        simulation_run_id=zone.simulation_run_id,
        source_tick_id=zone.tick_id,
        expires_at=created_at + timedelta(minutes=15),
    )


def recommend_ai_intervention(
    zone: ZoneSnapshot,
    predictions: tuple[DriverActionPrediction, ...],
    *,
    demand_by_interval: tuple[int, ...],
    upper_demand_by_interval: tuple[int, ...],
    budget_cap_vnd: int = 5_000_000,
    sponsor_per_driver_vnd: int = 8_000,
    candidate_start_delays: tuple[int, ...] = (0,),
    preventive_ids: frozenset[str] = frozenset(),
    allowed_driver_ids: frozenset[str] | None = None,
    candidate_waves: tuple[int, ...] = (1, 2, 3, 4),
) -> RecommendationResult:
    if not predictions:
        return RecommendationResult(
            status="MODEL_UNAVAILABLE",
            prediction_run_id=None,
            model_version=None,
            eligible_drivers=0,
            baseline_expected_risk_events=0.0,
            recommended=None,
            message="AI predictions are unavailable; monitoring only.",
        )
    run_ids = {item.prediction_run_id for item in predictions}
    model_versions = {item.model_version for item in predictions}
    snapshots = {item.snapshot_id for item in predictions}
    if len(run_ids) != 1 or len(model_versions) != 1 or snapshots != {zone.snapshot_id}:
        return RecommendationResult(
            status="MODEL_UNAVAILABLE",
            prediction_run_id=None,
            model_version=None,
            eligible_drivers=0,
            baseline_expected_risk_events=0.0,
            recommended=None,
            message="AI predictions do not match the active snapshot; monitoring only.",
        )

    baseline, actions = _prediction_index(predictions)
    exposure_by_driver: dict[str, int] = {}
    for item in predictions:
        exposure_by_driver[item.driver_id_hash] = item.exposure_minutes
    if allowed_driver_ids is not None:
        allowed = set(allowed_driver_ids)
        baseline = {
            driver_id: risk
            for driver_id, risk in baseline.items()
            if driver_id in allowed
        }
        actions = {
            key: value for key, value in actions.items() if key[0] in allowed
        }
        exposure_by_driver = {
            driver_id: exposure
            for driver_id, exposure in exposure_by_driver.items()
            if driver_id in allowed
        }
    mandatory_ids = {
        driver_id
        for driver_id, exposure in exposure_by_driver.items()
        if exposure >= MANDATORY_EXPOSURE_MINUTES
    }
    preventive_ids = (
        frozenset(preventive_ids) & frozenset(exposure_by_driver)
    ) - mandatory_ids
    missing_mandatory_actions = {
        driver_id
        for driver_id in mandatory_ids
        if any(
            (driver_id, delay, duration) not in actions
            for duration in ACTION_DURATIONS
            for delay in ACTION_DELAYS
        )
    }
    if missing_mandatory_actions:
        return RecommendationResult(
            status="MODEL_UNAVAILABLE",
            prediction_run_id=next(iter(run_ids)),
            model_version=next(iter(model_versions)),
            eligible_drivers=0,
            baseline_expected_risk_events=round(sum(baseline.values()), 3),
            recommended=None,
            message=(
                "Mandatory 4h+ driver action predictions are incomplete; "
                "monitoring only."
            ),
        )
    eligible_ids = sorted(
        mandatory_ids
        | set(preventive_ids)
        | {
            driver_id
            for driver_id, risk in baseline.items()
            if risk >= MIN_BASELINE_RISK
            and max(
                (
                    item.risk_reduction
                    for key, item in actions.items()
                    if key[0] == driver_id
                ),
                default=0.0,
            )
            >= MIN_ACTION_RISK_REDUCTION
        },
        key=lambda driver_id: (
            driver_id not in mandatory_ids,
            driver_id not in preventive_ids,
            -baseline[driver_id],
            -exposure_by_driver[driver_id],
            driver_id,
        ),
    )
    baseline_events = sum(baseline.values())
    if not eligible_ids:
        return RecommendationResult(
            status="NO_FEASIBLE",
            prediction_run_id=next(iter(run_ids)),
            model_version=next(iter(model_versions)),
            eligible_drivers=0,
            baseline_expected_risk_events=round(baseline_events, 3),
            recommended=None,
            message="The model found no driver with sufficient predicted risk reduction.",
        )

    candidates: list[SafePauseProposal] = []
    selected_counts = {
        max(1, math.ceil(len(eligible_ids) * coverage))
        for coverage in (0.10, 0.15, 0.20, 0.25, 0.35, 0.50, 0.75, 1.0)
    }
    if mandatory_ids:
        selected_counts.add(len(mandatory_ids))
    if preventive_ids:
        selected_counts.add(len(mandatory_ids | set(preventive_ids)))
    for pause_minutes in ACTION_DURATIONS:
        for start_delay in candidate_start_delays:
            for waves in candidate_waves:
                for selected_count in sorted(selected_counts):
                    candidate = _build_candidate(
                        zone,
                        eligible_ids=eligible_ids,
                        selected_count=selected_count,
                        pause_minutes=pause_minutes,
                        waves=waves,
                        baseline_risk=baseline,
                        actions=actions,
                        demand_by_interval=demand_by_interval,
                        upper_demand_by_interval=upper_demand_by_interval,
                        budget_cap_vnd=budget_cap_vnd,
                        sponsor_per_driver_vnd=sponsor_per_driver_vnd,
                        prediction_run_id=next(iter(run_ids)),
                        model_version=next(iter(model_versions)),
                        mandatory_ids=mandatory_ids,
                        preventive_ids=set(preventive_ids),
                        start_delay_minutes=start_delay,
                    )
                    if candidate is not None:
                        candidates.append(candidate)

    feasible = sorted(
        (item for item in candidates if item.within_guardrails),
        key=lambda item: (
            item.max_mandatory_delay_minutes,
            sum(
                decision.pause_start_delay_minutes
                for decision in item.driver_decisions
                if decision.priority_tier == MANDATORY_PRIORITY_TIER
            ),
            -item.expected_risk_events_prevented,
            -item.selected_drivers,
            item.p90_eta_increase_minutes,
            item.net_platform_cost_vnd,
        ),
    )
    if not feasible:
        least_violating = sorted(
            candidates,
            key=lambda item: (
                item.max_mandatory_delay_minutes,
                sum(
                    decision.pause_start_delay_minutes
                    for decision in item.driver_decisions
                    if decision.priority_tier == MANDATORY_PRIORITY_TIER
                ),
                len(item.guardrail_notes),
                item.p90_eta_increase_minutes,
                -item.p90_fulfillment_rate,
                item.net_platform_cost_vnd,
            ),
        )[:5]
        return RecommendationResult(
            status="NO_FEASIBLE",
            prediction_run_id=next(iter(run_ids)),
            model_version=next(iter(model_versions)),
            eligible_drivers=len(eligible_ids),
            baseline_expected_risk_events=round(baseline_events, 3),
            recommended=None,
            alternatives=tuple(least_violating),
            message=(
                "No plan can cover every mandatory 4h+ driver within all operational "
                "guardrails."
                if mandatory_ids
                else "No AI-scored intervention satisfies all incremental guardrails."
            ),
        )
    return RecommendationResult(
        status="FEASIBLE",
        prediction_run_id=next(iter(run_ids)),
        model_version=next(iter(model_versions)),
        eligible_drivers=len(eligible_ids),
        baseline_expected_risk_events=round(baseline_events, 3),
        recommended=feasible[0],
        alternatives=tuple(feasible[:5]),
        message=(
            f"Safety-first plan covers all {len(mandatory_ids)} mandatory 4h+ drivers "
            "and satisfies all incremental guardrails."
            if mandatory_ids
            else "AI-scored intervention satisfies all incremental guardrails."
        ),
    )


def evaluate_rule_reference(
    zone: ZoneSnapshot,
    predictions: tuple[DriverActionPrediction, ...],
    *,
    demand_by_interval: tuple[int, ...],
    upper_demand_by_interval: tuple[int, ...],
    budget_cap_vnd: int,
    sponsor_per_driver_vnd: int,
) -> SafePauseProposal | None:
    if not predictions:
        return None
    baseline, actions = _prediction_index(predictions)
    rule_ids = sorted(
        {
            item.driver_id_hash
            for item in predictions
            if item.exposure_minutes >= 120
        },
        key=lambda driver_id: baseline[driver_id],
        reverse=True,
    )
    if not rule_ids:
        return None
    return _build_candidate(
        zone,
        eligible_ids=rule_ids,
        selected_count=len(rule_ids),
        pause_minutes=30,
        waves=4,
        baseline_risk=baseline,
        actions=actions,
        demand_by_interval=demand_by_interval,
        upper_demand_by_interval=upper_demand_by_interval,
        budget_cap_vnd=budget_cap_vnd,
        sponsor_per_driver_vnd=sponsor_per_driver_vnd,
        prediction_run_id=predictions[0].prediction_run_id,
        model_version=predictions[0].model_version,
        mandatory_ids={
            driver_id
            for driver_id in rule_ids
            if next(
                item.exposure_minutes
                for item in predictions
                if item.driver_id_hash == driver_id
            )
            >= MANDATORY_EXPOSURE_MINUTES
        },
        min_action_reduction=0.0,
    )
