"""Default operator-first Operations surface."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

import streamlit as st

from .city_map import render_city_map
from .decision_card import render_decision_card
from .decision_insights import render_decision_insights
from .view_models import OperatorConsoleView


@dataclass(frozen=True)
class OperatorOperationsResult:
    selected_zone_id: str | None
    decision_action: str | None
    insight_view: str


def render_operator_header(view: OperatorConsoleView) -> None:
    st.markdown(
        '<div class="operator-header">'
        '<div class="operator-brand">HeatSafe AI Ops</div>'
        '<div class="operator-meta">'
        f'<span>{escape(view.mode_label)}</span>'
        f'<span class="operator-status">{escape(view.readiness_state)}</span>'
        f'<span>{escape(view.operational_time_label)}</span>'
        f'<span>{escape(view.updated_label)}</span>'
        "</div></div>"
        f'<div class="operator-disclosure">{escape(view.synthetic_disclosure)}</div>',
        unsafe_allow_html=True,
    )


def render_city_kpis(view: OperatorConsoleView) -> None:
    """Render the fixed exactly-three KPI contract."""
    with st.container(horizontal=True):
        for card in view.city_kpis.cards:
            st.metric(
                card.label,
                card.value,
                card.detail,
                delta_color="off",
                border=True,
            )


def render_operations(
    view: OperatorConsoleView,
    *,
    decision_available: bool = True,
    recording: bool = False,
    recorded_action: str | None = None,
    selected_insight: str | None = None,
    key_prefix: str = "operator-operations",
) -> OperatorOperationsResult:
    """Render map-first operations with no data table and at most two columns."""
    render_operator_header(view)
    render_city_kpis(view)
    map_column, decision_column = st.columns([1.85, 1], gap="medium")
    with map_column:
        selected_zone_id = render_city_map(
            view.map_areas,
            view.priority_areas,
            key_prefix=f"{key_prefix}:map",
        )
    with decision_column:
        decision_action = render_decision_card(
            view.selected_area,
            view.recommendation,
            decision_available=decision_available,
            recording=recording,
            recorded_action=recorded_action,
            key_prefix=f"{key_prefix}:decision",
        )
    insight_view = render_decision_insights(
        view.decision_insights,
        selected_view=selected_insight,
        key_prefix=f"{key_prefix}:insights",
    )
    return OperatorOperationsResult(
        selected_zone_id=selected_zone_id,
        decision_action=decision_action,
        insight_view=insight_view,
    )


__all__ = [
    "OperatorOperationsResult",
    "render_city_kpis",
    "render_operations",
    "render_operator_header",
]
