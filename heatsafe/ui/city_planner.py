"""Shared all-zone planning view for Current and Accelerated operations.

The renderer deliberately separates heat (map fill) from portfolio state
(outline and status).  It accepts an unavailable view as well, so a missing
Current-mode model input remains visible and fail-closed instead of borrowing
future simulation evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Sequence
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

from heatsafe.currency import vnd_to_usd
from heatsafe.models import PredictiveCityPlan, ZoneSnapshot

from .styles import PLOTLY_LAYOUT

HANOI_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass(frozen=True)
class CityPlannerRow:
    """One display row with the same fields in both operational modes."""

    zone_id: str
    zone_name: str
    latitude: float
    longitude: float
    active_drivers: int
    heat_index_c: float
    heat_provenance: str
    mandatory_now: float | None
    projected_mandatory_60m: float | None
    projected_mandatory_120m: float | None
    watchlist_120m: float | None
    expected_crossers_120m: float | None
    raw_risk_120m: float | None
    expected_risk_prevented: float | None
    residual_risk_120m: float | None
    severity_rank: int | None
    future_safety_rank: int | None
    opportunity_rank: int | None
    best_window: str | None
    expected_cost_vnd: int | None
    p95_reserved_cost_vnd: int | None
    portfolio_status: str
    portfolio_reason: str
    selected: bool = False


@dataclass(frozen=True)
class CityPlannerView:
    mode: str
    snapshot_id: str
    observed_at_label: str
    plan_status: str
    budget_cap_vnd: int | None
    expected_cost_vnd: int | None
    p95_reserved_cost_vnd: int | None
    mandatory_now_covered: int | None
    mandatory_now_uncovered: int | None
    rows: tuple[CityPlannerRow, ...]
    unavailable_reason: str | None = None

    @property
    def selected_count(self) -> int:
        return sum(row.selected for row in self.rows)

    @property
    def actionable(self) -> bool:
        return self.unavailable_reason is None and self.selected_count > 0


def _horizon(row, minutes_ahead: int):
    try:
        return next(
            item for item in row.horizons if item.minutes_ahead == minutes_ahead
        )
    except StopIteration as exc:
        raise ValueError(
            f"zone {row.zone_id} has no +{minutes_ahead} minute horizon"
        ) from exc


def build_city_planner_view(
    plan: PredictiveCityPlan,
    zones: Sequence[ZoneSnapshot],
) -> CityPlannerView:
    """Normalize an authoritative plan into the renderer's single contract."""
    zone_by_id = {zone.zone_id: zone for zone in zones}
    if len(zone_by_id) != 10 or len(plan.rows) != 10:
        raise ValueError("city planner requires exactly ten configured districts")
    if set(zone_by_id) != {row.zone_id for row in plan.rows}:
        raise ValueError("city planner plan and zone evidence do not match")

    selected_zone_ids = set(plan.selected_zone_ids)
    rows: list[CityPlannerRow] = []
    for plan_row in plan.rows:
        zone = zone_by_id[plan_row.zone_id]
        now = _horizon(plan_row, 0)
        horizon_60 = _horizon(plan_row, 60)
        horizon_120 = _horizon(plan_row, 120)
        window = plan_row.best_window
        rows.append(
            CityPlannerRow(
                zone_id=plan_row.zone_id,
                zone_name=plan_row.zone_name,
                latitude=zone.latitude,
                longitude=zone.longitude,
                active_drivers=zone.active_drivers,
                heat_index_c=now.heat.heat_index_c,
                heat_provenance=now.heat.provenance,
                mandatory_now=now.mandatory_now,
                projected_mandatory_60m=horizon_60.projected_mandatory,
                projected_mandatory_120m=horizon_120.projected_mandatory,
                watchlist_120m=horizon_120.watchlist,
                expected_crossers_120m=horizon_120.expected_crossers,
                raw_risk_120m=horizon_120.baseline_expected_risk,
                expected_risk_prevented=plan_row.expected_risk_prevented,
                residual_risk_120m=(
                    window.residual_risk_120m if window is not None else None
                ),
                severity_rank=plan_row.severity_rank,
                future_safety_rank=plan_row.future_safety_rank,
                opportunity_rank=plan_row.opportunity_rank,
                best_window=(
                    f"+{window.start_delay_minutes}–+{window.end_delay_minutes}m"
                    if window is not None
                    else None
                ),
                expected_cost_vnd=(
                    window.expected_cost_vnd if window is not None else None
                ),
                p95_reserved_cost_vnd=(
                    window.p95_reserved_cost_vnd if window is not None else None
                ),
                portfolio_status=plan_row.portfolio_status,
                portfolio_reason=plan_row.portfolio_reason,
                selected=plan_row.zone_id in selected_zone_ids,
            )
        )
    lineage = plan.evidence_lineage
    return CityPlannerView(
        mode=plan.mode,
        snapshot_id=lineage.snapshot_id,
        observed_at_label=lineage.observed_at.astimezone(HANOI_TZ).strftime(
            "%d %b %Y %H:%M"
        ),
        plan_status=plan.status,
        budget_cap_vnd=plan.budget_cap_vnd,
        expected_cost_vnd=plan.expected_cost_vnd,
        p95_reserved_cost_vnd=plan.p95_reserved_cost_vnd,
        mandatory_now_covered=plan.mandatory_now_covered,
        mandatory_now_uncovered=plan.mandatory_now_uncovered,
        rows=tuple(rows),
    )


