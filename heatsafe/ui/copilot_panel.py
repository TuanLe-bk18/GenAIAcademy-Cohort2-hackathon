from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import streamlit as st

from heatsafe.copilot import CopilotEvidenceRepository, HeatSafeCopilot
from heatsafe.models import DecisionConstraints, ZoneSnapshot
from heatsafe.replay_copilot import ReplayCopilot, ReplayCopilotFrame
from heatsafe.repository import HybridRepository

COPILOT_STATE_VERSION = 18
PRODUCTION_COPILOT_NAMESPACE = "production_copilot"
REPLAY_COPILOT_NAMESPACE = "replay_copilot"


def _state_key(namespace: str, suffix: str) -> str:
    return f"{namespace}_{suffix}"


def _chat_context_requires_reset(
    *,
    previous_scope: object,
    scope: str,
    previous_context: object,
    context: str,
    reset_on_context_change: bool,
    previous_position: object,
    position: int | None,
) -> bool:
    rewound = (
        isinstance(position, int)
        and isinstance(previous_position, int)
        and position < previous_position
    )
    return (
        previous_scope != scope
        or rewound
        or (reset_on_context_change and previous_context != context)
    )


def _source_caption(tool: object, source_label: object) -> str:
    display_tool = {
        "get_replay_snapshot": "Operational conditions",
        "explain_replay_zone": "Area conditions",
        "compare_replay_areas": "Area comparison",
        "rank_safepause_optimizer_areas": "Optimizer priorities",
        "safepause_optimizer_no_action": "Optimizer status",
        "explain_replay_demand": "Demand forecast",
        "explain_city_safepause_decision": "City SafePause decision",
        "explain_replay_operational_impact": "Operational impact",
        "explain_safepause_decision": "SafePause decision",
        "compare_recorded_safepause_options": "SafePause options",
        "safepause_decision_pending": "SafePause status",
        "compare_replay_branches": "Outcome comparison",
        "get_replay_events": "Operational events",
        "get_replay_policy": "Operating policy",
    }.get(str(tool), "Verified evidence")
    suffix = f" · {source_label}" if source_label else ""
    return f"Verified source · {display_tool}{suffix}"


def create_copilot(
    zones: Sequence[ZoneSnapshot],
    scenario: str,
    constraints: DecisionConstraints,
    repository: CopilotEvidenceRepository | None = None,
) -> HeatSafeCopilot:
    """Create a copilot whose deterministic tools load current evidence on demand."""
    active_repository = repository or HybridRepository(scenario=scenario)
    return HeatSafeCopilot(
        list(zones),
        active_repository,
        default_constraints=constraints,
    )


