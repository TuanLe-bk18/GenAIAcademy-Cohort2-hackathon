from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import streamlit as st

from heatsafe.audit import HybridInterventionAuditStore
from heatsafe.config import Settings
from heatsafe.currency import vnd_to_usd
from heatsafe.models import DecisionConstraints
from heatsafe.repository import (
    HybridRepository,
    ReplayRunProgress,
    ReplayRunSummary,
)
from heatsafe.risk import operational_priority
from heatsafe.services.decision_service import (
    CityWidePlan,
    SelectedZoneDecision,
    build_city_wide_plan,
    build_selected_zone_decision,
)
from heatsafe.telemetry import log_event
from heatsafe.ui import (
    advance_refresh_token,
    build_constraints,
    initialize_state,
    render_city_intelligence,
    render_copilot_panel,
    render_decision_workspace,
    render_driver_evidence,
    render_model_performance,
    replay_run_label,
    replay_tick_time,
    render_styles,
)

DECISION_HORIZON_MINUTES = 240

st.set_page_config(page_title="HeatSafe AI Ops", page_icon="☀️", layout="wide")
render_styles()


def repository_snapshot(
    scenario: str,
    *,
    run_id: str | None = None,
    tick_index: int | None = None,
):
    repository = HybridRepository(scenario=scenario)
    if run_id is not None and tick_index is not None:
        return repository, repository.load_replay_tick(run_id, tick_index)
    return repository, repository.load()


@st.cache_data(ttl=300, show_spinner=False)
def load_snapshot(
    scenario: str,
    refresh_token: str,
    run_id: str | None = None,
    tick_index: int | None = None,
):
    del refresh_token
    return repository_snapshot(
        scenario, run_id=run_id, tick_index=tick_index
    )[1]


@st.cache_data(ttl=10, show_spinner=False)
def load_replay_runs(
    scenario: str, refresh_token: str
) -> list[ReplayRunSummary]:
    del refresh_token
    return HybridRepository(scenario=scenario).list_replay_runs(limit=20)


@st.cache_data(ttl=10, show_spinner=False)
def load_replay_progress(
    scenario: str, run_id: str, refresh_token: str
) -> ReplayRunProgress:
    del refresh_token
    return HybridRepository(scenario=scenario).load_replay_progress(run_id)


@st.cache_data(ttl=900, show_spinner=False)
def load_zone_ai_summary(
    scenario: str,
    snapshot_id: str,
    refresh_token: str,
    run_id: str | None = None,
    tick_index: int | None = None,
) -> dict[str, float]:
    del refresh_token
    repository, _ = repository_snapshot(
        scenario, run_id=run_id, tick_index=tick_index
    )
    return repository.load_zone_risk_summary(snapshot_id)


@st.cache_data(ttl=900, show_spinner=False)
def load_selected_decision(
    scenario: str,
    zone_id: str,
    snapshot_id: str,
    constraints: DecisionConstraints,
    refresh_token: str,
    run_id: str | None = None,
    tick_index: int | None = None,
) -> SelectedZoneDecision:
    del refresh_token
    repository, result = repository_snapshot(
        scenario, run_id=run_id, tick_index=tick_index
    )
    zone = next(
        zone
        for zone in result.zones
        if zone.zone_id == zone_id and zone.snapshot_id == snapshot_id
    )
    return build_selected_zone_decision(repository, zone, constraints)


@st.cache_data(ttl=900, show_spinner="Analyzing city-wide AI interventions...")
def load_city_wide_ai_plan(
    scenario: str,
    snapshot_id: str,
    constraints: DecisionConstraints,
    refresh_token: str,
    run_id: str | None = None,
    tick_index: int | None = None,
) -> CityWidePlan:
    del refresh_token
    repository, result = repository_snapshot(
        scenario, run_id=run_id, tick_index=tick_index
    )
    return build_city_wide_plan(
        repository,
        result.zones,
        snapshot_id=snapshot_id,
        constraints=constraints,
    )