def build_unavailable_city_planner_view(
    zones: Sequence[ZoneSnapshot],
    *,
    mode: str,
    reason: str,
) -> CityPlannerView:
    """Expose all current observations without fabricating model evidence."""
    if len({zone.zone_id for zone in zones}) != 10:
        raise ValueError("city planner requires exactly ten configured districts")
    rows = tuple(
        CityPlannerRow(
            zone_id=zone.zone_id,
            zone_name=zone.name,
            latitude=zone.latitude,
            longitude=zone.longitude,
            active_drivers=zone.active_drivers,
            heat_index_c=zone.heat_index_c,
            heat_provenance=(
                "SIMULATED_OBSERVATION"
                if zone.weather_is_simulated
                else "OBSERVED"
            ),
            mandatory_now=zone.exposed_4h,
            projected_mandatory_60m=None,
            projected_mandatory_120m=None,
            watchlist_120m=None,
            expected_crossers_120m=None,
            raw_risk_120m=None,
            expected_risk_prevented=None,
            residual_risk_120m=None,
            severity_rank=None,
            future_safety_rank=None,
            opportunity_rank=None,
            best_window=None,
            expected_cost_vnd=None,
            p95_reserved_cost_vnd=None,
            portfolio_status="UNAVAILABLE",
            portfolio_reason=reason,
        )
        for zone in sorted(zones, key=lambda item: item.zone_id)
    )
    observed_at = next(zone for zone in zones).observed_at
    return CityPlannerView(
        mode=mode,
        snapshot_id=next(zone for zone in zones).snapshot_id,
        observed_at_label=observed_at.astimezone(HANOI_TZ).strftime(
            "%d %b %Y %H:%M"
        ),
        plan_status="EVIDENCE_UNAVAILABLE",
        budget_cap_vnd=None,
        expected_cost_vnd=None,
        p95_reserved_cost_vnd=None,
        mandatory_now_covered=None,
        mandatory_now_uncovered=None,
        rows=rows,
        unavailable_reason=reason,
    )


def _number(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.{digits}f}"


def _currency(value_vnd: int | None) -> str:
    return "—" if value_vnd is None else f"${vnd_to_usd(value_vnd):,.2f}"


