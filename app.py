from __future__ import annotations

import time
from dataclasses import replace
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import streamlit as st

from heatsafe.audit import HybridInterventionAuditStore
from heatsafe.models import (
    DecisionConstraints,
    PredictiveCityPlan,
    SimulatedControlReceipt,
)
from heatsafe.operational_runtime import (
    activate_simulated_plan,
    continue_without_intervention,
)
from heatsafe.production_mode import (
    ProductionSession,
    SessionChoice,
    build_production_evidence,
)
from heatsafe.services.decision_service import SelectedZoneDecision
from heatsafe.services.preventive_planning import (
    build_accelerated_forecast_input,
    build_predictive_city_plan,
    project_city_forecast,
)
from heatsafe.simulation.engine import load_zone_priors
from heatsafe.telemetry import log_event
from heatsafe.ui import advance_refresh_token
from heatsafe.ui.operator_console import (
    OperatorPlaybackView,
    OperatorRecommendationView,
    build_operator_console_view,
    build_safepause_outcome_view,
    format_hanoi_range,
    format_hanoi_time,
    render_evidence,
    render_operations,
    render_presentation_playback,
    render_sidebar,
    render_styles,
)
from heatsafe.ui.production_mode import get_production_session
from heatsafe.ui.state import (
    DEFAULT_BUDGET_USD,
    DEFAULT_SPONSOR_USD,
    build_constraints,
)

DECISION_HORIZON_MINUTES = 120
MODE_KEY = "operator-console:sidebar:mode"
AREA_KEY = "operator-console:sidebar:area"
SPEED_KEY = "operator-console:sidebar:speed"
SURFACE_KEY = "operator-console:surface"
SESSION_KEY = "production_window_session"

