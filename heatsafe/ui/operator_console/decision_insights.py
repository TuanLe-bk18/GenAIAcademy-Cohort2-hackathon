"""One-slot decision explanation charts."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from .styles import CYAN, GRAY, GREEN, ORANGE, PLOTLY_LAYOUT, RED
from .view_models import OperatorAreaView, OperatorDecisionInsightsView


def _timing_figure(view: OperatorDecisionInsightsView) -> go.Figure | None:
    options = view.timing_options
    if not options:
        return None
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        row_heights=(0.45, 0.55),
        subplot_titles=("Demand around each start option", "Projected safety effect"),
    )
    starts = [item.start_time for item in options]
    if any(item.expected_demand is not None for item in options):
        expected = [item.expected_demand or 0 for item in options]
        high_demand = [item.high_demand or item.expected_demand or 0 for item in options]
        figure.add_trace(
            go.Scatter(
                x=starts,
                y=expected,
                name="Expected demand",
                line={"color": CYAN, "width": 2.5},
                mode="lines+markers",
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=starts,
                y=high_demand,
                name="High-demand range",
                line={"color": ORANGE, "width": 2, "dash": "dot"},
                fill="tonexty",
                fillcolor="rgba(240, 163, 90, 0.18)",
                mode="lines+markers",
            ),
            row=1,
            col=1,
        )
    values = [
        item.projected_drivers_at_limit
        if item.projected_drivers_at_limit is not None
        else item.drivers_protected
        for item in options
    ]
    figure.add_trace(
        go.Bar(
            x=starts,
            y=values,
            name="Drivers at the safety limit",
            marker_color=[
                ORANGE if item.selected else CYAN if item.feasible else GRAY
                for item in options
            ],
            text=[
                "Selected" if item.selected else "Within limits" if item.feasible else "Blocked"
                for item in options
            ],
            textposition="outside",
            cliponaxis=False,
            customdata=[
                [item.drivers_protected, item.pause_minutes, item.rejection_reason or "Within all limits"]
                for item in options
            ],
            hovertemplate=(
                "Start %{x|%H:%M}<br>Drivers protected: %{customdata[0]}<br>"
                "Break length: %{customdata[1]} min<br>%{customdata[2]}<extra></extra>"
            ),
            showlegend=False,
        ),
        row=2,
        col=1,
    )
    selected = next((item for item in options if item.selected), None)
    if selected is not None:
        figure.add_vline(
            x=selected.start_time.timestamp() * 1000,
            line_color=ORANGE,
            line_width=2,
            annotation_text="Selected start",
            annotation_position="top right",
            row="all",
            col=1,  # type: ignore[arg-type]
        )
    figure.update_layout(
        **PLOTLY_LAYOUT,
        height=500,
        margin={"l": 52, "r": 44, "t": 58, "b": 42},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.11},
    )
    figure.update_yaxes(title_text="Requests", automargin=True, row=1, col=1)
    figure.update_yaxes(title_text="Drivers", automargin=True, row=2, col=1)
    figure.update_xaxes(title_text="Hanoi time", tickformat="%H:%M", row=2, col=1)
    return figure


def _all_districts_timing_figure(
    areas: tuple[OperatorAreaView, ...],
) -> go.Figure | None:
    """Compare every displayed district using only view-model values.

    This is intentionally a district comparison rather than a fabricated city
    forecast: the selected-area timing model is not available for every area.
    """
    if not areas:
        return None
    ordered = tuple(
        sorted(
            areas,
            key=lambda area: (
                not area.selected,
                -area.drivers_needing_break_now,
                area.name,
            ),
        )
    )
    labels = [area.name for area in ordered]
    colors = [
        CYAN if area.selected else ORANGE if area.included_in_plan else GRAY
        for area in ordered
    ]
    customdata = [
        [area.exposed_2h, area.forecast_requests_30m, area.plan_status_label]
        for area in ordered
    ]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.16,
        row_heights=(0.5, 0.5),
        subplot_titles=(
            "Drivers needing a break now",
            "Forecast demand over the next 30 minutes",
        ),
    )
    figure.add_trace(
        go.Bar(
            x=labels,
            y=[area.drivers_needing_break_now for area in ordered],
            marker_color=colors,
            text=[f"{area.drivers_needing_break_now:,}" for area in ordered],
            textposition="outside",
            cliponaxis=False,
            customdata=customdata,
            hovertemplate=(
                "%{x}<br>Need a break now: %{y:,}<br>2h exposure: %{customdata[0]:,}"
                "<br>Plan status: %{customdata[2]}<extra></extra>"
            ),
            showlegend=False,
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Bar(
            x=labels,
            y=[area.forecast_requests_30m for area in ordered],
            marker_color=colors,
            text=[f"{area.forecast_requests_30m:,}" for area in ordered],
            textposition="outside",
            cliponaxis=False,
            customdata=customdata,
            hovertemplate=(
                "%{x}<br>Forecast demand: %{y:,} requests / 30 min"
                "<br>2h exposure: %{customdata[0]:,}<br>Plan status: %{customdata[2]}"
                "<extra></extra>"
            ),
            showlegend=False,
        ),
        row=2,
        col=1,
    )
    figure.update_layout(
        **PLOTLY_LAYOUT,
        height=500,
        margin={"l": 52, "r": 36, "t": 58, "b": 96},
        showlegend=False,
    )
    figure.update_yaxes(automargin=True, row=1, col=1)
    figure.update_yaxes(automargin=True, row=2, col=1)
    figure.update_xaxes(tickangle=-32, automargin=True, row=2, col=1)
    return figure


def _portfolio_trace(label: str, items: list[object], color: str) -> go.Scatter:
    return go.Scatter(
        x=[item.high_demand_cost_usd for item in items],
        y=[item.exposure_hours_avoided for item in items],
        mode="markers",
        name=label,
        marker={
            "color": color,
            "size": [max(10, min(24, 10 + item.protected_drivers * 0.28)) for item in items],
            "line": {"color": color, "width": 1},
        },
        customdata=[
            [
                item.protected_drivers,
                item.pickup_delay_minutes,
                item.coverage_summary,
                item.rejection_reason or "Within all limits",
            ]
            for item in items
        ],
        hovertemplate=(
            "$%{x:,.0f} high-demand cost<br>%{y:.1f} driver-hours avoided<br>"
            "Drivers protected: %{customdata[0]}<br>Pickup delay: +%{customdata[1]:.1f} min<br>"
            "Coverage: %{customdata[2]}<br>%{customdata[3]}<extra></extra>"
        ),
    )


def _tradeoff_figure(view: OperatorDecisionInsightsView) -> go.Figure | None:
    options = view.portfolio_options
    if not options:
        return None
    figure = go.Figure()
    feasible = [item for item in options if item.feasible and not item.selected]
    blocked = [item for item in options if not item.feasible]
    if feasible:
        figure.add_trace(_portfolio_trace("Within limits", feasible, CYAN))
    if blocked:
        figure.add_trace(_portfolio_trace("Blocked", blocked, GRAY))
    selected = next((item for item in options if item.selected), None)
    if selected is not None:
        figure.add_trace(
            go.Scatter(
                x=[selected.high_demand_cost_usd],
                y=[selected.exposure_hours_avoided],
                mode="markers+text",
                text=["Selected plan"],
                textposition="top center",
                name="Selected plan",
                marker={
                    "color": ORANGE,
                    "size": max(24, min(40, 20 + selected.protected_drivers * 0.35)),
                    "line": {"color": "#F4F7F6", "width": 3},
                },
                customdata=[[
                    selected.protected_drivers,
                    selected.pickup_delay_minutes,
                    selected.coverage_summary,
                    selected.rejection_reason or "Within all limits",
                ]],
                hovertemplate=(
                    "$%{x:,.0f} high-demand cost<br>%{y:.1f} driver-hours avoided<br>"
                    "Drivers protected: %{customdata[0]}<br>Pickup delay: +%{customdata[1]:.1f} min<br>"
                    "Coverage: %{customdata[2]}<br>%{customdata[3]}<extra></extra>"
                ),
            )
        )
    figure.add_vline(
        x=view.budget_limit_usd,
        line_color=RED,
        line_dash="dash",
        annotation_text="Budget limit",
        annotation_position="top",
    )
    figure.update_layout(
        **PLOTLY_LAYOUT,
        height=410,
        margin={"l": 62, "r": 42, "t": 52, "b": 50},
        xaxis_title="Estimated high-demand cost ($)",
        yaxis_title="Heat exposure avoided (driver-hours)",
        legend={"orientation": "h", "y": 1.12},
    )
    figure.update_yaxes(automargin=True)
    return figure


def _stress_figure(view: OperatorDecisionInsightsView) -> go.Figure | None:
    metrics = view.stress_metrics
    if not metrics:
        return None
    figure = go.Figure()
    for index, item in enumerate(metrics):
        limit = item.limit_value or 1
        expected_ratio = 100 * item.expected_value / limit
        high_ratio = 100 * item.high_demand_value / limit
        figure.add_trace(
            go.Bar(
                name="Expected case",
                x=[expected_ratio],
                y=[item.label],
                orientation="h",
                marker_color=CYAN,
                text=[item.expected_label],
                textposition="outside",
                cliponaxis=False,
                showlegend=index == 0,
                hovertemplate=f"Expected case: {item.expected_label}<extra></extra>",
            )
        )
        figure.add_trace(
            go.Bar(
                name="High-demand case",
                x=[high_ratio],
                y=[item.label],
                orientation="h",
                marker_color=GREEN if item.passed else RED,
                text=[f"{item.high_demand_label} · {'Pass' if item.passed else 'Blocked'}"],
                textposition="outside",
                cliponaxis=False,
                showlegend=index == 0,
                hovertemplate=f"High-demand case: {item.high_demand_label}<extra></extra>",
            )
        )
    figure.add_vline(x=100, line_color=RED, line_dash="dot", line_width=2, annotation_text="Limit")
    figure.update_layout(
        **PLOTLY_LAYOUT,
        barmode="group",
        height=390,
        margin={"l": 150, "r": 100, "t": 54, "b": 38},
        legend={"orientation": "h", "y": 1.13},
    )
    figure.update_xaxes(title_text="Share of the constraint", ticksuffix="%", range=[0, 130])
    figure.update_yaxes(automargin=True)
    return figure


def _outcome_figure(view: OperatorDecisionInsightsView) -> go.Figure | None:
    outcome = view.outcome
    if outcome is None or not outcome.available:
        return None
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=[item.at for item in outcome.points],
            y=[item.without_safepause for item in outcome.points],
            name="Without SafePause",
            line={"color": GRAY, "width": 2, "dash": "dot"},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[item.at for item in outcome.points],
            y=[item.with_safepause for item in outcome.points],
            name="With SafePause",
            line={"color": ORANGE, "width": 3},
            fill="tonexty",
            fillcolor="rgba(67, 182, 110, 0.18)",
        )
    )
    figure.update_layout(
        **PLOTLY_LAYOUT,
        height=410,
        margin={"l": 62, "r": 42, "t": 50, "b": 46},
        xaxis_title="Hanoi time",
        yaxis_title=f"{outcome.metric_label} ({outcome.unit})",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.12},
    )
    figure.update_yaxes(automargin=True)
    return figure


def render_decision_insights(
    view: OperatorDecisionInsightsView,
    *,
    areas: tuple[OperatorAreaView, ...] = (),
    selected_view: str | None = None,
    key_prefix: str = "operator-insights",
) -> str:
    """Render exactly one selected explanation chart in a stable slot."""
    st.subheader("Why this plan")
    st.caption(view.evaluated_option_label)
    options = view.available_views
    chosen = st.segmented_control(
        "Plan explanation",
        options,
        default=selected_view if selected_view in options else "Timing",
        key=f"{key_prefix}:selector",
    )
    active = chosen if chosen in options else "Timing"
    builders = {
        "Timing": _timing_figure,
        "Trade-offs": _tradeoff_figure,
        "Stress test": _stress_figure,
        "Outcome": _outcome_figure,
    }
    scope_copy = {
        "Timing": "Selected area · expected and high-demand forecast",
        "Trade-offs": "City-wide portfolio · each point is one evaluated plan combination",
        "Stress test": "Selected area plus city budget · 100% marks each operating limit",
        "Outcome": "Same scenario · comparison branches only after the recorded choice",
    }
    timing_scope = "Selected district"
    if active == "Timing":
        timing_scope = st.segmented_control(
            "Timing scope",
            ("Selected district", "All districts"),
            default="Selected district",
            key=f"{key_prefix}:timing-scope",
        ) or "Selected district"
    if active == "Timing" and timing_scope == "All districts":
        st.caption(
            "All districts · cyan is selected, amber is included in the current plan."
        )
        figure = _all_districts_timing_figure(areas)
    else:
        st.caption(scope_copy[active])
        figure = builders[active](view)
    chart_height = 540 if active == "Timing" else 460
    with st.container(height=chart_height, border=True):
        if figure is None:
            st.info(
                "Supporting comparison evidence is not available for this view yet.",
                icon=":material/info:",
            )
        else:
            st.plotly_chart(figure, width="stretch", key=f"{key_prefix}:{active}")
    return active


__all__ = ["render_decision_insights"]