def city_table_rows(view: CityPlannerView) -> list[dict[str, object]]:
    """Return the fixed all-zone table contract for AppTest and rendering."""
    return [
        {
            "District": row.zone_name,
            "Heat index (°C)": round(row.heat_index_c, 1),
            "Heat source": row.heat_provenance,
            "Mandatory now": _number(row.mandatory_now),
            "Projected +60m": _number(row.projected_mandatory_60m),
            "Projected +120m": _number(row.projected_mandatory_120m),
            "Watchlist +120m": _number(row.watchlist_120m),
            "Expected crossers": _number(row.expected_crossers_120m),
            "Severity rank": _number(row.severity_rank, 0),
            "Future safety rank": _number(row.future_safety_rank, 0),
            "Opportunity rank": _number(row.opportunity_rank, 0),
            "Raw risk +120m": _number(row.raw_risk_120m, 2),
            "Risk prevented": _number(row.expected_risk_prevented, 2),
            "Residual risk +120m": _number(row.residual_risk_120m, 2),
            "Best window": row.best_window or "—",
            "Expected cost": _currency(row.expected_cost_vnd),
            "P95 reserve": _currency(row.p95_reserved_cost_vnd),
            "Portfolio status": row.portfolio_status,
            "Reason": row.portfolio_reason,
        }
        for row in sorted(
            view.rows,
            key=lambda item: (
                item.future_safety_rank is None,
                item.future_safety_rank or 0,
                item.zone_name,
            ),
        )
    ]


def _heat_color(value: float, lower: float, upper: float) -> list[int]:
    ratio = 0.5 if upper <= lower else (value - lower) / (upper - lower)
    ratio = max(0.0, min(1.0, ratio))
    return [
        round(242 * ratio + 245 * (1 - ratio)),
        round(88 * ratio + 183 * (1 - ratio)),
        round(61 * ratio + 66 * (1 - ratio)),
        215,
    ]


def _map_rows(rows: Iterable[CityPlannerRow]) -> list[dict[str, object]]:
    ordered = list(rows)
    lower = min((row.heat_index_c for row in ordered), default=0.0)
    upper = max((row.heat_index_c for row in ordered), default=1.0)
    return [
        {
            "zone_id": row.zone_id,
            "name": row.zone_name,
            "lat": row.latitude,
            "lon": row.longitude,
            "active": row.active_drivers,
            "heat_index": round(row.heat_index_c, 1),
            "heat_source": row.heat_provenance,
            "portfolio_status": row.portfolio_status,
            "portfolio_reason": row.portfolio_reason,
            "color": _heat_color(row.heat_index_c, lower, upper),
            "line_color": (
                [34, 197, 94, 255]
                if row.selected
                else [255, 255, 255, 80]
            ),
            "line_width": 5 if row.selected else 1,
        }
        for row in ordered
    ]


def _render_overview(view: CityPlannerView) -> None:
    coverage = "—"
    if (
        view.mandatory_now_covered is not None
        and view.mandatory_now_uncovered is not None
    ):
        total = view.mandatory_now_covered + view.mandatory_now_uncovered
        coverage = f"{view.mandatory_now_covered}/{total}"
    status_label = view.plan_status.replace("_", " ")
    status_delta = "Evidence unavailable" if view.unavailable_reason else None
    metrics = st.columns(4, vertical_alignment="center")
    metrics[0].metric("Plan status", status_label, status_delta, border=True)
    metrics[1].metric("Selected districts", view.selected_count, border=True)
    metrics[2].metric(
        "City P95 reserve", _currency(view.p95_reserved_cost_vnd), border=True
    )
    metrics[3].metric("Mandatory coverage", coverage, border=True)