def _render_chat(
    *,
    namespace: str,
    scope: str,
    context: str,
    caption: str | None,
    welcome: str | None,
    suggested_prompts: Mapping[str, str],
    answer_question: Callable[
        [str, Sequence[Mapping[str, Any]]], tuple[str, str]
    ],
    max_messages: int,
    reset_on_context_change: bool = True,
    source_label: str | None = None,
    position: int | None = None,
) -> None:
    """Render the shared chat shell around one context-bound answer function."""
    context_key = _state_key(namespace, "context")
    scope_key = _state_key(namespace, "scope")
    messages_key = _state_key(namespace, "messages")
    state_version_key = _state_key(namespace, "state_version")
    position_key = _state_key(namespace, "position")
    suggestion_nonce_key = _state_key(namespace, "suggestion_nonce")
    with st.container(key="gemini-copilot-header"):
        title_column, clear_column = st.columns([5, 1], vertical_alignment="center")
        with title_column:
            st.markdown("#### :material/auto_awesome: Gemini Copilot")
        with clear_column:
            clear_requested = st.button(
                "",
                icon=":material/delete_sweep:",
                help="Clear chat history",
                key=f"{namespace}-clear",
                width="stretch",
            )
        if caption:
            st.caption(caption)
    if st.session_state.get(state_version_key) != COPILOT_STATE_VERSION:
        st.session_state[messages_key] = []
        st.session_state.pop(suggestion_nonce_key, None)
    st.session_state[state_version_key] = COPILOT_STATE_VERSION
    previous_scope = st.session_state.get(scope_key)
    previous_context = st.session_state.get(context_key)
    previous_position = st.session_state.get(position_key)
    if _chat_context_requires_reset(
        previous_scope=previous_scope,
        scope=scope,
        previous_context=previous_context,
        context=context,
        reset_on_context_change=reset_on_context_change,
        previous_position=previous_position,
        position=position,
    ):
        st.session_state[messages_key] = []
    st.session_state[scope_key] = scope
    st.session_state[context_key] = context
    if position is not None:
        st.session_state[position_key] = position
    if clear_requested:
        st.session_state[messages_key] = []
    messages = st.session_state.setdefault(messages_key, [])

    limit = max(1, int(max_messages))
    history_container = st.container(
        height="stretch",
        border=False,
        key="gemini-copilot-history",
    )
    with history_container:
        if not messages and welcome:
            with st.chat_message("assistant", avatar=":material/auto_awesome:"):
                st.markdown(welcome)
        for message in messages[-limit:]:
            role = str(message.get("role", "assistant"))
            avatar = ":material/auto_awesome:" if role == "assistant" else None
            with st.chat_message(role, avatar=avatar):
                st.markdown(str(message.get("content", "")))
                if message.get("tool"):
                    st.caption(
                        _source_caption(
                            message["tool"], message.get("source_label")
                        )
                    )

    suggestion_nonce = int(
        st.session_state.setdefault(suggestion_nonce_key, 0)
    )
    with st.container(key="gemini-copilot-composer"):
        selected_suggestion = st.pills(
            "Suggested prompts",
            options=list(suggested_prompts),
            key=f"{namespace}-suggestions-{context}-{suggestion_nonce}",
        )
        typed_question = st.chat_input(
            "Ask Gemini Copilot...",
            key=f"{namespace}-input",
        )
    suggested_question = (
        suggested_prompts.get(selected_suggestion) if selected_suggestion else None
    )
    question = suggested_question or typed_question
    if not question:
        return
    if selected_suggestion:
        st.session_state[suggestion_nonce_key] = suggestion_nonce + 1

    history = tuple(messages[-10:])
    messages.append(
        {
            "role": "user",
            "content": question,
            "context": context,
            "scope": scope,
        }
    )
    with history_container:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant", avatar=":material/auto_awesome:"):
            thinking = st.empty()
            thinking.markdown(
                '<div class="operator-ai-thinking" role="status" aria-live="polite">'
                '<span>Gemini is thinking</span>'
                '<span class="operator-ai-thinking-dots" aria-hidden="true">'
                '<span>.</span><span>.</span><span>.</span>'
                '</span></div>',
                unsafe_allow_html=True,
            )
            try:
                answer, tool = answer_question(question, history)
            except Exception as exc:
                answer = (
                    "Gemini Copilot could not load verified evidence for this request."
                )
                tool = f"copilot_unavailable:{type(exc).__name__}"
            finally:
                thinking.empty()
            st.markdown(answer)
            st.caption(_source_caption(tool, source_label))
    messages.append(
        {
            "role": "assistant",
            "content": answer,
            "tool": tool,
            "source_label": source_label,
            "context": context,
            "scope": scope,
        }
    )
    stored_limit = max(20, limit * 2)
    messages[:] = messages[-stored_limit:]
    st.rerun()


