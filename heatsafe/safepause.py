from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .currency import USD_TO_VND
from .models import PauseWave, SafePauseProposal, ZoneSnapshot
from .risk import eligible_driver_cohorts

SIMULATION_STEP_MINUTES = 5
FORECAST_INTERVAL_MINUTES = 15
TRIPS_PER_DRIVER_30M = 1.0


@dataclass(frozen=True)
class _ScenarioResult:
    demand: float
    baseline_backlog: float
    action_backlog: float
    fulfillment_rate: float
    eta_increase_minutes: float


def _expand_demand(
    demand_by_interval: tuple[int, ...], horizon_minutes: int
) -> list[float]:
    slots = math.ceil(horizon_minutes / SIMULATION_STEP_MINUTES)
    source = demand_by_interval or (0,)
    return [
        source[min(slot // (FORECAST_INTERVAL_MINUTES // SIMULATION_STEP_MINUTES), len(source) - 1)]
        / (FORECAST_INTERVAL_MINUTES // SIMULATION_STEP_MINUTES)
        for slot in range(slots)
    ]


def _wave_plan(
    high_priority: int,
    medium_priority: int,
    selected: int,
    pause_minutes: int,
    waves: int,
) -> tuple[PauseWave, ...]:
    waves = max(1, min(waves, max(selected, 1)))
    base, remainder = divmod(selected, waves)
    remaining_high = min(high_priority, selected)
    plan: list[PauseWave] = []
    for index in range(waves):
        size = base + (1 if index < remainder else 0)
        wave_high = min(size, remaining_high)
        remaining_high -= wave_high
        plan.append(
            PauseWave(
                wave=index + 1,
                start_minute=index * pause_minutes,
                end_minute=(index + 1) * pause_minutes,
                selected_drivers=size,
                high_priority_drivers=wave_high,
                medium_priority_drivers=size - wave_high,
            )
        )
    return tuple(plan)


def _simulate_scenario(
    zone: ZoneSnapshot,
    demand_by_interval: tuple[int, ...],
    plan: tuple[PauseWave, ...],
    horizon_minutes: int,
) -> _ScenarioResult:
    demand_slots = _expand_demand(demand_by_interval, horizon_minutes)
    baseline_backlog = 0.0
    action_backlog = 0.0
    max_baseline_backlog = 0.0
    max_action_backlog = 0.0
    capacity_per_driver = TRIPS_PER_DRIVER_30M * SIMULATION_STEP_MINUTES / 30

    for slot, demand in enumerate(demand_slots):
        minute = slot * SIMULATION_STEP_MINUTES
        paused = sum(
            wave.selected_drivers
            for wave in plan
            if wave.start_minute <= minute < wave.end_minute
        )
        baseline_capacity = zone.active_drivers * capacity_per_driver
        action_capacity = max(0, zone.active_drivers - paused) * capacity_per_driver

        baseline_available = baseline_backlog + demand
        baseline_served = min(baseline_available, baseline_capacity)
        baseline_backlog = baseline_available - baseline_served

        action_available = action_backlog + demand
        served = min(action_available, action_capacity)
        action_backlog = action_available - served
        max_baseline_backlog = max(max_baseline_backlog, baseline_backlog)
        max_action_backlog = max(max_action_backlog, action_backlog)

    total_demand = sum(demand_slots)
    fulfillment = (
        1.0
        if total_demand <= 0
        else max(0.0, min(1.0, (total_demand - action_backlog) / total_demand))
    )
    baseline_capacity_per_minute = max(
        zone.active_drivers * TRIPS_PER_DRIVER_30M / 30, 0.01
    )
    incremental_backlog = max(0.0, max_action_backlog - max_baseline_backlog)
    eta_increase = min(10.0, incremental_backlog / baseline_capacity_per_minute)
    return _ScenarioResult(
        demand=total_demand,
        baseline_backlog=baseline_backlog,
        action_backlog=action_backlog,
        fulfillment_rate=fulfillment,
        eta_increase_minutes=eta_increase,
    )


def simulate_safepause(
    zone: ZoneSnapshot,
    *,
    pause_minutes: int = 20,
    waves: int = 3,
    cohort_coverage: float = 1.0,
    demand_by_interval: tuple[int, ...] | None = None,
    upper_demand_by_interval: tuple[int, ...] | None = None,
    budget_cap_vnd: int = 1_000_000,
    sponsor_per_driver_vnd: int = 8_000,
    min_fulfillment_rate: float = 0.95,
    max_eta_increase_minutes: float = 2.0,
) -> SafePauseProposal:
    """Run a deterministic interval simulation for one SafePause candidate."""
    high_priority, medium_priority = eligible_driver_cohorts(zone)
    eligible = high_priority + medium_priority
    cohort_coverage = max(0.0, min(1.0, cohort_coverage))
    requested = math.ceil(eligible * cohort_coverage)
    selected = min(eligible, max(min(high_priority, eligible), requested)) if eligible else 0
    pause_minutes = max(5, min(60, pause_minutes))
    waves = max(1, min(waves, max(selected, 1)))
    plan = _wave_plan(high_priority, medium_priority, selected, pause_minutes, waves)
    horizon_minutes = max(60, waves * pause_minutes + 30)

    if demand_by_interval is None:
        first = zone.forecast_requests_30m // 2
        demand_by_interval = (first, zone.forecast_requests_30m - first)
    if upper_demand_by_interval is None:
        upper_demand_by_interval = tuple(math.ceil(value * 1.15) for value in demand_by_interval)

    p50 = _simulate_scenario(zone, demand_by_interval, plan, horizon_minutes)
    p90 = _simulate_scenario(zone, upper_demand_by_interval, plan, horizon_minutes)
    incremental_missed = max(
        0, math.ceil(p50.action_backlog - p50.baseline_backlog)
    )
    paused_trip_capacity = selected * TRIPS_PER_DRIVER_30M * pause_minutes / 30
    reassigned_trips = max(0, round(paused_trip_capacity) - incremental_missed)

    earnings_guard_cost = incremental_missed * zone.avg_driver_earnings_vnd
    partner_sponsorship = min(
        selected * max(0, sponsor_per_driver_vnd), earnings_guard_cost
    )
    lost_contribution = incremental_missed * zone.avg_platform_contribution_vnd
    net_cost = max(0, earnings_guard_cost + lost_contribution - partner_sponsorship)
    hydration_value = selected * 12_000
    high_selected = min(selected, high_priority)
    medium_selected = max(0, selected - high_selected)
    exposure_minutes = selected * pause_minutes
    risk_weighted_minutes = (high_selected * 2 + medium_selected) * pause_minutes

    notes: list[str] = []
    if net_cost > budget_cap_vnd:
        notes.append(f"Exceeds budget of ${budget_cap_vnd / USD_TO_VND:,.2f}")
    if p90.fulfillment_rate < min_fulfillment_rate:
        notes.append(f"P90 fulfillment below {min_fulfillment_rate:.0%}")
    if p90.eta_increase_minutes > max_eta_increase_minutes:
        notes.append(f"P90 ETA increase exceeds {max_eta_increase_minutes:.1f} min")
    if selected == 0:
        notes.append("No eligible cohort")
    if not notes:
        notes.append("Meets P90 cost, fulfillment and ETA guardrails")

    reason = (
        f"Prioritizes {high_selected} drivers active 4h+, then {medium_selected} active 2-4h; "
        f"validated over {horizon_minutes} minutes against median and upper-bound demand."
    )
    proposal_fingerprint = ":".join(
        (
            zone.snapshot_id,
            zone.zone_id,
            str(selected),
            str(pause_minutes),
            str(waves),
            str(budget_cap_vnd),
            str(sponsor_per_driver_vnd),
            ",".join(map(str, demand_by_interval)),
            ",".join(map(str, upper_demand_by_interval)),
        )
    )
    created_at = datetime.now(UTC)
    return SafePauseProposal(
        proposal_id=str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"heatsafe:proposal:{proposal_fingerprint}")
        ),
        zone_id=zone.zone_id,
        zone_name=zone.name,
        created_at=created_at,
        source_snapshot_at=zone.observed_at,
        eligible_drivers=eligible,
        high_priority_drivers=high_priority,
        medium_priority_drivers=medium_priority,
        selected_drivers=selected,
        cohort_coverage=0.0 if eligible == 0 else selected / eligible,
        pause_minutes=pause_minutes,
        waves=waves,
        planned_paused_driver_slots=max((wave.selected_drivers for wave in plan), default=0),
        reassigned_trips=reassigned_trips,
        missed_trips=incremental_missed,
        earnings_guard_cost_vnd=earnings_guard_cost,
        partner_sponsorship_vnd=partner_sponsorship,
        lost_contribution_vnd=lost_contribution,
        net_platform_cost_vnd=net_cost,
        partner_hydration_value_vnd=hydration_value,
        exposure_minutes_avoided=exposure_minutes,
        risk_weighted_minutes_avoided=risk_weighted_minutes,
        simulation_horizon_minutes=horizon_minutes,
        projected_fulfillment_rate=round(p50.fulfillment_rate, 4),
        projected_eta_increase_minutes=round(p50.eta_increase_minutes, 1),
        p90_fulfillment_rate=round(p90.fulfillment_rate, 4),
        p90_eta_increase_minutes=round(p90.eta_increase_minutes, 1),
        within_guardrails=len(notes) == 1 and notes[0].startswith("Meets"),
        guardrail_notes=tuple(notes),
        decision_reason=reason,
        wave_plan=plan,
        scenario_id=zone.scenario_id,
        source_snapshot_id=zone.snapshot_id,
        simulation_run_id=zone.simulation_run_id,
        source_tick_id=zone.tick_id,
        expires_at=created_at + timedelta(minutes=15),
    )


def recommend_safepause(
    zone: ZoneSnapshot,
    *,
    demand_by_interval: tuple[int, ...],
    upper_demand_by_interval: tuple[int, ...],
    budget_cap_vnd: int = 1_000_000,
    sponsor_per_driver_vnd: int = 8_000,
) -> tuple[SafePauseProposal, tuple[SafePauseProposal, ...]]:
    """Enumerate explainable candidates and choose safety-first robust action."""
    candidates = tuple(
        simulate_safepause(
            zone,
            pause_minutes=pause_minutes,
            waves=waves,
            cohort_coverage=coverage,
            demand_by_interval=demand_by_interval,
            upper_demand_by_interval=upper_demand_by_interval,
            budget_cap_vnd=budget_cap_vnd,
            sponsor_per_driver_vnd=sponsor_per_driver_vnd,
        )
        for pause_minutes in (10, 15, 20, 25, 30)
        for waves in (2, 3, 4, 5)
        for coverage in (0.5, 0.75, 1.0)
    )
    ranked = tuple(
        sorted(
            candidates,
            key=lambda item: (
                not item.within_guardrails,
                -item.risk_weighted_minutes_avoided,
                -item.selected_drivers,
                -item.p90_fulfillment_rate,
                item.p90_eta_increase_minutes,
                item.net_platform_cost_vnd,
                item.waves,
            ),
        )
    )
    return ranked[0], ranked