def _render_map(view: CityPlannerView, *, selection_context: str) -> None:
    map_rows = _map_rows(view.rows)
    if not map_rows:
        return
    map_event = st.pydeck_chart(
        pdk.Deck(
            layers=[
                pdk.Layer(
                    "ScatterplotLayer",
                    pd.DataFrame(map_rows),
                    id="city-plan-zones",
                    get_position=["lon", "lat"],
                    get_fill_color="color",
                    get_radius="800 + active * 2",
                    pickable=True,
                    stroked=True,
                    get_line_color="line_color",
                    get_line_width="line_width",
                    line_width_min_pixels=1,
                )
            ],
            initial_view_state=pdk.ViewState(
                latitude=21.025, longitude=105.81, zoom=10.1, pitch=35
            ),
            map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
            tooltip=cast(
                Any,
                {
                    "html": (
                        "<b>{name}</b><br/>Heat Index: {heat_index}°C "
                        "({heat_source})<br/>Portfolio: {portfolio_status}"
                        "<br/>{portfolio_reason}"
                    ),
                    "style": {"backgroundColor": "#111827", "color": "#f8fafc"},
                },
            ),
        ),
        height=360,
        key=f"city-planner-map:{selection_context}",
        on_select="rerun",
        selection_mode="single-object",
    )
    selected_objects = (
        map_event.get("selection", {})
        .get("objects", {})
        .get("city-plan-zones", [])
    )
    if selected_objects:
        selected_zone_id = selected_objects[0].get("zone_id")
        if selected_zone_id in {row.zone_id for row in view.rows}:
            st.session_state.selected_zone_id = selected_zone_id
            st.rerun()


def _render_tradeoffs(view: CityPlannerView) -> None:
    records = [
        {
            "District": row.zone_name,
            "Heat index": row.heat_index_c,
            "Raw risk": row.raw_risk_120m or 0.0,
            "Risk prevented": row.expected_risk_prevented or 0.0,
            "Expected crossers": row.expected_crossers_120m or 0.0,
            "Portfolio": row.portfolio_status,
        }
        for row in view.rows
        if row.raw_risk_120m is not None
    ]
    if not records:
        st.info("Tradeoffs remain unavailable until snapshot-matched risk evidence loads.")
        return
    figure = px.scatter(
        pd.DataFrame(records),
        x="Raw risk",
        y="Risk prevented",
        size="Expected crossers",
        color="Heat index",
        symbol="Portfolio",
        hover_name="District",
        color_continuous_scale=px.colors.sequential.YlOrRd,
        title="Severity and preventable risk by district",
    )
    figure.update_layout(
        **PLOTLY_LAYOUT,
        height=360,
        margin={"l": 0, "r": 0, "t": 56, "b": 0},
        title_x=0.5,
        title_xanchor="center",
        xaxis={"gridcolor": "rgba(255,255,255,.08)"},
        yaxis={"gridcolor": "rgba(255,255,255,.08)"},
    )
    st.plotly_chart(figure, width="stretch")


def _render_horizon_chart(view: CityPlannerView) -> None:
    records = [
        row
        for row in sorted(
            view.rows,
            key=lambda item: (item.future_safety_rank or 99, item.zone_name),
        )
        if row.mandatory_now is not None
    ]
    if not records:
        return
    figure = go.Figure()
    figure.add_bar(
        name="Mandatory now",
        x=[row.zone_name for row in records],
        y=[row.mandatory_now for row in records],
        marker_color="#fb923c",
    )
    figure.add_bar(
        name="Projected mandatory +120m",
        x=[row.zone_name for row in records],
        y=[row.projected_mandatory_120m or 0 for row in records],
        marker_color="#22c55e",
    )
    figure.update_layout(
        **PLOTLY_LAYOUT,
        barmode="group",
        height=340,
        title="Mandatory cohort: now versus +120 minutes",
        title_x=0.5,
        title_xanchor="center",
        margin={"l": 0, "r": 0, "t": 56, "b": 0},
        legend={"orientation": "h", "y": 1.08, "x": 0},
    )
    figure.update_xaxes(tickangle=-28, gridcolor="rgba(255,255,255,.08)")
    figure.update_yaxes(title_text="Drivers", gridcolor="rgba(255,255,255,.08)")
    st.plotly_chart(figure, width="stretch")


