"""Selected-area recommendation and authoritative action intents."""

from __future__ import annotations

from html import escape

import streamlit as st

from .state_panels import render_no_safe_plan, render_recommendation_unavailable
from .view_models import OperatorAreaView, OperatorRecommendationView


def _record_confirmed_action(action: str, key_prefix: str) -> None:
    st.session_state[f"{key_prefix}:confirmed-action"] = action


@st.dialog("Confirm simulated decision")
def _confirm_decision(action: str, key_prefix: str) -> None:
    label = "Activate SafePause" if action == "ACTIVATE" else "Continue monitoring"
    st.write(f"Confirm **{label}** for the current city plan?")
    st.caption(
        "This is a synthetic environment. No driver notification or real dispatch is sent."
    )
    with st.container(horizontal=True):
        st.button(
            f"Confirm {label.lower()}",
            type="primary" if action == "ACTIVATE" else "secondary",
            key=f"{key_prefix}:confirm:{action}",
            on_click=_record_confirmed_action,
            args=(action, key_prefix),
        )
        st.button("Cancel", key=f"{key_prefix}:cancel:{action}")


def render_decision_card(
    area: OperatorAreaView | None,
    recommendation: OperatorRecommendationView,
    *,
    decision_available: bool = True,
    recording: bool = False,
    recorded_action: str | None = None,
    key_prefix: str = "operator-decision",
) -> str | None:
    """Render one selected-area card and return ``ACTIVATE`` or ``CONTINUE`` only.

    The caller remains responsible for applying that intent to the exact authoritative
    city plan and its existing freshness checks.
    """
    with st.container(border=True):
        st.subheader("Selected area")
        if area is None:
            render_recommendation_unavailable()
            return None
        st.markdown(
            f'<div class="operator-area-title">{escape(area.name)}</div>'
            f'<div class="operator-area-context">{escape(area.heat_state_label)} heat · '
            f'{area.heat_index_c:.1f}°C · {area.drivers_needing_break_now:,} '
            "drivers need a break now</div>",
            unsafe_allow_html=True,
        )
        if recommendation.state == "unavailable":
            render_recommendation_unavailable(recommendation.blocking_reason)
        elif recommendation.state == "blocked":
            render_no_safe_plan(recommendation.blocking_reason)
        else:
            st.markdown(
                '<div class="operator-recommendation">'
                f'<strong>{escape(recommendation.headline)}</strong>'
                f'<p>{escape(recommendation.group_summary)} · '
                f'{escape(recommendation.break_length_label)}<br/>'
                f'{escape(recommendation.explanation)}</p></div>',
                unsafe_allow_html=True,
            )
            for guard in recommendation.guardrails[:4]:
                state_class = "operator-pass" if guard.passed else "operator-fail"
                icon = "✓" if guard.passed else "!"
                st.markdown(
                    '<div class="operator-guard">'
                    f'<span class="operator-guard-label">{escape(guard.label)}</span>'
                    f'<span class="{state_class}">{icon} {escape(guard.value)} · '
                    f'{escape(guard.status_label)}</span></div>',
                    unsafe_allow_html=True,
                )
        if recorded_action in {"ACTIVATE", "CONTINUE"}:
            st.success(
                "SafePause activated for this simulation."
                if recorded_action == "ACTIVATE"
                else "Continue monitoring recorded for this simulation.",
                icon=":material/check_circle:",
            )
            st.caption("The decision is locked for this operational interval.")
            return None
        if not decision_available:
            st.caption("Monitoring conditions · action will be available at decision time.")
            return None
        confirmed_key = f"{key_prefix}:confirmed-action"
        confirmed_action = st.session_state.pop(confirmed_key, None)
        if confirmed_action in {"ACTIVATE", "CONTINUE"}:
            return str(confirmed_action)
        action: str | None = None
        with st.container(horizontal=True):
            if st.button(
                "Activate SafePause",
                type="primary",
                disabled=recording or not recommendation.can_activate,
                key=f"{key_prefix}:activate",
            ):
                _confirm_decision("ACTIVATE", key_prefix)
            if st.button(
                "Continue monitoring",
                disabled=recording,
                key=f"{key_prefix}:continue",
            ):
                _confirm_decision("CONTINUE", key_prefix)
        st.caption("Synthetic environment · no driver notification or real dispatch is sent.")
        return action


__all__ = ["render_decision_card"]
