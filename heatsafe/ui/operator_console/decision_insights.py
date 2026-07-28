"""One-slot decision explanation charts."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from .styles import CYAN, GRAY, GREEN, ORANGE, PLOTLY_LAYOUT, RED
from .view_models import OperatorDecisionInsightsView


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
        figure.add_trace(
            go.Scatter(
                x=starts,
                y=[item.expected_demand or 0 for item in options],
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
                y=[item.high_demand or 0 for item in options],
                name="High-demand case",
                line={"color": ORANGE, "width": 2, "dash": "dot"},
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
        margin={"l": 30, "r": 25, "t": 45, "b": 25},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.08},
    )
    figure.update_yaxes(title_text="Requests", row=1, col=1)
    figure.update_yaxes(title_text="Drivers", row=2, col=1)
    figure.update_xaxes(title_text="Hanoi time", tickformat="%H:%M", row=2, col=1)
    return figure


def _tradeoff_figure(view: OperatorDecisionInsightsView) -> go.Figure | None:
    options = view.portfolio_options
    if not options:
        return None
    figure = go.Figure()
    for item in options:
        color = ORANGE if item.selected else CYAN if item.feasible else GRAY
        label = "Selected" if item.selected else "Within limits" if item.feasible else "Blocked"
        figure.add_trace(
            go.Scatter(
                x=[item.high_demand_cost_usd],
                y=[item.exposure_hours_avoided],
                mode="markers+text" if item.selected else "markers",
                text=["Selected"] if item.selected else None,
                textposition="top center",
                name=label,
                marker={
                    "color": color,
                    "size": max(10, min(34, 10 + item.protected_drivers * 0.45)),
                    "line": {"color": GRAY if item.selected else color, "width": 2 if item.selected else 1},
                },
                customdata=[[
                    item.protected_drivers,
                    item.pickup_delay_minutes,
                    item.coverage_summary,
                    item.rejection_reason or "Within all limits",
                ]],
                hovertemplate=(
                    "$%{x:,.0f} high-demand cost<br>%{y:.1f} driver-hours avoided<br>"
                    "Drivers protected: %{customdata[0]}<br>Pickup delay: +%{customdata[1]:.1f} min<br>"
                    "Coverage: %{customdata[2]}<br>%{customdata[3]}<extra></extra>"
                ),
                showlegend=False,
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
        height=390,
        margin={"l": 20, "r": 20, "t": 30, "b": 20},
        xaxis_title="Estimated high-demand cost ($)",
        yaxis_title="Heat exposure avoided (driver-hours)",
    )
    return figure


def _stress_figure(view: OperatorDecisionInsightsView) -> go.Figure | None:
    metrics = view.stress_metrics
    if not metrics:
        return None
    figure = make_subplots(
        rows=len(metrics),
        cols=1,
        vertical_spacing=0.12,
        subplot_titles=tuple(item.label for item in metrics),
    )
    for row, item in enumerate(metrics, start=1):
        figure.add_trace(
            go.Bar(
                name="Expected demand",
                x=[item.expected_value],
                y=["Expected"],
                orientation="h",
                marker_color=CYAN,
                text=[item.expected_label],
                textposition="auto",
                showlegend=row == 1,
                hovertemplate=f"Expected demand: {item.expected_label}<extra></extra>",
            ),
            row=row,
            col=1,
        )
        figure.add_trace(
            go.Bar(
                name="High-demand case",
                x=[item.high_demand_value],
                y=["High demand"],
                orientation="h",
                marker_color=GREEN if item.passed else RED,
                text=[f"{item.high_demand_label} · {'Pass' if item.passed else 'Blocked'}"],
                textposition="auto",
                showlegend=row == 1,
                hovertemplate=f"High-demand case: {item.high_demand_label}<extra></extra>",
            ),
            row=row,
            col=1,
        )
        if item.limit_value is not None:
            figure.add_vline(
                x=item.limit_value,
                line_color=RED,
                line_dash="dot",
                line_width=2,
                annotation_text="Limit",
                annotation_position="top",
                row=row,  # type: ignore[arg-type]
                col=1,  # type: ignore[arg-type]
            )
        figure.update_xaxes(title_text=item.unit, row=row, col=1)
    figure.update_layout(
        **PLOTLY_LAYOUT,
        barmode="group",
        height=610,
        margin={"l": 35, "r": 25, "t": 45, "b": 20},
        legend={"orientation": "h", "y": 1.06},
    )
    return figure


def _outcome_figure(view: OperatorDecisionInsightsView) -> go.Figure | None:
    outcome = view.outcome
    if outcome is None or not outcome.available:
        return None
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=[item.at for item in outcome.points],
            y=[item.with_safepause for item in outcome.points],
            name="With SafePause",
            line={"color": ORANGE, "width": 3},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[item.at for item in outcome.points],
            y=[item.without_safepause for item in outcome.points],
            name="Without SafePause",
            line={"color": GRAY, "width": 2, "dash": "dot"},
        )
    )
    figure.update_layout(
        **PLOTLY_LAYOUT,
        height=390,
        margin={"l": 20, "r": 20, "t": 30, "b": 20},
        xaxis_title="Hanoi time",
        yaxis_title=f"{outcome.metric_label} ({outcome.unit})",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.08},
    )
    return figure


def render_decision_insights(
    view: OperatorDecisionInsightsView,
    *,
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
    figure = builders[active](view)
    chart_height = 660 if active == "Stress test" else 550 if active == "Timing" else 440
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
