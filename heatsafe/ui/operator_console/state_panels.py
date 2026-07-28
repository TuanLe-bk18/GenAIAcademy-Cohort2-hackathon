"""Reusable semantic state panels."""

from __future__ import annotations

import streamlit as st


def render_loading_state(message: str = "Loading current conditions…") -> None:
    st.info(message, icon=":material/progress_activity:")


def render_recommendation_unavailable(reason: str = "") -> None:
    st.warning(
        "Recommendation temporarily unavailable\n\n"
        "City heat and driver monitoring are still available. "
        "Action is paused until current evidence is verified."
        + (f" {reason}" if reason else ""),
        icon=":material/warning:",
    )


def render_no_safe_plan(reason: str = "") -> None:
    st.error(
        "No safe plan fits the current limits\n\n"
        "Available options exceed at least one service or budget limit."
        + (f" {reason}" if reason else ""),
        icon=":material/block:",
    )


def render_monitoring_state(next_time_label: str = "") -> None:
    message = "Monitoring conditions"
    if next_time_label:
        message += f" · The next recommendation will be available at {next_time_label}."
    st.info(message, icon=":material/visibility:")


def render_complete_state() -> None:
    st.success(
        "Simulation complete\n\nReview the outcome comparison below.",
        icon=":material/check_circle:",
    )


__all__ = [
    "render_complete_state",
    "render_loading_state",
    "render_monitoring_state",
    "render_no_safe_plan",
    "render_recommendation_unavailable",
]
