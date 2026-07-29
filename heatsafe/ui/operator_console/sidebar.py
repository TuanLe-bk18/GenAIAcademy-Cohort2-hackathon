"""Global operator controls for app orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from heatsafe.currency import usd_to_vnd, vnd_to_usd
from heatsafe.models import DecisionConstraints

from .view_models import OperatorConsoleView

_LOGO_PATH = Path(__file__).with_name("assets") / "HeatsafeAIOps-logo.png"


@dataclass(frozen=True)
class OperatorPlaybackView:
    range_label: str
    current_time_label: str
    decision_time_label: str
    running: bool = False
    complete: bool = False


@dataclass(frozen=True)
class OperatorSidebarResult:
    mode: str
    selected_zone_id: str | None
    constraints: DecisionConstraints
    limits_applied: bool
    playback_action: str | None
    playback_speed: str
    refresh_requested: bool
    reset_requested: bool


def render_sidebar(
    view: OperatorConsoleView | None,
    constraints: DecisionConstraints,
    *,
    playback: OperatorPlaybackView | None = None,
    mode: str | None = None,
    area_options: Sequence[tuple[str, str]] = (),
    key_prefix: str = "operator-sidebar",
) -> OperatorSidebarResult:
    """Render app-level controls and return intents without executing domain commands."""
    current_mode = mode if mode in {"current", "accelerated-production"} else (
        "accelerated-production"
        if view is not None and view.mode_label == "EVENT REPLAY"
        else "current"
    )
    with st.sidebar:
        with st.container(
            horizontal=True,
            horizontal_alignment="left",
            vertical_alignment="center",
            gap="small",
        ):
            st.image(str(_LOGO_PATH), width=90)
            st.markdown(
                '<div class="operator-sidebar-copy">'
                '<div class="operator-sidebar-brand">'
                '<span class="operator-brand-heat">Heat</span>'
                '<span class="operator-brand-safe">Safe</span>'
                '<span class="operator-brand-text">AI</span><br>'
                '<span class="operator-brand-text">OPS</span></div>'
                '<div class="operator-sidebar-tagline">MONITOR — ALERT — PROTECT</div>'
                '</div>',
                unsafe_allow_html=True,
            )

        mode = st.segmented_control(
            "Mode",
            ("current", "accelerated-production"),
            default=current_mode,
            format_func=lambda item: (
                "PRODUCTION" if item == "current" else "EVENT REPLAY"
            ),
            key=f"{key_prefix}:mode",
            label_visibility="collapsed",
        )
        resolved_mode = (
            mode
            if mode in {"current", "accelerated-production"}
            else current_mode
        )
        area_views = view.map_areas if view is not None else ()
        area_name_by_id = {
            area.zone_id: area.name for area in area_views
        } or dict(area_options)
        selected_ids = list(area_name_by_id)
        selected_default = (
            view.selected_area.zone_id
            if view is not None and view.selected_area
            else st.session_state.get("selected_zone_id")
        )
        selected_zone_id = None
        if selected_ids and resolved_mode == "current":
            selected_zone_id = st.selectbox(
                "Selected area",
                selected_ids,
                index=(
                    selected_ids.index(selected_default)
                    if selected_default in selected_ids
                    else 0
                ),
                format_func=lambda value: area_name_by_id[value],
                key=f"{key_prefix}:area",
            )

        limits_applied = False
        budget_usd = float(vnd_to_usd(constraints.budget_cap_vnd))
        support_usd = float(vnd_to_usd(constraints.sponsor_per_driver_vnd))
        if resolved_mode == "current":
            with st.form(f"{key_prefix}:limits"):
                budget_usd = st.number_input(
                    "Budget limit ($)",
                    min_value=0.0,
                    value=budget_usd,
                    step=10.0,
                )
                support_usd = st.number_input(
                    "Support per driver ($)",
                    min_value=0.0,
                    value=support_usd,
                    step=0.04,
                )
                limits_applied = st.form_submit_button(
                    "Apply limits", type="primary", width="stretch"
                )
        applied_constraints = (
            DecisionConstraints(
                horizon_minutes=constraints.horizon_minutes,
                budget_cap_vnd=usd_to_vnd(budget_usd),
                sponsor_per_driver_vnd=usd_to_vnd(support_usd),
            )
            if limits_applied
            else constraints
        )

        playback_action: str | None = None
        playback_speed = "Normal"
        if playback is not None and resolved_mode == "accelerated-production":
            st.subheader("Playback")
            st.caption(
                f"{playback.range_label} · Now {playback.current_time_label} · "
                f"Decision available at {playback.decision_time_label}"
            )
            if st.button(
                "Pause" if playback.running else "Play",
                disabled=playback.complete,
                key=f"{key_prefix}:play",
                width="stretch",
            ):
                playback_action = "PAUSE" if playback.running else "PLAY"
            if st.button(
                "Next 15 min",
                disabled=playback.running or playback.complete,
                key=f"{key_prefix}:next",
                width="stretch",
            ):
                playback_action = "NEXT"
            playback_speed_value = st.segmented_control(
                "Speed",
                ("Slow", "Normal", "Fast"),
                default="Normal",
                key=f"{key_prefix}:speed",
            )
            if playback_speed_value in {"Slow", "Normal", "Fast"}:
                playback_speed = playback_speed_value
        elif resolved_mode == "accelerated-production":
            st.caption(
                "Replaying a historical heatwave scenario, fully pre-processed by "
                "BigQuery ML, TimeFM, and the Safety Optimizer."
            )

        refresh_requested = False
        reset_requested = False
        if resolved_mode == "current":
            refresh_requested = st.button(
                "Refresh conditions",
                key=f"{key_prefix}:refresh",
                width="stretch",
            )
            reset_requested = st.button(
                "Reset view",
                key=f"{key_prefix}:reset",
                width="stretch",
            )


    return OperatorSidebarResult(
        mode=resolved_mode,
        selected_zone_id=selected_zone_id,
        constraints=applied_constraints,
        limits_applied=limits_applied,
        playback_action=playback_action,
        playback_speed=playback_speed,
        refresh_requested=refresh_requested,
        reset_requested=reset_requested,
    )


__all__ = [
    "OperatorPlaybackView",
    "OperatorSidebarResult",
    "render_sidebar",
]
