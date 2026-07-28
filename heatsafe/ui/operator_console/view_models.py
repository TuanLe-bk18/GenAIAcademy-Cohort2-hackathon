"""Immutable, Streamlit-free operator console presentation models.

The builders in this module only read authoritative domain objects. They never run the
optimizer, choose a proposal, or alter an action payload.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from heatsafe.ai_decision import (
    MAX_ETA_INCREASE_MINUTES,
    MAX_FULFILLMENT_DEGRADATION,
)
from heatsafe.models import (
    DecisionConstraints,
    PredictiveCityPlan,
    PredictiveZonePlanRow,
    SafePauseProposal,
    ZoneSnapshot,
)

from .vocabulary import (
    format_currency_vnd,
    format_duration,
    format_freshness,
    format_hanoi_after,
    format_hanoi_time,
    format_heat_state,
    format_mode_label,
    format_plan_status_label,
    format_readiness_label,
    format_risk_level,
)

MAX_AREAS = 10
MAX_PRIORITY_AREAS = 3
MAX_TIMING_OPTIONS = 4
MAX_PORTFOLIO_OPTIONS = 12
MAX_DRIVER_ROWS = 20
MAX_HISTORY_ROWS = 10


@dataclass(frozen=True)
class OperatorKpiView:
    label: str
    value: str
    detail: str
    state: str


@dataclass(frozen=True)
class OperatorCityKpis:
    drivers_needing_break_now: int
    covered_drivers: int
    total_drivers_requiring_coverage: int
    budget_remaining_usd: float | None
    coverage_state: str
    budget_remaining_label: str

    @property
    def cards(self) -> tuple[OperatorKpiView, OperatorKpiView, OperatorKpiView]:
        return (
            OperatorKpiView(
                label="Drivers needing a break now",
                value=f"{self.drivers_needing_break_now:,}",
                detail="Across Hanoi",
                state="critical" if self.drivers_needing_break_now else "safe",
            ),
            OperatorKpiView(
                label="Safety coverage",
                value=(
                    f"{self.covered_drivers:,} / "
                    f"{self.total_drivers_requiring_coverage:,}"
                ),
                detail=self.coverage_state,
                state="safe" if self.coverage_state == "All covered" else "warning",
            ),
            OperatorKpiView(
                label="Budget remaining after this plan",
                value=self.budget_remaining_label,
                detail="Includes the high-demand case",
                state=(
                    "neutral"
                    if self.budget_remaining_usd is None
                    else "safe" if self.budget_remaining_usd >= 0 else "critical"
                ),
            ),
        )


@dataclass(frozen=True)
class OperatorAreaView:
    zone_id: str
    name: str
    latitude: float
    longitude: float
    active_drivers: int
    heat_index_c: float
    heat_state_label: str
    drivers_needing_break_now: int
    expected_needing_protection_by_label: str
    expected_needing_protection_count: int | None
    recommended_start_label: str
    plan_status_label: str
    selected: bool
    included_in_plan: bool
    priority_order: int | None = None
    exposed_2h: int = 0
    forecast_requests_30m: int = 0


@dataclass(frozen=True)
class OperatorGuardrailView:
    label: str
    value: str
    status_label: str
    passed: bool


@dataclass(frozen=True)
class OperatorRecommendationView:
    state: str
    headline: str
    explanation: str
    driver_count: int
    start_time_label: str
    group_summary: str
    break_length_label: str
    coverage_summary: str
    order_impact_summary: str
    pickup_delay_summary: str
    cost_summary: str
    guardrails: tuple[OperatorGuardrailView, ...]
    can_activate: bool
    blocking_reason: str


@dataclass(frozen=True)
class OperatorTimingOptionView:
    start_time: datetime
    start_time_label: str
    selected: bool
    feasible: bool
    drivers_protected: int
    projected_drivers_at_limit: float | None
    pause_minutes: int
    expected_demand: int | None = None
    high_demand: int | None = None
    rejection_reason: str = ""


@dataclass(frozen=True)
class OperatorPortfolioOptionView:
    label: str
    selected: bool
    feasible: bool
    protected_drivers: int
    exposure_hours_avoided: float
    high_demand_cost_usd: float
    pickup_delay_minutes: float
    coverage_summary: str
    rejection_reason: str = ""


@dataclass(frozen=True)
class OperatorStressMetricView:
    label: str
    expected_value: float
    high_demand_value: float
    limit_value: float | None
    unit: str
    expected_label: str
    high_demand_label: str
    passed: bool


@dataclass(frozen=True)
class OperatorOutcomePoint:
    at: datetime
    with_safepause: float
    without_safepause: float


@dataclass(frozen=True)
class OperatorOutcomeView:
    points: tuple[OperatorOutcomePoint, ...]
    metric_label: str = "Heat-exposure burden"
    unit: str = "driver-hours"
    summary: str = ""

    @property
    def available(self) -> bool:
        return len(self.points) >= 2


@dataclass(frozen=True)
class OperatorDecisionInsightsView:
    timing_options: tuple[OperatorTimingOptionView, ...]
    portfolio_options: tuple[OperatorPortfolioOptionView, ...]
    stress_metrics: tuple[OperatorStressMetricView, ...]
    outcome: OperatorOutcomeView | None
    evaluated_option_label: str
    selected_start_label: str
    budget_limit_usd: float

    @property
    def outcome_available(self) -> bool:
        return self.outcome is not None and self.outcome.available

    @property
    def available_views(self) -> tuple[str, ...]:
        base = ("Timing", "Trade-offs", "Stress test")
        return (*base, "Outcome") if self.outcome_available else base


@dataclass(frozen=True)
class OperatorTable:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]

    def __post_init__(self) -> None:
        width = len(self.columns)
        if any(len(row) != width for row in self.rows):
            raise ValueError("every operator table row must match its column contract")

    def as_records(self) -> list[dict[str, object]]:
        return [dict(zip(self.columns, row)) for row in self.rows]


@dataclass(frozen=True)
class OperatorEvidenceSummary:
    areas: OperatorTable
    drivers: OperatorTable
    history: OperatorTable


@dataclass(frozen=True)
class OperatorConsoleView:
    mode_label: str
    operational_time_label: str
    updated_label: str
    readiness_state: str
    synthetic_disclosure: str
    city_kpis: OperatorCityKpis
    map_areas: tuple[OperatorAreaView, ...]
    priority_areas: tuple[OperatorAreaView, ...]
    selected_area: OperatorAreaView | None
    recommendation: OperatorRecommendationView
    decision_insights: OperatorDecisionInsightsView
    evidence_summary: OperatorEvidenceSummary


EMPTY_RECOMMENDATION = OperatorRecommendationView(
    state="unavailable",
    headline="Recommendation temporarily unavailable",
    explanation="City heat and driver monitoring are still available.",
    driver_count=0,
    start_time_label="—",
    group_summary="—",
    break_length_label="—",
    coverage_summary="Monitoring continues",
    order_impact_summary="—",
    pickup_delay_summary="—",
    cost_summary="—",
    guardrails=(),
    can_activate=False,
    blocking_reason="Action is paused until current evidence is verified.",
)


def _horizon(row: PredictiveZonePlanRow, minutes: int) -> Any | None:
    return next(
        (item for item in row.horizons if item.minutes_ahead == minutes),
        None,
    )


def _plan_time(plan: PredictiveCityPlan | None, zones: Sequence[ZoneSnapshot]) -> datetime:
    if plan is not None:
        return plan.evidence_lineage.observed_at
    if zones:
        return max(zone.observed_at for zone in zones)
    return datetime.now(UTC)


def _plain_reason(value: object) -> str:
    text = str(value or "").lower()
    if not text:
        return ""
    if "budget" in text or "cost" in text:
        return "Blocked by the budget limit"
    if "eta" in text or "pickup" in text:
        return "Blocked by the pickup-delay limit"
    if "fulfillment" in text or "service" in text:
        return "Blocked by the orders-completed limit"
    if "unavailable" in text or "evidence" in text:
        return "Current evidence is unavailable"
    return "Blocked by an operational limit"


def _get(value: object, field: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _operator_areas(
    plan: PredictiveCityPlan | None,
    zones: Sequence[ZoneSnapshot],
    *,
    selected_zone_id: str | None,
) -> tuple[OperatorAreaView, ...]:
    zone_by_id = {zone.zone_id: zone for zone in zones}
    at = _plan_time(plan, zones)
    endpoint = format_hanoi_after(at, 120)
    result: list[OperatorAreaView] = []

    if plan is None:
        source_rows: Sequence[PredictiveZonePlanRow | None] = [None] * min(
            len(zones), MAX_AREAS
        )
        ordered_zones = sorted(zones, key=lambda item: item.name)[:MAX_AREAS]
        pairs = zip(source_rows, ordered_zones)
    else:
        rows = tuple(plan.rows[:MAX_AREAS])
        missing = [row.zone_id for row in rows if row.zone_id not in zone_by_id]
        if missing:
            raise ValueError("city plan and area evidence do not match")
        pairs = ((row, zone_by_id[row.zone_id]) for row in rows)

    for row, zone in pairs:
        now = _horizon(row, 0) if row is not None else None
        future = _horizon(row, 120) if row is not None else None
        window = row.best_window if row is not None else None
        mandatory_now = (
            int(now.mandatory_now) if now is not None else max(0, zone.exposed_4h)
        )
        future_count = int(future.projected_mandatory) if future is not None else None
        status = row.portfolio_status if row is not None else "UNAVAILABLE"
        result.append(
            OperatorAreaView(
                zone_id=zone.zone_id,
                name=zone.name,
                latitude=zone.latitude,
                longitude=zone.longitude,
                active_drivers=zone.active_drivers,
                heat_index_c=float(
                    now.heat.heat_index_c if now is not None else zone.heat_index_c
                ),
                heat_state_label=format_heat_state(
                    now.heat.heat_index_c if now is not None else zone.heat_index_c
                ),
                drivers_needing_break_now=mandatory_now,
                expected_needing_protection_by_label=(
                    f"{future_count:,} by {endpoint}" if future_count is not None else "Updating"
                ),
                expected_needing_protection_count=future_count,
                recommended_start_label=(
                    format_hanoi_after(at, window.start_delay_minutes)
                    if window is not None
                    else "Not scheduled"
                ),
                plan_status_label=format_plan_status_label(status),
                selected=zone.zone_id == selected_zone_id,
                included_in_plan=(
                    plan is not None and zone.zone_id in set(plan.selected_zone_ids)
                ),
                priority_order=(row.future_safety_rank if row is not None else None),
                exposed_2h=max(0, int(zone.exposed_2h)),
                forecast_requests_30m=max(0, int(zone.forecast_requests_30m)),
            )
        )
    return tuple(result)


def _select_area(
    areas: tuple[OperatorAreaView, ...], selected_zone_id: str | None
) -> tuple[tuple[OperatorAreaView, ...], OperatorAreaView | None]:
    if not areas:
        return areas, None
    selected = next(
        (area for area in areas if area.zone_id == selected_zone_id),
        next((area for area in areas if area.included_in_plan), areas[0]),
    )
    if selected.selected:
        return areas, selected
    updated = tuple(
        OperatorAreaView(**{**area.__dict__, "selected": area.zone_id == selected.zone_id})
        for area in areas
    )
    return updated, next(area for area in updated if area.zone_id == selected.zone_id)


def _priority_areas(areas: tuple[OperatorAreaView, ...]) -> tuple[OperatorAreaView, ...]:
    ordered = sorted(
        areas,
        key=lambda area: (
            area.priority_order is None,
            area.priority_order if area.priority_order is not None else 999,
            not area.included_in_plan,
            -area.drivers_needing_break_now,
            -area.heat_index_c,
            area.name,
        ),
    )
    return tuple(ordered[:MAX_PRIORITY_AREAS])


def build_city_kpis(
    plan: PredictiveCityPlan | None,
    areas: Sequence[OperatorAreaView],
) -> OperatorCityKpis:
    urgent = sum(area.drivers_needing_break_now for area in areas)
    if plan is None:
        return OperatorCityKpis(
            drivers_needing_break_now=urgent,
            covered_drivers=0,
            total_drivers_requiring_coverage=urgent,
            budget_remaining_usd=None,
            coverage_state="Coverage is updating",
            budget_remaining_label="—",
        )
    covered = max(0, int(plan.mandatory_now_covered))
    required = covered + max(0, int(plan.mandatory_now_uncovered))
    uncovered = max(0, required - covered)
    remaining_vnd = int(plan.budget_cap_vnd) - int(plan.p95_reserved_cost_vnd)
    return OperatorCityKpis(
        drivers_needing_break_now=urgent,
        covered_drivers=covered,
        total_drivers_requiring_coverage=required,
        budget_remaining_usd=remaining_vnd / 25_000,
        coverage_state="All covered" if uncovered == 0 else f"{uncovered:,} still uncovered",
        budget_remaining_label=format_currency_vnd(remaining_vnd),
    )


def _guardrails(
    proposal: SafePauseProposal,
    *,
    reserved_cost_vnd: int,
    budget_cap_vnd: int,
) -> tuple[OperatorGuardrailView, ...]:
    required = max(0, int(proposal.mandatory_eligible_drivers))
    covered = max(0, int(proposal.mandatory_selected_drivers))
    safety_passed = covered >= required
    service_drop = max(
        0.0,
        proposal.baseline_stress_fulfillment_rate - proposal.p90_fulfillment_rate,
    )
    service_passed = service_drop <= MAX_FULFILLMENT_DEGRADATION + 1e-9
    delay_passed = proposal.p90_eta_increase_minutes <= MAX_ETA_INCREASE_MINUTES
    cost_passed = reserved_cost_vnd <= budget_cap_vnd
    return (
        OperatorGuardrailView(
            "Safety coverage",
            f"{covered:,} of {required:,}",
            "All covered" if safety_passed else "Coverage gap",
            safety_passed,
        ),
        OperatorGuardrailView(
            "Orders completed",
            f"-{service_drop * 100:.1f} percentage points",
            "Within limit" if service_passed else "Over limit",
            service_passed,
        ),
        OperatorGuardrailView(
            "Expected pickup delay",
            f"+{proposal.p90_eta_increase_minutes:.1f} min",
            "Within limit" if delay_passed else "Over limit",
            delay_passed,
        ),
        OperatorGuardrailView(
            "Estimated plan cost",
            f"{format_currency_vnd(reserved_cost_vnd)} of {format_currency_vnd(budget_cap_vnd)}",
            "Within limit" if cost_passed else "Over limit",
            cost_passed,
        ),
    )


def build_recommendation_view(
    plan: PredictiveCityPlan | None,
    selected_area: OperatorAreaView | None,
) -> OperatorRecommendationView:
    if plan is None or selected_area is None:
        return EMPTY_RECOMMENDATION
    row = next((item for item in plan.rows if item.zone_id == selected_area.zone_id), None)
    if row is None:
        return EMPTY_RECOMMENDATION
    globally_actionable = (
        plan.status == "READY"
        and bool(plan.selected_zone_ids)
        and all(
            item.best_window is not None
            for item in plan.rows
            if item.zone_id in set(plan.selected_zone_ids)
        )
    )
    if row.best_window is None:
        no_plan = plan.status == "SAFETY_CAPACITY_BREACH"
        return OperatorRecommendationView(
            state="blocked" if no_plan else "unavailable",
            headline=(
                "No safe plan fits the current limits"
                if no_plan
                else "Recommendation temporarily unavailable"
            ),
            explanation=(
                "Available options exceed a service or budget limit."
                if no_plan
                else "Monitoring remains available while current evidence is checked."
            ),
            driver_count=0,
            start_time_label="—",
            group_summary="—",
            break_length_label="—",
            coverage_summary="Coverage not available",
            order_impact_summary="—",
            pickup_delay_summary="—",
            cost_summary="—",
            guardrails=(),
            can_activate=False,
            blocking_reason=(
                _plain_reason(row.portfolio_reason)
                or "Adjust limits or continue monitoring."
            ),
        )
    if not selected_area.included_in_plan:
        return OperatorRecommendationView(
            state="watch",
            headline=f"Continue monitoring {selected_area.name}",
            explanation="This area is not included in the current city break plan.",
            driver_count=0,
            start_time_label="Not scheduled",
            group_summary="No groups scheduled",
            break_length_label="—",
            coverage_summary="Area on watch",
            order_impact_summary="—",
            pickup_delay_summary="—",
            cost_summary="—",
            guardrails=(),
            can_activate=globally_actionable,
            blocking_reason="",
        )

    window = row.best_window
    proposal = window.proposal
    start_label = format_hanoi_after(
        plan.evidence_lineage.observed_at, window.start_delay_minutes
    )
    guards = _guardrails(
        proposal,
        reserved_cost_vnd=window.p95_reserved_cost_vnd,
        budget_cap_vnd=plan.budget_cap_vnd,
    )
    return OperatorRecommendationView(
        state="ready" if globally_actionable else "blocked",
        headline=f"Protect {proposal.selected_drivers:,} drivers starting at {start_label}",
        explanation=(
            "The city plan prioritizes urgent coverage while staying within current limits."
        ),
        driver_count=proposal.selected_drivers,
        start_time_label=start_label,
        group_summary=(
            f"{proposal.waves} staggered group" + ("" if proposal.waves == 1 else "s")
        ),
        break_length_label=f"{proposal.pause_minutes}-minute breaks",
        coverage_summary=guards[0].value,
        order_impact_summary=guards[1].value,
        pickup_delay_summary=guards[2].value,
        cost_summary=guards[3].value,
        guardrails=guards[:4],
        can_activate=globally_actionable and all(item.passed for item in guards),
        blocking_reason=(
            "" if globally_actionable else "Action is paused until all city limits pass."
        ),
    )


def _timing_options(
    plan: PredictiveCityPlan | None,
    selected_area: OperatorAreaView | None,
    optimization_evidence: object | None,
) -> tuple[OperatorTimingOptionView, ...]:
    if plan is None or selected_area is None:
        return ()
    row = next((item for item in plan.rows if item.zone_id == selected_area.zone_id), None)
    if row is None:
        return ()
    selected_proposal_id = (
        row.best_window.proposal.proposal_id if row.best_window is not None else None
    )
    raw_options: Sequence[object] = ()
    zone_options = _get(optimization_evidence, "zone_options", ())
    for item in zone_options or ():
        if _get(item, "zone_id") == selected_area.zone_id:
            raw_options = _get(item, "timing_options", ()) or ()
            break
    if not raw_options:
        raw_options = _get(optimization_evidence, "timing_options", ()) or ()

    result: list[OperatorTimingOptionView] = []
    for item in tuple(raw_options)[:MAX_TIMING_OPTIONS]:
        delay = max(0, int(_get(item, "start_delay_minutes", 0)))
        at = _get(item, "start_time")
        if not isinstance(at, datetime):
            at = plan.evidence_lineage.observed_at + timedelta(minutes=delay)
        proposal_id = _get(item, "proposal_id")
        reasons = tuple(_get(item, "rejection_reasons", ()) or ())
        result.append(
            OperatorTimingOptionView(
                start_time=at,
                start_time_label=format_hanoi_time(at),
                selected=bool(_get(item, "selected", False))
                or (proposal_id is not None and proposal_id == selected_proposal_id),
                feasible=bool(_get(item, "feasible", True)),
                drivers_protected=max(0, int(_get(item, "drivers_protected", 0))),
                projected_drivers_at_limit=_optional_float(
                    _get(item, "projected_drivers_at_limit_120m")
                ),
                pause_minutes=max(0, int(_get(item, "pause_minutes", 0))),
                expected_demand=_optional_int(
                    _get(item, "expected_demand_requests", _get(item, "expected_demand"))
                ),
                high_demand=_optional_int(
                    _get(item, "high_demand_requests", _get(item, "high_demand"))
                ),
                rejection_reason=_plain_reason(reasons[0]) if reasons else "",
            )
        )
    if result or row.best_window is None:
        return tuple(result)
    window = row.best_window
    return (
        OperatorTimingOptionView(
            start_time=plan.evidence_lineage.observed_at
            + timedelta(minutes=window.start_delay_minutes),
            start_time_label=format_hanoi_after(
                plan.evidence_lineage.observed_at, window.start_delay_minutes
            ),
            selected=True,
            feasible=True,
            drivers_protected=window.proposal.selected_drivers,
            projected_drivers_at_limit=window.projected_mandatory_after_120m,
            pause_minutes=window.proposal.pause_minutes,
        ),
    )


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _portfolio_options(
    optimization_evidence: object | None,
) -> tuple[OperatorPortfolioOptionView, ...]:
    raw = tuple(_get(optimization_evidence, "portfolio_options", ()) or ())
    result: list[OperatorPortfolioOptionView] = []
    for index, item in enumerate(raw[:MAX_PORTFOLIO_OPTIONS], start=1):
        required = max(0, int(_get(item, "urgent_drivers_required", 0)))
        covered = max(0, int(_get(item, "urgent_drivers_covered", 0)))
        reasons = tuple(_get(item, "rejection_reasons", ()) or ())
        result.append(
            OperatorPortfolioOptionView(
                label="Selected plan" if bool(_get(item, "selected", False)) else f"Plan option {index}",
                selected=bool(_get(item, "selected", False)),
                feasible=bool(_get(item, "feasible", False)),
                protected_drivers=max(0, int(_get(item, "protected_drivers", 0))),
                exposure_hours_avoided=max(
                    0.0, float(_get(item, "exposure_hours_avoided", 0.0))
                ),
                high_demand_cost_usd=max(
                    0.0,
                    float(_get(item, "high_demand_reserved_cost_vnd", 0)) / 25_000,
                ),
                pickup_delay_minutes=max(
                    0.0,
                    float(_get(item, "worst_area_pickup_delay_minutes", 0.0)),
                ),
                coverage_summary=f"{covered:,} of {required:,}",
                rejection_reason=_plain_reason(reasons[0]) if reasons else "",
            )
        )
    return tuple(result)


def _stress_metrics(proposal: SafePauseProposal | None, plan: PredictiveCityPlan | None) -> tuple[OperatorStressMetricView, ...]:
    if proposal is None or plan is None:
        return ()
    required = max(0, proposal.mandatory_eligible_drivers)
    covered = max(0, proposal.mandatory_selected_drivers)
    service_limit = max(
        0.0,
        proposal.baseline_stress_fulfillment_rate - MAX_FULFILLMENT_DEGRADATION,
    )
    return (
        OperatorStressMetricView(
            label="Safety coverage",
            expected_value=float(covered),
            high_demand_value=float(covered),
            limit_value=float(required),
            unit="drivers",
            expected_label=f"{covered:,} of {required:,}",
            high_demand_label=f"{covered:,} of {required:,}",
            passed=covered >= required,
        ),
        OperatorStressMetricView(
            label="Orders completed",
            expected_value=proposal.projected_fulfillment_rate * 100,
            high_demand_value=proposal.p90_fulfillment_rate * 100,
            limit_value=service_limit * 100,
            unit="%",
            expected_label=f"{proposal.projected_fulfillment_rate:.1%}",
            high_demand_label=f"{proposal.p90_fulfillment_rate:.1%}",
            passed=proposal.p90_fulfillment_rate >= service_limit - 1e-9,
        ),
        OperatorStressMetricView(
            label="Expected pickup delay",
            expected_value=proposal.projected_eta_increase_minutes,
            high_demand_value=proposal.p90_eta_increase_minutes,
            limit_value=MAX_ETA_INCREASE_MINUTES,
            unit="min",
            expected_label=f"+{proposal.projected_eta_increase_minutes:.1f} min",
            high_demand_label=f"+{proposal.p90_eta_increase_minutes:.1f} min",
            passed=proposal.p90_eta_increase_minutes <= MAX_ETA_INCREASE_MINUTES,
        ),
        OperatorStressMetricView(
            label="Plan cost",
            expected_value=plan.expected_cost_vnd / 25_000,
            high_demand_value=plan.p95_reserved_cost_vnd / 25_000,
            limit_value=plan.budget_cap_vnd / 25_000,
            unit="$",
            expected_label=format_currency_vnd(plan.expected_cost_vnd),
            high_demand_label=format_currency_vnd(plan.p95_reserved_cost_vnd),
            passed=plan.p95_reserved_cost_vnd <= plan.budget_cap_vnd,
        ),
    )


def build_decision_insights(
    plan: PredictiveCityPlan | None,
    selected_area: OperatorAreaView | None,
    *,
    optimization_evidence: object | None = None,
    outcome: OperatorOutcomeView | None = None,
) -> OperatorDecisionInsightsView:
    selected_row = (
        next((row for row in plan.rows if row.zone_id == selected_area.zone_id), None)
        if plan is not None and selected_area is not None
        else None
    )
    proposal = (
        selected_row.best_window.proposal
        if selected_row is not None and selected_row.best_window is not None
        else None
    )
    evaluated = _optional_int(
        _get(optimization_evidence, "evaluated_portfolio_count")
    )
    compliant = _optional_int(
        _get(optimization_evidence, "budget_compliant_portfolio_count")
    )
    if evaluated is None:
        evaluated_label = "Comparison details are not available yet."
    elif compliant is None:
        evaluated_label = f"Compared {evaluated:,} plan combinations."
    else:
        evaluated_label = (
            f"Compared {evaluated:,} plan combinations; "
            f"{compliant:,} were within all limits."
        )
    timing = _timing_options(plan, selected_area, optimization_evidence)
    selected_start = next(
        (item.start_time_label for item in timing if item.selected), "—"
    )
    return OperatorDecisionInsightsView(
        timing_options=timing,
        portfolio_options=_portfolio_options(optimization_evidence),
        stress_metrics=_stress_metrics(proposal, plan),
        outcome=outcome,
        evaluated_option_label=evaluated_label,
        selected_start_label=selected_start,
        budget_limit_usd=(plan.budget_cap_vnd / 25_000 if plan is not None else 0.0),
    )


def build_area_evidence_table(
    areas: Sequence[OperatorAreaView], endpoint_label: str
) -> OperatorTable:
    columns = (
        "Area",
        "Heat",
        "Need a break now",
        f"By {endpoint_label}",
        "Recommended start",
        "Plan status",
    )
    rows = tuple(
        (
            area.name,
            f"{area.heat_state_label} · {area.heat_index_c:.1f}°C",
            area.drivers_needing_break_now,
            area.expected_needing_protection_count
            if area.expected_needing_protection_count is not None
            else "Updating",
            area.recommended_start_label,
            area.plan_status_label,
        )
        for area in tuple(areas)[:MAX_AREAS]
    )
    return OperatorTable(columns=columns, rows=rows)


def build_driver_evidence_table(
    proposal: SafePauseProposal | None,
    *,
    operational_time: datetime,
) -> OperatorTable:
    columns = (
        "Driver",
        "Why included",
        "Heat exposure",
        "Risk level",
        "Break starts",
        "Break length",
    )
    if proposal is None:
        return OperatorTable(columns=columns, rows=())
    ordered = sorted(
        proposal.driver_decisions,
        key=lambda item: (
            item.priority_tier != "MANDATORY_4H",
            -item.exposure_minutes,
            item.driver_id_hash,
        ),
    )[:MAX_DRIVER_ROWS]
    rows = tuple(
        (
            item.driver_id_hash[:10],
            (
                "Safety limit reached"
                if item.priority_tier == "MANDATORY_4H"
                else "Approaching the safety limit"
            ),
            format_duration(item.exposure_minutes),
            format_risk_level(item.baseline_risk),
            format_hanoi_after(operational_time, item.pause_start_delay_minutes),
            format_duration(item.pause_duration_minutes),
        )
        for item in ordered
    )
    return OperatorTable(columns=columns, rows=rows)


def _history_action(value: object) -> str:
    normalized = str(value or "").upper()
    if normalized in {"ACTIVATE", "APPROVED", "SIMULATED"}:
        return "Activate SafePause"
    if normalized in {"CONTINUE", "NO_ACTION", "CONTINUED"}:
        return "Continue monitoring"
    return "Recorded decision"


def build_history_evidence_table(history: Sequence[object]) -> OperatorTable:
    columns = ("Time", "Action", "Drivers", "Result", "Coverage")
    rows: list[tuple[object, ...]] = []
    for item in tuple(history)[-MAX_HISTORY_ROWS:][::-1]:
        recorded_at = (
            _get(item, "recorded_at")
            or _get(item, "approved_at")
            or _get(item, "created_at")
        )
        action_value = _get(item, "choice") or _get(item, "action") or _get(item, "status")
        driver_count = (
            _get(item, "protected_driver_count")
            if _get(item, "protected_driver_count") is not None
            else _get(item, "selected_drivers", 0)
        )
        coverage = (
            _get(item, "coverage")
            or _get(item, "area_summary")
            or _get(item, "zone_name")
            or "City plan"
        )
        result = _get(item, "result") or _get(item, "status") or "Recorded"
        rows.append(
            (
                format_hanoi_time(recorded_at) if isinstance(recorded_at, datetime) else "—",
                _history_action(action_value),
                max(0, int(driver_count or 0)),
                str(result).replace("_", " ").title(),
                str(coverage),
            )
        )
    return OperatorTable(columns=columns, rows=tuple(rows))


def build_operator_console_view(
    plan: PredictiveCityPlan | None,
    zones: Sequence[ZoneSnapshot],
    constraints: DecisionConstraints,
    *,
    selected_zone_id: str | None = None,
    selected_decision: object | None = None,
    optimization_evidence: object | None = None,
    outcome: OperatorOutcomeView | None = None,
    history: Sequence[object] = (),
    now: datetime | None = None,
) -> OperatorConsoleView:
    """Build the complete immutable console view from authoritative inputs.

    ``selected_decision`` is accepted for app integration, but proposal selection remains
    owned by ``plan``. It is used only when its proposal is the exact proposal already
    referenced by the selected city-plan row.
    """
    del constraints  # The normalized values are already represented by the city plan.
    areas = _operator_areas(plan, zones, selected_zone_id=selected_zone_id)
    areas, selected_area = _select_area(areas, selected_zone_id)
    at = _plan_time(plan, zones)
    selected_row = (
        next((row for row in plan.rows if selected_area and row.zone_id == selected_area.zone_id), None)
        if plan is not None
        else None
    )
    authoritative_proposal = (
        selected_row.best_window.proposal
        if selected_row is not None and selected_row.best_window is not None
        else None
    )
    decision_proposal = _get(selected_decision, "proposal")
    evidence_proposal = (
        decision_proposal
        if authoritative_proposal is not None
        and decision_proposal is not None
        and decision_proposal.proposal_id == authoritative_proposal.proposal_id
        else authoritative_proposal
    )
    endpoint = format_hanoi_after(at, 120)
    readiness = format_readiness_label(plan.status if plan is not None else None)
    effective_optimization_evidence = optimization_evidence
    if effective_optimization_evidence is None and plan is not None:
        effective_optimization_evidence = getattr(plan, "optimization_evidence", None)
    return OperatorConsoleView(
        mode_label=format_mode_label(plan.mode if plan is not None else "CURRENT"),
        operational_time_label=format_hanoi_time(at),
        updated_label=format_freshness(at, now),
        readiness_state=readiness,
        synthetic_disclosure="Synthetic Hanoi operations · No real dispatch",
        city_kpis=build_city_kpis(plan, areas),
        map_areas=areas,
        priority_areas=_priority_areas(areas),
        selected_area=selected_area,
        recommendation=build_recommendation_view(plan, selected_area),
        decision_insights=build_decision_insights(
            plan,
            selected_area,
            optimization_evidence=effective_optimization_evidence,
            outcome=outcome,
        ),
        evidence_summary=OperatorEvidenceSummary(
            areas=build_area_evidence_table(areas, endpoint),
            drivers=build_driver_evidence_table(
                evidence_proposal,
                operational_time=at,
            ),
            history=build_history_evidence_table(history),
        ),
    )


__all__ = [
    "MAX_AREAS",
    "MAX_DRIVER_ROWS",
    "MAX_HISTORY_ROWS",
    "MAX_PORTFOLIO_OPTIONS",
    "MAX_PRIORITY_AREAS",
    "MAX_TIMING_OPTIONS",
    "OperatorAreaView",
    "OperatorCityKpis",
    "OperatorConsoleView",
    "OperatorDecisionInsightsView",
    "OperatorEvidenceSummary",
    "OperatorGuardrailView",
    "OperatorKpiView",
    "OperatorOutcomePoint",
    "OperatorOutcomeView",
    "OperatorPortfolioOptionView",
    "OperatorRecommendationView",
    "OperatorStressMetricView",
    "OperatorTable",
    "OperatorTimingOptionView",
    "build_area_evidence_table",
    "build_city_kpis",
    "build_decision_insights",
    "build_driver_evidence_table",
    "build_history_evidence_table",
    "build_operator_console_view",
    "build_recommendation_view",
]
