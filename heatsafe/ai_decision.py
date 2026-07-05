from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

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
    min_action_reduction: float = MIN_ACTION_RISK_REDUCTION,
) -> SafePauseProposal | None:
    selected_count = min(selected_count, len(eligible_ids))
    waves = max(1, min(waves, len(ACTION_DELAYS), selected_count))
    remaining = set(eligible_ids)
    decisions: list[DriverDecision] = []
    plan: list[PauseWave] = []

    for wave_index, size in enumerate(_wave_sizes(selected_count, waves)):
        delay = ACTION_DELAYS[wave_index]
        ranked = sorted(
            (
                actions[(driver_id, delay, pause_minutes)]
                for driver_id in remaining
                if (driver_id, delay, pause_minutes) in actions
            ),
            key=lambda item: (item.risk_reduction, item.baseline_risk),
            reverse=True,
        )
        selected_wave = [item for item in ranked if item.risk_reduction >= min_action_reduction][
            :size
        ]
        if len(selected_wave) != size:
            return None
        high = sum(item.exposure_minutes >= 240 for item in selected_wave)
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
        for item in selected_wave:
            remaining.remove(item.driver_id_hash)
            decisions.append(
                DriverDecision(
                    driver_id_hash=item.driver_id_hash,
                    exposure_minutes=item.exposure_minutes,
                    baseline_risk=item.baseline_risk,
                    action_risk=item.action_risk,
                    pause_start_delay_minutes=delay,
                    pause_duration_minutes=pause_minutes,
                    top_factors=item.top_factors,
                )
            )

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
        violations.append(f"Platform cost exceeds ${budget_cap_vnd / 25_000:,.2f}")
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
    high_selected = sum(item.exposure_minutes >= 240 for item in decisions)
    medium_selected = selected_count - high_selected
    fingerprint = ":".join(
        (
            prediction_run_id,
            zone.zone_id,
            str(selected_count),
            str(pause_minutes),
            str(waves),
            str(budget_cap_vnd),
            str(sponsor_per_driver_vnd),
            ",".join(item.driver_id_hash for item in decisions),
        )
    )
    return SafePauseProposal(
        proposal_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"heatsafe:ai:{fingerprint}")),
        zone_id=zone.zone_id,
        zone_name=zone.name,
        created_at=datetime.now(UTC),
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
            f"BigQuery ML scored {len(baseline_risk)} active drivers; this plan selects "
            f"{selected_count} whose action-conditioned risk falls by at least "
            f"{MIN_ACTION_RISK_REDUCTION:.0%}."
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
    )


def recommend_ai_intervention(
    zone: ZoneSnapshot,
    predictions: tuple[DriverActionPrediction, ...],
    *,
    demand_by_interval: tuple[int, ...],
    upper_demand_by_interval: tuple[int, ...],
    budget_cap_vnd: int = 5_000_000,
    sponsor_per_driver_vnd: int = 8_000,
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
    eligible_ids = sorted(
        (
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
        ),
        key=baseline.get,
        reverse=True,
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
    for pause_minutes in ACTION_DURATIONS:
        for waves in (1, 2, 3, 4):
            for coverage in (0.10, 0.15, 0.20, 0.25, 0.35, 0.50, 0.75, 1.0):
                candidate = _build_candidate(
                    zone,
                    eligible_ids=eligible_ids,
                    selected_count=max(1, math.ceil(len(eligible_ids) * coverage)),
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
                )
                if candidate is not None:
                    candidates.append(candidate)

    feasible = sorted(
        (item for item in candidates if item.within_guardrails),
        key=lambda item: (
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
            message="No AI-scored intervention satisfies all incremental guardrails.",
        )
    return RecommendationResult(
        status="FEASIBLE",
        prediction_run_id=next(iter(run_ids)),
        model_version=next(iter(model_versions)),
        eligible_drivers=len(eligible_ids),
        baseline_expected_risk_events=round(baseline_events, 3),
        recommended=feasible[0],
        alternatives=tuple(feasible[:5]),
        message="AI-scored intervention satisfies all incremental guardrails.",
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
        key=baseline.get,
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
        min_action_reduction=0.0,
    )
