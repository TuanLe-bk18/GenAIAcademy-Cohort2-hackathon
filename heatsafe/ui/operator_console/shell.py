"""Composable public shell for integrating the console from ``app.py``."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from heatsafe.models import DecisionConstraints

from .evidence import render_evidence
from .operations import OperatorOperationsResult, render_operations
from .sidebar import OperatorPlaybackView, OperatorSidebarResult, render_sidebar
from .styles import render_styles
from .view_models import OperatorConsoleView


@dataclass(frozen=True)
class OperatorConsoleResult:
    surface: str
    sidebar: OperatorSidebarResult
    operations: OperatorOperationsResult | None
    evidence_view: str | None

    @property
    def decision_action(self) -> str | None:
        return self.operations.decision_action if self.operations is not None else None

    @property
    def selected_zone_id(self) -> str | None:
        if self.operations is not None and self.operations.selected_zone_id is not None:
            return self.operations.selected_zone_id
        return self.sidebar.selected_zone_id


def render_operator_console(
    view: OperatorConsoleView,
    constraints: DecisionConstraints,
    *,
    playback: OperatorPlaybackView | None = None,
    decision_available: bool = True,
    recording: bool = False,
    recorded_action: str | None = None,
    key_prefix: str = "operator-console",
) -> OperatorConsoleResult:
    """Render one console surface and return intents for authoritative app handlers."""
    render_styles()
    sidebar_result = render_sidebar(
        view,
        constraints,
        playback=playback,
        key_prefix=f"{key_prefix}:sidebar",
    )
    selected_surface = st.segmented_control(
        "Console view",
        ("Operations", "Evidence & history"),
        default="Operations",
        key=f"{key_prefix}:surface",
    )
    surface = (
        selected_surface
        if selected_surface in {"Operations", "Evidence & history"}
        else "Operations"
    )
    operations_result: OperatorOperationsResult | None = None
    evidence_view: str | None = None
    if surface == "Operations":
        operations_result = render_operations(
            view,
            decision_available=decision_available,
            recording=recording,
            recorded_action=recorded_action,
            key_prefix=f"{key_prefix}:operations",
        )
    else:
        evidence_view = render_evidence(
            view.evidence_summary,
            key_prefix=f"{key_prefix}:evidence",
        )
    return OperatorConsoleResult(
        surface=surface,
        sidebar=sidebar_result,
        operations=operations_result,
        evidence_view=evidence_view,
    )


__all__ = ["OperatorConsoleResult", "render_operator_console"]
