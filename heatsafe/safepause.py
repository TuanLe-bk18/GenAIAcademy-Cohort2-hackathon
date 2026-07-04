from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

from .models import SafePauseProposal, ZoneSnapshot
from .risk import eligible_driver_count


def simulate_safepause(
    zone: ZoneSnapshot,
    *,
    pause_minutes: int = 20,
    waves: int = 3,
    budget_cap_vnd: int = 1_000_000,
    sponsor_per_driver_vnd: int = 8_000,
    min_fulfillment_rate: float = 0.95,
    max_eta_increase_minutes: float = 2.0,
) -> SafePauseProposal:
    """Estimate a staggered pause; values are operational scenarios, not causal claims."""
    eligible = eligible_driver_count(zone)
    waves = max(1, min(waves, max(eligible, 1)))
    planned_slots = math.ceil(eligible / waves) if eligible else 0

    trips_per_driver_during_pause = 0.75 * (pause_minutes / 30)
    planned_paused_trips = math.ceil(eligible * trips_per_driver_during_pause)
    # "Fresh" drivers are already part of active supply, so only forecast slack can
    # absorb paused work. Counting all fresh drivers as spare would overstate capacity.
    reserve_drivers = max(0, zone.fresh_drivers - math.ceil(zone.active_drivers * 0.05))
    forecast_slack_trips = max(0, zone.active_drivers - zone.forecast_requests_30m)
    absorbable_trips = min(math.floor(reserve_drivers * 0.75), forecast_slack_trips)
    reassigned_trips = min(planned_paused_trips, absorbable_trips)
    missed_trips = max(0, planned_paused_trips - reassigned_trips)

    earnings_guard_cost = missed_trips * zone.avg_driver_earnings_vnd
    partner_sponsorship = min(
        eligible * sponsor_per_driver_vnd,
        earnings_guard_cost,
    )
    lost_contribution = missed_trips * zone.avg_platform_contribution_vnd
    net_cost = max(0, earnings_guard_cost + lost_contribution - partner_sponsorship)
    hydration_value = eligible * 12_000

    requests = max(zone.forecast_requests_30m, 1)
    fulfillment = max(0.0, 1 - (missed_trips / requests))
    eta_increase = round(min(10.0, (missed_trips / requests) * 20), 1)

    notes: list[str] = []
    if net_cost > budget_cap_vnd:
        notes.append(f"Exceeds budget of ${budget_cap_vnd / 25000:,.2f}")
    if fulfillment < min_fulfillment_rate:
        notes.append(f"Fulfillment below {min_fulfillment_rate:.0%}")
    if eta_increase > max_eta_increase_minutes:
        notes.append(f"ETA increase exceeds {max_eta_increase_minutes:.1f} min")
    if eligible == 0:
        notes.append("No eligible cohort")
    if not notes:
        notes.append("Meets cost, fulfillment and ETA guardrails")

    now = datetime.now(UTC)
    return SafePauseProposal(
        proposal_id=str(uuid.uuid4()),
        zone_id=zone.zone_id,
        zone_name=zone.name,
        created_at=now,
        source_snapshot_at=zone.observed_at,
        eligible_drivers=eligible,
        pause_minutes=pause_minutes,
        waves=waves,
        planned_paused_driver_slots=planned_slots,
        reassigned_trips=reassigned_trips,
        missed_trips=missed_trips,
        earnings_guard_cost_vnd=earnings_guard_cost,
        partner_sponsorship_vnd=partner_sponsorship,
        lost_contribution_vnd=lost_contribution,
        net_platform_cost_vnd=net_cost,
        partner_hydration_value_vnd=hydration_value,
        exposure_minutes_avoided=eligible * pause_minutes,
        projected_fulfillment_rate=fulfillment,
        projected_eta_increase_minutes=eta_increase,
        within_guardrails=len(notes) == 1 and notes[0].startswith("Meets"),
        guardrail_notes=tuple(notes),
    )
