from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from heatsafe.copilot import HeatSafeCopilot
from heatsafe.models import DecisionConstraints, ZoneSnapshot
from heatsafe.repository import HybridRepository

COPILOT_STATE_VERSION = 6


def create_copilot(
    zones: Sequence[ZoneSnapshot],
    scenario: str,
    constraints: DecisionConstraints,
) -> HeatSafeCopilot:
    """Create a copilot whose deterministic tools load current evidence on demand."""
    repository = HybridRepository(scenario=scenario)
    return HeatSafeCopilot(
        list(zones),
        repository,
        default_constraints=constraints,
    )


def render_copilot_panel(
    zones: Sequence[ZoneSnapshot],
    selected_zone: ZoneSnapshot,
    scenario: str,
    constraints: DecisionConstraints,
    *,
    max_messages: int = 6,
    refresh_token: str | None = None,
) -> None:
    """Render suggested prompts and fail-closed copilot chat inside an error boundary."""
    st.markdown("#### HeatSafe Copilot")
    st.caption(
        "Gemini explains verified BigQuery ML outputs; it cannot approve decisions."
    )
    context = (
        f"{COPILOT_STATE_VERSION}:{scenario}:{selected_zone.zone_id}:"
        f"{selected_zone.snapshot_id}:{constraints.horizon_minutes}:"
        f"{constraints.budget_cap_vnd}:{constraints.sponsor_per_driver_vnd}:"
        f"{refresh_token or 'current'}"
    )
    if st.session_state.get("copilot_context") != context:
        st.session_state.copilot_context = context
        st.session_state.messages = []
    messages = st.session_state.setdefault("messages", [])

    suggested_prompts = (
        f"Why is {selected_zone.name} prioritized now?",
        f"Forecast demand in {selected_zone.name} for the next 60 minutes",
        f"Compare SafePause options in {selected_zone.name} with the current budget",
        "Which area should we intervene in under the current constraints?",
    )
    st.markdown(
        '<div class="ops-eyebrow" style="margin:.35rem 0 .45rem">Suggested questions</div>',
        unsafe_allow_html=True,
    )
    suggested_question: str | None = None
    suggestion_columns = st.columns(2, gap="small")
    for index, prompt in enumerate(suggested_prompts):
        with suggestion_columns[index % 2]:
            if st.button(
                prompt,
                key=f"copilot-suggestion-{context}-{index}",
                width="stretch",
            ):
                suggested_question = prompt

    limit = max(1, int(max_messages))
    for message in messages[-limit:]:
        with st.chat_message(message.get("role", "assistant")):
            st.markdown(str(message.get("content", "")))
            if message.get("tool"):
                st.caption(f"Verified tool trace: {message['tool']}")

    typed_question = st.chat_input("Ask HeatSafe Copilot...")
    question = suggested_question or typed_question
    if not question:
        return

    messages.append({"role": "user", "content": question})
    try:
        copilot = create_copilot(zones, scenario, constraints)
        answer, tool = copilot.answer(question)
    except Exception as exc:
        answer = (
            "HeatSafe Copilot could not load verified forecast or model evidence. "
            "Monitoring remains available; no recommendation was generated."
        )
        tool = f"copilot_unavailable:{type(exc).__name__}"
    messages.append({"role": "assistant", "content": answer, "tool": tool})
    st.rerun()


__all__ = ["COPILOT_STATE_VERSION", "create_copilot", "render_copilot_panel"]