def render_copilot_panel(
    zones: Sequence[ZoneSnapshot],
    selected_zone: ZoneSnapshot,
    scenario: str,
    constraints: DecisionConstraints,
    *,
    repository: CopilotEvidenceRepository | None = None,
    max_messages: int = 6,
    refresh_token: str | None = None,
) -> None:
    """Render the current-conditions Copilot against one evidence repository."""
    context = (
        f"{COPILOT_STATE_VERSION}:current:{scenario}:{selected_zone.zone_id}:"
        f"{selected_zone.snapshot_id}:{constraints.horizon_minutes}:"
        f"{constraints.budget_cap_vnd}:{constraints.sponsor_per_driver_vnd}:"
        f"{refresh_token or 'current'}"
    )
    prompts = {
        f"Why is {selected_zone.name} prioritized?": (
            f"Why is {selected_zone.name} prioritized now?"
        ),
        "Forecast the next 60 minutes": (
            f"Forecast demand in {selected_zone.name} for the next 60 minutes"
        ),
        "Compare SafePause options": (
            f"Compare SafePause options in {selected_zone.name} with the current budget"
        ),
        "Where should we intervene?": (
            "Which area should we intervene in under the current constraints?"
        ),
    }

    def answer_question(
        question: str, history: Sequence[Mapping[str, Any]]
    ) -> tuple[str, str]:
        return create_copilot(
            zones, scenario, constraints, repository=repository
        ).answer(question, history)

    with st.container(key="gemini-copilot-shell"):
        _render_chat(
            namespace=PRODUCTION_COPILOT_NAMESPACE,
            scope="current",
            context=context,
            caption=None,
            welcome=None,
            suggested_prompts=prompts,
            answer_question=answer_question,
            max_messages=max_messages,
        )


def render_replay_copilot_panel(
    replay_frame: ReplayCopilotFrame,
    *,
    max_messages: int = 8,
) -> None:
    """Render Gemini against only the currently displayed EVENT REPLAY frame."""
    context = (
        f"{COPILOT_STATE_VERSION}:replay:{replay_frame.tick_index}:"
        f"{replay_frame.branch}:{replay_frame.scope}:"
        f"{replay_frame.selected_zone_id or 'citywide'}:"
        f"{replay_frame.provenance.get('source_state_checksum', 'artifact')}"
    )
    zone_name = replay_frame.selected_zone_name
    scope_label = (
        zone_name if replay_frame.scope == "district" else "All Districts"
    )
    safepause_label, safepause_question = (
        (
            "SafePause decision status",
            "Is a SafePause decision available at the current operational time?",
        )
        if replay_frame.tick_index < replay_frame.decision_tick
        else (
            "Compare SafePause options",
            "Compare SafePause options across all areas",
        )
    )
    if replay_frame.scope == "district":
        condition_label = f"{zone_name} conditions now"
        condition_question = (
            f"Explain conditions in {zone_name} at the current operational time"
        )
    else:
        condition_label = "City conditions now"
        condition_question = (
            "Summarize conditions across all districts at the current operational time"
        )
    prompts = {
        condition_label: condition_question,
        "Explain demand": "Explain trip demand across all areas at the current operational time",
        safepause_label: safepause_question,
        "Priority areas now": "Which areas have the highest current operational priority?",
    }
    context_label = (
        f"{replay_frame.time_label} · {scope_label} · "
        f"{replay_frame.branch.replace('_', '-')}"
    )
    conversation_scope = (
        f"operations:{replay_frame.branch}:{replay_frame.scope}:"
        f"{replay_frame.selected_zone_id or 'citywide'}"
    )
    copilot = ReplayCopilot(replay_frame)
    with st.container(key="gemini-copilot-shell"):
        _render_chat(
            namespace=REPLAY_COPILOT_NAMESPACE,
            scope=conversation_scope,
            context=context,
            caption=context_label,
            welcome=None,
            suggested_prompts=prompts,
            answer_question=copilot.answer,
            max_messages=max_messages,
            reset_on_context_change=False,
            source_label=context_label,
            position=replay_frame.tick_index,
        )


__all__ = [
    "COPILOT_STATE_VERSION",
    "PRODUCTION_COPILOT_NAMESPACE",
    "REPLAY_COPILOT_NAMESPACE",
    "create_copilot",
    "render_copilot_panel",
    "render_replay_copilot_panel",
]