st.set_page_config(
    page_title="HeatSafe AI Ops",
    page_icon=":material/health_and_safety:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.session_state.setdefault("refresh_token", uuid4().hex)
st.session_state.setdefault("decision_budget_cap", DEFAULT_BUDGET_USD)
st.session_state.setdefault("decision_partner_credit", DEFAULT_SPONSOR_USD)
st.session_state.setdefault("selected_zone_id", None)
st.session_state.setdefault("operator_recording", False)

pending_zone = st.session_state.pop("operator_pending_zone", None)
if pending_zone is not None:
    st.session_state.selected_zone_id = pending_zone
    st.session_state[AREA_KEY] = pending_zone


def _constraints() -> DecisionConstraints:
    return build_constraints(DECISION_HORIZON_MINUTES)


@st.cache_resource(show_spinner="Loading verified current conditions…")
def load_current_session() -> ProductionSession:
    """Load the canonical decision point once without exposing simulation internals."""
    session = ProductionSession.create()
    while session.current_tick < session.window.decision_tick:
        session.advance()
    if session.status != "AWAITING_DECISION" or session.decision_evidence is None:
        raise RuntimeError("verified current conditions did not reach a decision state")
    return session


def _playback_view(session: ProductionSession) -> OperatorPlaybackView:
    current = session.actual_result.simulation_time
    start = current - timedelta(
        minutes=(session.current_tick - session.window.start_tick) * 15
    )
    end = current + timedelta(
        minutes=(session.window.end_tick - session.current_tick) * 15
    )
    decision = current + timedelta(
        minutes=(session.window.decision_tick - session.current_tick) * 15
    )
    return OperatorPlaybackView(
        range_label=format_hanoi_range(start, end),
        current_time_label=format_hanoi_time(current),
        decision_time_label=format_hanoi_time(decision),
        running=session.status == "RUNNING",
        complete=session.status == "COMPLETED",
    )


def _advance_once(
    session: ProductionSession,
    minimum_interval: float,
    *,
    force: bool = False,
) -> None:
    """Serialize an expensive simulation advance and keep the last good frame visible."""
    if session.status in {"AWAITING_DECISION", "COMPLETED"}:
        return
    if (session.status != "RUNNING" and not force) or st.session_state.get(
        "operator_advancing"
    ):
        return
    now = time.monotonic()
    last = float(st.session_state.get("production_window_last_advance", now))
    if now - last < minimum_interval * 0.8:
        return
    st.session_state.operator_advancing = True
    try:
        session.advance()
        st.session_state.production_window_last_advance = now
        st.session_state.pop("operator_advance_error", None)
    except Exception as exc:  # monitoring must remain visible on an advance failure
        st.session_state.operator_advance_error = type(exc).__name__
        log_event(
            "operator_playback_advance_failed",
            severity="ERROR",
            error_type=type(exc).__name__,
        )
        session.pause()
    finally:
        st.session_state.operator_advancing = False


def _selected_decision(
    evidence: Any,
    zones: tuple[Any, ...],
    selected_zone_id: str,
    constraints: DecisionConstraints,
) -> SelectedZoneDecision | None:
    zone = next((item for item in zones if item.zone_id == selected_zone_id), None)
    if zone is None:
        return None
    forecast = evidence.forecast_for(selected_zone_id)
    recommendation = evidence.recommendation_for(selected_zone_id)
    if forecast is None or recommendation is None:
        return None
    return SelectedZoneDecision(
        zone=zone,
        constraints=constraints,
        forecast=forecast,
        predictions=tuple(
            item for item in evidence.predictions if item.zone_id == selected_zone_id
        ),
        recommendation=recommendation,
        rule_reference=None,
    )


def _monitoring_recommendation(decision_time: str) -> OperatorRecommendationView:
    return OperatorRecommendationView(
        state="monitoring",
        headline="Monitoring conditions",
        explanation=f"The next recommendation will be available at {decision_time}.",
        driver_count=0,
        start_time_label="—",
        group_summary="No action required yet",
        break_length_label="—",
        coverage_summary="Monitoring continues",
        order_impact_summary="—",
        pickup_delay_summary="—",
        cost_summary="—",
        guardrails=(),
        can_activate=False,
        blocking_reason="",
    )


def _history_for_choice(
    session: ProductionSession | None,
    plan: PredictiveCityPlan | None,
) -> tuple[dict[str, object], ...]:
    if session is None or session.choice is None or plan is None:
        return ()
    protected = sum(
        row.best_window.proposal.selected_drivers
        for row in plan.rows
        if row.zone_id in plan.selected_zone_ids and row.best_window is not None
    )
    return (
        {
            "recorded_at": session.actual_result.simulation_time,
            "choice": session.choice,
            "protected_driver_count": protected if session.choice == "ACTIVATE" else 0,
            "result": "Running" if session.status == "RUNNING" else session.status,
            "coverage": "City plan",
        },
    )


def _recorded_action(
    mode: str,
    session: ProductionSession | None,
    snapshot_id: str,
) -> str | None:
    if session is not None:
        return session.choice
    record = st.session_state.get("operator_recorded_decision")
    if (
        mode == "current"
        and isinstance(record, dict)
        and record.get("snapshot_id") == snapshot_id
        and record.get("action") in {"ACTIVATE", "CONTINUE"}
    ):
        return str(record["action"])
    return None


def _apply_action(
    action: str,
    *,
    mode: str,
    session: ProductionSession | None,
    plan: PredictiveCityPlan,
    snapshot_id: str,
) -> None:
    if action not in {"ACTIVATE", "CONTINUE"}:
        raise RuntimeError(f"unsupported city plan action: {action!r}")
    if session is not None:
        choice = cast(SessionChoice, action)
        proposals = tuple(
            row.best_window.proposal
            for row in plan.rows
            if row.zone_id in plan.selected_zone_ids and row.best_window is not None
        )
        session.choose(choice, proposals=proposals)
        st.session_state.production_window_last_advance = time.monotonic()
        return

    audit = HybridInterventionAuditStore()
    receipt: SimulatedControlReceipt
    if action == "ACTIVATE":
        receipt = activate_simulated_plan(
            plan,
            audit_store=audit,
            current_snapshot_id=snapshot_id,
        )
    else:
        receipt = continue_without_intervention(
            plan,
            current_snapshot_id=snapshot_id,
        )
    st.session_state.operator_recorded_decision = {
        "snapshot_id": snapshot_id,
        "action": action,
        "status": receipt.status,
    }


active_mode = str(st.session_state.get(MODE_KEY, "current"))
active_session: ProductionSession | None = None
render_styles()
sidebar_result = render_sidebar(
    None,
    _constraints(),
    playback=None,
    mode=active_mode,
    area_options=tuple(
        (area.zone_id, area.name) for area in load_zone_priors()
    ),
    system_details={
        "Environment": "Synthetic Hanoi operations",
        "Appearance": f"{(st.context.theme.type or 'dark').title()} theme",
        "Decision state": (
            "Display-only replay"
            if active_mode == "accelerated-production"
            else "Ready"
        ),
    },
    key_prefix="operator-console:sidebar",
)
selected_surface = st.segmented_control(
    "Console view",
    ("Operations", "Evidence & history"),
    default="Operations",
    key=SURFACE_KEY,
)
surface = (
    selected_surface
    if selected_surface in {"Operations", "Evidence & history"}
    else "Operations"
)
presentation_mode = (
    sidebar_result.mode == "accelerated-production"
    and surface == "Operations"
)
if sidebar_result.mode == "accelerated-production" and not presentation_mode:
    active_session = get_production_session()

if sidebar_result.limits_applied:
    st.session_state.decision_budget_cap = (
        sidebar_result.constraints.budget_cap_vnd / 25_000
    )
    st.session_state.decision_partner_credit = (
        sidebar_result.constraints.sponsor_per_driver_vnd / 25_000
    )
    st.session_state.pop("operator_recorded_decision", None)
    st.rerun()
if sidebar_result.refresh_requested:
    advance_refresh_token()
    st.rerun()
if sidebar_result.reset_requested:
    st.session_state.pop("operator_recorded_decision", None)
    st.session_state.pop("operator_advance_error", None)
    if isinstance(active_session, ProductionSession):
        active_session.reset()
        st.session_state.production_window_last_advance = time.monotonic()
    st.rerun()
if isinstance(active_session, ProductionSession) and sidebar_result.playback_action:
    if sidebar_result.playback_action == "PLAY":
        active_session.start()
        st.session_state.production_window_last_advance = time.monotonic()
    elif sidebar_result.playback_action == "PAUSE":
        active_session.pause()
    elif sidebar_result.playback_action == "NEXT":
        _advance_once(active_session, 0.0, force=True)
    st.rerun()

speed_seconds = {
    "Slow": 5,
    "Normal": 3,
    "Fast": 2,
}.get(str(st.session_state.get(SPEED_KEY, "Normal")), 3)
auto_refresh = (
    f"{speed_seconds}s"
    if surface == "Operations"
    and isinstance(active_session, ProductionSession)
    and active_session.status == "RUNNING"
    else None
)



@st.fragment(run_every=auto_refresh)
def live_operator_workspace() -> None:
    mode = str(st.session_state.get(MODE_KEY, "current"))
    accelerated = mode == "accelerated-production"
    session = get_production_session() if accelerated else None
    evidence_session = session if session is not None else load_current_session()

    if session is not None and session.status == "RUNNING":
        interval = {
            "Slow": 5,
            "Normal": 3,
            "Fast": 2,
        }.get(str(st.session_state.get(SPEED_KEY, "Normal")), 3)
        _advance_once(session, interval)

    constraints = _constraints()
    evidence = (
        evidence_session.decision_evidence
        if evidence_session.status == "AWAITING_DECISION"
        and evidence_session.decision_evidence is not None
        else build_production_evidence(
            evidence_session.actual_result,
            fixture=evidence_session.fixture,
            zones=evidence_session.zones,
            constraints=constraints,
        )
    )
    zones = tuple(evidence.zones)
    valid_zone_ids = {zone.zone_id for zone in zones}
    selected_zone_id = str(
        st.session_state.get(AREA_KEY)
        or st.session_state.get("selected_zone_id")
        or zones[0].zone_id
    )
    if selected_zone_id not in valid_zone_ids:
        selected_zone_id = zones[0].zone_id
    st.session_state.selected_zone_id = selected_zone_id

    plan: PredictiveCityPlan | None = None
    planning_issue: str | None = None
    try:
        forecast_input = build_accelerated_forecast_input(
            evidence_session.actual_result,
            fixture=evidence_session.fixture,
            zones=evidence_session.zones,
        )
        plan = build_predictive_city_plan(
            project_city_forecast(forecast_input), constraints
        )
    except Exception as exc:
        planning_issue = type(exc).__name__
        log_event(
            "predictive_city_plan_unavailable",
            severity="WARNING",
            error_type=planning_issue,
        )

    selected_decision = _selected_decision(
        evidence, zones, selected_zone_id, constraints
    )
    outcome = None
    if session is not None and session.choice == "ACTIVATE":
        outcome = build_safepause_outcome_view(
            session.actual_history,
            session.shadow_history,
            decision_tick=session.window.decision_tick,
        )
    history = _history_for_choice(session, plan)
    view = build_operator_console_view(
        plan,
        zones,
        constraints,
        selected_zone_id=selected_zone_id,
        selected_decision=selected_decision,
        outcome=outcome,
        history=history,
    )

    decision_available = session is None or session.status == "AWAITING_DECISION"
    if session is not None and not decision_available and session.choice is None:
        view = replace(
            view,
            recommendation=_monitoring_recommendation(
                _playback_view(session).decision_time_label
            ),
        )
    snapshot_id = zones[0].snapshot_id
    recorded = _recorded_action(mode, session, snapshot_id)
    operations_result = None
    if surface == "Operations":
        operations_result = render_operations(
            view,
            decision_available=decision_available,
            recording=bool(st.session_state.get("operator_recording")),
            recorded_action=recorded,
            key_prefix="operator-console:operations",
        )
    else:
        render_evidence(
            view.evidence_summary,
            key_prefix="operator-console:evidence",
        )

    if planning_issue:
        st.warning(
            "Recommendation data is updating; monitoring remains available. "
            "Action is paused until the latest evidence is verified.",
            icon=":material/warning:",
        )
    advance_error = st.session_state.get("operator_advance_error")
    if advance_error:
        st.warning(
            "Playback paused because the next interval could not be prepared. "
            "The last completed conditions remain visible.",
            icon=":material/pause_circle:",
        )

    if (
        operations_result is not None
        and operations_result.selected_zone_id is not None
        and operations_result.selected_zone_id != selected_zone_id
    ):
        st.session_state.operator_pending_zone = operations_result.selected_zone_id
        st.rerun()

    if (
        operations_result is not None
        and operations_result.decision_action is not None
        and plan is not None
        and recorded is None
    ):
        st.session_state.operator_recording = True
        try:
            _apply_action(
                operations_result.decision_action,
                mode=mode,
                session=session,
                plan=plan,
                snapshot_id=snapshot_id,
            )
        finally:
            st.session_state.operator_recording = False
        st.rerun()


if presentation_mode:
    render_presentation_playback()
else:
    live_operator_workspace()