def _render_selected_detail(view: CityPlannerView) -> None:
    selected_zone_id = st.session_state.get("selected_zone_id")
    row = next(
        (item for item in view.rows if item.zone_id == selected_zone_id),
        view.rows[0] if view.rows else None,
    )
    if row is None:
        return
    with st.container(border=True):
        st.subheader(f"{row.zone_name} planning detail")
        st.caption(
            "Selection changes this detail only; it never changes the city portfolio."
        )
        metrics = st.columns(4, vertical_alignment="center")
        metrics[0].metric("Heat index", f"{row.heat_index_c:.1f}°C")
        metrics[1].metric("Mandatory now", _number(row.mandatory_now))
        metrics[2].metric("Projected +120m", _number(row.projected_mandatory_120m))
        metrics[3].metric("Best window", row.best_window or "Not actionable")
        st.caption(
            f"Heat evidence: {row.heat_provenance} · Portfolio: "
            f"{row.portfolio_status} · {row.portfolio_reason}"
        )


def render_city_planner(
    view: CityPlannerView,
    *,
    selection_context: str,
) -> None:
    """Render the complete ten-district visualization used by both modes."""
    display_mode = {
        "CURRENT": "PRODUCTION",
        "ACCELERATED": "ACCELERATED PRODUCTION",
    }.get(view.mode.upper(), view.mode.upper())
    st.subheader("City-wide preventive plan")
    st.caption(
        f"{display_mode} · {view.observed_at_label} ICT · "
        f"snapshot {view.snapshot_id[:12]}. Map fill is Heat Index; a green outline "
        "means the district is in the SafePause portfolio."
    )
    _render_overview(view)
    if view.unavailable_reason:
        st.warning(
            "Planning remains monitoring-only: "
            f"{view.unavailable_reason}"
        )
    map_column, tradeoff_column = st.columns(2, gap="medium")
    with map_column:
        _render_map(view, selection_context=selection_context)
    with tradeoff_column:
        _render_tradeoffs(view)
    _render_horizon_chart(view)
    st.markdown("#### All districts")
    st.dataframe(
        pd.DataFrame(city_table_rows(view)),
        hide_index=True,
        width="stretch",
        key=f"city-planner-table:{selection_context}",
    )
    _render_selected_detail(view)


def render_city_plan_actions(
    view: CityPlannerView,
    *,
    decision_available: bool,
) -> str | None:
    """Render one shared city-portfolio control bar and return the chosen action."""
    with st.container(border=True):
        st.markdown("#### City plan decision")
        if not decision_available:
            st.caption(
                "Predictive watch is active. The shared decision controls unlock "
                "at the accelerated decision tick."
            )
            return None
        if not view.actionable:
            st.caption(
                "No simulated action is available until exact, snapshot-matched "
                "city evidence produces a portfolio."
            )
            return None
        st.caption(
            f"Portfolio {view.plan_status.replace('_', ' ').lower()} · "
            f"{view.selected_count} district(s) selected · P95 reserve "
            f"{_currency(view.p95_reserved_cost_vnd)}. No external dispatch is sent."
        )
        activate_column, continue_column = st.columns(2)
        with activate_column:
            if st.button(
                "Activate SafePause",
                type="primary",
                width="stretch",
                key="city-plan-activate",
            ):
                return "ACTIVATE"
        with continue_column:
            if st.button(
                "Continue without intervention",
                width="stretch",
                key="city-plan-continue",
            ):
                return "CONTINUE"
    return None


def render_city_plan_copilot(view: CityPlannerView) -> None:
    """Explain the authoritative portfolio without recomputing any ranking."""
    st.markdown("#### HeatSafe Copilot")
    if view.unavailable_reason:
        st.info(
            "Copilot is monitoring-only because the authoritative city plan is unavailable."
        )
        return
    selected = [row.zone_name for row in view.rows if row.selected]
    st.write(
        "The authoritative portfolio is "
        f"**{view.plan_status.replace('_', ' ').lower()}**: "
        f"{', '.join(selected) if selected else 'no district selected'}."
    )
    st.caption(
        "This explanation reads the shared city plan; it does not independently "
        "re-rank districts."
    )


__all__ = [
    "CityPlannerRow",
    "CityPlannerView",
    "build_city_planner_view",
    "build_unavailable_city_planner_view",
    "city_table_rows",
    "render_city_plan_actions",
    "render_city_plan_copilot",
    "render_city_planner",
]
