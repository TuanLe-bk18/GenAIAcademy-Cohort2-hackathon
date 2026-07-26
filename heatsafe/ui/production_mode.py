"""Accelerated clock controls for the shared city-planner visualization."""

from __future__ import annotations

import time

import streamlit as st

from heatsafe.production_mode import ProductionSession


def get_production_session() -> ProductionSession:
    value = st.session_state.get("production_window_session")
    if not isinstance(value, ProductionSession):
        with st.spinner("Loading verified K-8 operational state..."):
            value = ProductionSession.create()
        st.session_state.production_window_session = value
        st.session_state.production_window_last_advance = time.monotonic()
    return value


def render_production_mode() -> ProductionSession:
    """Render only clock controls; the shared planner renders all evidence."""
    session = get_production_session()
    window = session.window
    st.markdown("## Production · Accelerated operational window")
    st.caption(
        "Synthetic Hanoi operations · server-side stateful engine · "
        "1 tick = 15 operational minutes · no real dispatch"
    )
    progress = (session.current_tick - window.start_tick) / (
        window.end_tick - window.start_tick
    )
    st.progress(max(0.0, min(1.0, progress)))
    st.markdown(
        f"**Tick {session.current_tick} / {window.end_tick}** · "
        f"{session.actual_result.simulation_time:%d %b %Y %H:%M} ICT · "
        f"decision K={window.decision_tick} · **{session.status}**"
    )

    start, pause, advance, reset = st.columns(4, vertical_alignment="bottom")
    with start:
        if st.button(
            "Start",
            disabled=session.status not in {"READY"},
            width="stretch",
            key="production_start",
        ):
            session.start()
            st.rerun()
    with pause:
        if st.button(
            "Pause",
            disabled=session.status != "RUNNING",
            width="stretch",
            key="production_pause",
        ):
            session.pause()
            st.rerun()
    with advance:
        if st.button(
            "Advance 15 min",
            disabled=session.status in {"AWAITING_DECISION", "COMPLETED"},
            width="stretch",
            key="production_advance",
        ):
            session.advance()
            st.rerun()
    with reset:
        if st.button("Reset run", width="stretch", key="production_reset"):
            session.reset()
            st.rerun()
    playback_speed = st.segmented_control(
        "Playback speed",
        options=(2, 3, 5),
        default=3,
        format_func=lambda value: f"{value}s / tick",
        key="production_window_speed",
    )
    if session.status == "AWAITING_DECISION":
        st.warning(
            "Decision point reached. Use the shared city-plan controls below; "
            "the operational clock is paused."
        )
    if session.choice is not None:
        st.success(
            "SafePause controls were scheduled from the shared city portfolio."
            if session.choice == "ACTIVATE"
            else "No-action choice recorded; actual follows the shadow baseline."
        )

    if session.status == "RUNNING":
        # A segmented control may legitimately return ``None`` on a rerun for
        # an older browser session that had no selected value.  The operational
        # clock must retain a deterministic cadence rather than crash on Start.
        refresh_seconds = playback_speed if playback_speed in {2, 3, 5} else 3

        @st.fragment(run_every=f"{refresh_seconds}s")
        def production_heartbeat() -> None:
            now = time.monotonic()
            last = float(st.session_state.production_window_last_advance)
            if now - last < refresh_seconds * 0.8:
                return
            st.session_state.production_window_last_advance = now
            session.advance()
            st.rerun()

        production_heartbeat()
    return session


__all__ = ["get_production_session", "render_production_mode"]