@st.cache_data(ttl=900, show_spinner=False)
def load_model_evaluation_history(
    scenario: str, refresh_token: str
) -> list[dict[str, Any]]:
    del refresh_token
    repository = HybridRepository(scenario=scenario)
    repository.load()
    return repository.load_model_evaluations(limit=10)


def render_header() -> str:
    brand_column, scenario_column = st.columns([5, 1], vertical_alignment="center")
    with brand_column:
        st.markdown(
            '<div class="ops-brand"><div class="ops-mark">H</div><div>'
            '<div class="ops-title">HeatSafe '
            '<span style="color:var(--ops-muted);font-weight:500">AI Ops</span></div>'
            '<div class="ops-subtitle">Hanoi fleet operations · extreme heat decision support</div>'
            '</div></div>',
            unsafe_allow_html=True,
        )
    with scenario_column:
        return st.selectbox(
            "Operating scenario",
            ("heatwave", "live"),
            format_func=lambda value: (
                "Heatwave replay" if value == "heatwave" else "Live weather"
            ),
            label_visibility="collapsed",
        )


def render_replay_controls(
    scenario: str, refresh_token: str
) -> tuple[str | None, int | None, ReplayRunProgress | None]:
    if scenario != "heatwave" or Settings.from_env().mode == "snapshot":
        return None, None, None
    try:
        runs = load_replay_runs(scenario, refresh_token)
    except Exception as exc:
        log_event(
            "replay_run_list_unavailable",
            severity="WARNING",
            error_type=type(exc).__name__,
        )
        return None, None, None
    if not runs:
        return None, None, None

    run_by_id = {run.simulation_run_id: run for run in runs}
    run_id = st.selectbox(
        "Replay run",
        options=list(run_by_id),
        format_func=lambda value: replay_run_label(run_by_id[value]),
        key="playback_run_id",
    )
    try:
        progress = load_replay_progress(scenario, run_id, refresh_token)
    except Exception as exc:
        st.warning(f"Replay progress unavailable ({type(exc).__name__}).")
        return None, None, None

    latest = progress.latest_succeeded_tick_index
    if latest is None:
        st.info("This replay has not committed its first tick yet.")
        st.stop()
    if progress.succeeded_ticks != latest + 1:
        st.error(
            "Replay history is non-contiguous; playback stopped to avoid "
            "showing the wrong tick."
        )
        st.stop()
    context = f"{scenario}:{run_id}"
    if st.session_state.get("playback_context") != context:
        st.session_state.playback_context = context
        st.session_state.playback_tick_index = latest
        st.session_state.playback_follow_latest = True
        st.session_state.playback_playing = False
        st.session_state.playback_speed_seconds = 2
        st.session_state.playback_last_advance_at = time.monotonic()

    follow_latest = st.toggle(
        "Follow latest committed tick",
        key="playback_follow_latest",
    )
    if follow_latest:
        st.session_state.playback_tick_index = latest
        st.session_state.playback_playing = False

    previous_column, play_column, next_column, live_column, speed_column = (
        st.columns([0.7, 1, 0.7, 1, 1.4], vertical_alignment="bottom")
    )
    with previous_column:
        if st.button(
            "← Previous",
            key="playback_previous",
            disabled=follow_latest
            or st.session_state.playback_tick_index <= 0,
            help="Previous committed tick",
            width="stretch",
        ):
            st.session_state.playback_tick_index -= 1
    with play_column:
        playing = bool(st.session_state.playback_playing)
        if st.button(
            "Pause" if playing else "Play",
            key="playback_toggle",
            disabled=follow_latest,
            width="stretch",
        ):
            st.session_state.playback_playing = not playing
    with next_column:
        if st.button(
            "Next →",
            key="playback_next",
            disabled=follow_latest
            or st.session_state.playback_tick_index >= latest,
            help="Next committed tick",
            width="stretch",
        ):
            st.session_state.playback_tick_index += 1
    with live_column:
        if st.button(
            "Latest",
            key="playback_latest",
            disabled=follow_latest,
            width="stretch",
        ):
            st.session_state.playback_tick_index = latest
    with speed_column:
        st.selectbox(
            "Playback speed",
            options=(1, 2, 5),
            format_func=lambda value: f"{value}s / tick",
            key="playback_speed_seconds",
            disabled=follow_latest,
        )

    selected_tick = st.slider(
        "Replay timeline",
        min_value=0,
        max_value=latest,
        key="playback_tick_index",
        disabled=follow_latest,
        help="Each tick represents 15 minutes of simulated Hanoi operations.",
    )
    selected_time = replay_tick_time(run_by_id[run_id], int(selected_tick))
    st.markdown(
        '<div class="ops-playback-strip">'
        f'<span>Tick <b>{selected_tick:02d}</b> / 95</span>'
        f'<span><b>{selected_time:%d %b %H:%M}</b> ICT (GMT+7)</span>'
        f'<span>{progress.succeeded_ticks} committed</span>'
        f'<span>{progress.failed_ticks} failed</span>'
        f'<span>{"LIVE FOLLOW" if follow_latest else "READ-ONLY PLAYBACK"}</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    if not follow_latest:
        st.caption(
            "Historical playback is read-only. Playback speed changes only "
            "the presentation; stored simulation data is unchanged."
        )
    return run_id, int(selected_tick), progress


def render_status(
    result,
    ai_summary_ready: bool,
    snapshot_id: str,
    *,
    playback_tick_index: int | None = None,
) -> None:
    if not result.data_fresh:
        tone = "warn"
        label = "Snapshot stale · monitoring only"
    elif ai_summary_ready:
        tone = "ok"
        label = "Decision engine ready"
    else:
        tone = "warn"
        label = "AI unavailable · monitoring only"
    scenario_label = "Heatwave replay" if result.zones[0].scenario_id == "heatwave" else "Live weather"
    tick_pill = (
        f'<span class="ops-pill">Tick {playback_tick_index:02d}</span>'
        if playback_tick_index is not None
        else ""
    )
    st.markdown(
        '<div class="ops-status-row">'
        f'<span class="ops-pill {tone}">● {label}</span>'
        f'<span class="ops-pill">{scenario_label}</span>'
        f'<span class="ops-pill">Snapshot {snapshot_id[:12]}</span>'
        f"{tick_pill}"
        f'<span class="ops-pill">{result.mode.upper()}</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    if result.freshness_warning:
        st.error(result.freshness_warning)


def select_zone_from_control() -> None:
    st.session_state.selected_zone_id = st.session_state.zone_selector_id


def render_decision_controls(ordered_zones) -> str:
    rank_by_id = {zone.zone_id: index for index, zone in enumerate(ordered_zones, 1)}
    zone_by_id = {zone.zone_id: zone for zone in ordered_zones}
    selected_zone_id = str(st.session_state.selected_zone_id)
    if st.session_state.get("zone_selector_id") != selected_zone_id:
        st.session_state.zone_selector_id = selected_zone_id
    zone_column, budget_column, sponsor_column = st.columns(
        [2.2, 1, 1], gap="medium", vertical_alignment="bottom"
    )
    with zone_column:
        selected_zone_id = st.selectbox(
            "Decision zone",
            options=[zone.zone_id for zone in ordered_zones],
            key="zone_selector_id",
            on_change=select_zone_from_control,
            format_func=lambda zone_id: (
                f"{rank_by_id[zone_id]}. {zone_by_id[zone_id].name} · "
                f"Priority {operational_priority(zone_by_id[zone_id])}/100 · "
                f"{zone_by_id[zone_id].heat_index_c:.1f}°C"
            ),
        )
    with budget_column:
        st.number_input(
            "Cost cap ($)",
            min_value=0.0,
            step=10.0,
            key="decision_budget_cap",
        )
    with sponsor_column:
        st.number_input(
            "Partner / driver ($)",
            min_value=0.0,
            step=0.04,
            key="decision_partner_credit",
        )
    selected_zone_id = str(selected_zone_id)
    st.session_state.selected_zone_id = selected_zone_id
    return selected_zone_id


scenario = render_header()
st.session_state.setdefault("refresh_token", uuid4().hex)
refresh_token = str(st.session_state.refresh_token)
replay_run_id, replay_tick_index, replay_progress = render_replay_controls(
    scenario, refresh_token
)
result = load_snapshot(
    scenario,
    refresh_token,
    replay_run_id,
    replay_tick_index,
)
zones = result.zones
if not zones:
    st.error("No operational zones are available for this scenario.")
    st.stop()

snapshot_id = zones[0].snapshot_id
audit = HybridInterventionAuditStore()
try:
    zone_risk = load_zone_ai_summary(
        scenario,
        snapshot_id,
        refresh_token,
        replay_run_id,
        replay_tick_index,
    )
    ai_summary_ready = True
except Exception as exc:
    zone_risk = {}
    ai_summary_ready = False
    log_event(
        "ai_zone_summary_unavailable",
        severity="WARNING",
        error_type=type(exc).__name__,
    )

ordered_zones = sorted(
    zones,
    key=lambda zone: zone_risk.get(
        zone.zone_id, float(operational_priority(zone))
    ),
    reverse=True,
)
initialize_state(
    scenario,
    snapshot_id,
    zones,
    ordered_zones=ordered_zones,
    selection_context=(
        f"{scenario}:{replay_run_id}"
        if replay_run_id is not None
        else None
    ),
)
render_status(
    result,
    ai_summary_ready,
    snapshot_id,
    playback_tick_index=replay_tick_index,
)
selected_zone_id = render_decision_controls(ordered_zones)
selected = next(zone for zone in zones if zone.zone_id == selected_zone_id)
constraints = build_constraints(DECISION_HORIZON_MINUTES)

selected_decision: SelectedZoneDecision | None = None
decision_error: Exception | None = None
try:
    selected_decision = load_selected_decision(
        scenario,
        selected.zone_id,
        selected.snapshot_id,
        constraints,
        refresh_token,
        replay_run_id,
        replay_tick_index,
    )
except Exception as exc:
    decision_error = exc
    log_event(
        "ai_decision_context_unavailable",
        severity="WARNING",
        zone_id=selected.zone_id,
        error_type=type(exc).__name__,
    )

render_decision_workspace(
    selected,
    selected_decision,
    constraints,
    expected_escalations=zone_risk.get(selected.zone_id),
    audit_store=audit,
    data_fresh=(
        result.data_fresh
        and (
            replay_run_id is None
            or bool(st.session_state.get("playback_follow_latest"))
        )
    ),
    error=decision_error,
)

try:
    city_plan = load_city_wide_ai_plan(
        scenario,
        snapshot_id,
        constraints,
        refresh_token,
        replay_run_id,
        replay_tick_index,
    )
except Exception as exc:
    city_plan = CityWidePlan(
        rows=(),
        unavailable_zones=(),
        constraints=constraints,
    )
    log_event(
        "city_ai_plan_unavailable",
        severity="WARNING",
        error_type=type(exc).__name__,
    )

try:
    evaluations = load_model_evaluation_history(scenario, refresh_token)
except Exception as exc:
    evaluations = []
    log_event(
        "model_evaluations_unavailable",
        severity="WARNING",
        error_type=type(exc).__name__,
    )

proposal = selected_decision.proposal if selected_decision is not None else None
st.divider()
city_tab, drivers_tab, copilot_tab, model_tab = st.tabs(
    ["CITY INTELLIGENCE", "DRIVER EVIDENCE", "COPILOT & AUDIT", "MODEL PERFORMANCE"]
)
with city_tab:
    render_city_intelligence(
        zones,
        zone_risk,
        city_plan,
        selection_context=f"{scenario}:{snapshot_id}",
    )
with drivers_tab:
    render_driver_evidence(proposal)
with copilot_tab:
    copilot_column, audit_column = st.columns([1.2, 1], gap="large")
    with copilot_column:
        if (
            replay_run_id is not None
            and not st.session_state.get("playback_follow_latest")
        ):
            st.markdown("#### HeatSafe Copilot")
            st.info(
                "Historical playback is read-only. Copilot stays disabled "
                "to prevent mixing this tick with current evidence."
            )
        else:
            render_copilot_panel(
                zones,
                selected,
                scenario,
                constraints,
                refresh_token=refresh_token,
            )
    with audit_column:
        st.markdown(f"#### {selected.name} simulation audit")
        if (
            replay_run_id is not None
            and not st.session_state.get("playback_follow_latest")
        ):
            st.info(
                "Historical audit details stay hidden until they can be "
                "filtered by exact run and tick lineage."
            )
        else:
            try:
                audit_rows = [
                    row
                    for row in audit.list_recent()
                    if row.get("zone_id") == selected.zone_id
                ]
            except Exception as exc:
                st.warning(
                    "Simulation audit is temporarily unavailable "
                    f"({type(exc).__name__})."
                )
            else:
                if audit_rows:
                    st.dataframe(
                        audit_rows, hide_index=True, width="stretch"
                    )
                else:
                    st.info("No simulated intervention recorded.")
with model_tab:
    render_model_performance(evaluations, proposal)

refresh_column, policy_column = st.columns([1, 5], vertical_alignment="center")
with refresh_column:
    if st.button("Refresh data", width="stretch"):
        advance_refresh_token()
        st.rerun()
with policy_column:
    st.caption(
        "AI failure policy: fail closed; monitoring remains available. "
        f"Current controls: ${vnd_to_usd(constraints.budget_cap_vnd):,.0f} cap · "
        f"${vnd_to_usd(constraints.sponsor_per_driver_vnd):,.2f} partner credit."
    )

if replay_run_id is not None:
    follow_latest = bool(st.session_state.get("playback_follow_latest"))
    playing = bool(st.session_state.get("playback_playing"))
    latest_tick = (
        replay_progress.latest_succeeded_tick_index
        if replay_progress is not None
        else None
    )
    if (
        playing
        and latest_tick is not None
        and replay_tick_index is not None
        and replay_tick_index >= latest_tick
    ):
        st.session_state.playback_playing = False
    elif follow_latest or playing:
        refresh_seconds = (
            2
            if follow_latest
            else int(st.session_state.get("playback_speed_seconds", 2))
        )

        @st.fragment(run_every=f"{refresh_seconds}s")
        def replay_heartbeat() -> None:
            now = time.monotonic()
            last_advance = float(
                st.session_state.get("playback_last_advance_at", now)
            )
            if now - last_advance < refresh_seconds * 0.8:
                return
            st.session_state.playback_last_advance_at = now
            if st.session_state.get("playback_follow_latest"):
                refreshed = load_replay_progress(
                    scenario, replay_run_id, uuid4().hex
                )
                refreshed_latest = refreshed.latest_succeeded_tick_index
                if (
                    refreshed_latest is not None
                    and refreshed_latest
                    != st.session_state.playback_tick_index
                ):
                    st.session_state.playback_tick_index = refreshed_latest
                    advance_refresh_token()
                    st.rerun()
            if st.session_state.get("playback_playing"):
                current = int(st.session_state.playback_tick_index)
                if latest_tick is not None and current < latest_tick:
                    st.session_state.playback_tick_index = current + 1
                    st.rerun()
                st.session_state.playback_playing = False

        replay_heartbeat()
