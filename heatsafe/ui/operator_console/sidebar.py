"""Global operator controls for app orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

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


def render_sidebar(
    view: OperatorConsoleView | None,
    constraints: DecisionConstraints,
    *,
    playback: OperatorPlaybackView | None = None,
    mode: str | None = None,
    key_prefix: str = "operator-sidebar",
) -> OperatorSidebarResult:
    """Render the shared mode-only sidebar header.

    Area selection and replay playback belong to the primary operator component.
    Policy constraints are validated server settings and are returned unchanged.
    """
    del playback
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
        if resolved_mode == "accelerated-production":
            st.caption(
                "Replaying the reviewed historical heatwave scenario with the "
                "deterministic Safety Optimizer."
            )
        else:
            st.caption(
                "Monitoring pinned hackathon simulation evidence with fixed "
                "server-side safety policy."
            )

    return OperatorSidebarResult(
        mode=resolved_mode,
        selected_zone_id=None,
        constraints=constraints,
    )


__all__ = [
    "OperatorPlaybackView",
    "OperatorSidebarResult",
    "render_sidebar",
]
