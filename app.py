from __future__ import annotations

from typing import Any
from uuid import uuid4

import streamlit as st

from heatsafe.audit import HybridInterventionAuditStore
from heatsafe.currency import vnd_to_usd
from heatsafe.models import DecisionConstraints
from heatsafe.repository import HybridRepository
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
    render_styles,
)

DECISION_HORIZON_MINUTES = 240

st.set_page_config(page_title="HeatSafe AI Ops", page_icon="☀️", layout="wide")
render_styles()


@st.cache_data(ttl=300, show_spinner=False)
def load_snapshot(scenario: str, refresh_token: str):
    del refresh_token
    return HybridRepository(scenario=scenario).load()


@st.cache_data(ttl=900, show_spinner=False)
def load_zone_ai_summary(
    scenario: str, snapshot_id: str, refresh_token: str
) -> dict[str, float]:
    del refresh_token
    repository = HybridRepository(scenario=scenario)
    repository.load()
    return repository.load_zone_risk_summary(snapshot_id)


@st.cache_data(ttl=900, show_spinner=False)
def load_selected_decision(
    scenario: str,
    zone_id: str,
    snapshot_id: str,
    constraints: DecisionConstraints,
    refresh_token: str,
) -> SelectedZoneDecision:
    del refresh_token
    repository = HybridRepository(scenario=scenario)
    result = repository.load()
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
) -> CityWidePlan:
    del refresh_token
    repository = HybridRepository(scenario=scenario)
    result = repository.load()
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


def render_status(result, ai_summary_ready: bool, snapshot_id: str) -> None:
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
    st.markdown(
        '<div class="ops-status-row">'
        f'<span class="ops-pill {tone}">● {label}</span>'
        f'<span class="ops-pill">{scenario_label}</span>'
        f'<span class="ops-pill">Snapshot {snapshot_id[:12]}</span>'
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
result = load_snapshot(scenario, refresh_token)
zones = result.zones
if not zones:
    st.error("No operational zones are available for this scenario.")
    st.stop()

snapshot_id = zones[0].snapshot_id
audit = HybridInterventionAuditStore()
try:
    zone_risk = load_zone_ai_summary(scenario, snapshot_id, refresh_token)
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
)
render_status(result, ai_summary_ready, snapshot_id)
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
    data_fresh=result.data_fresh,
    error=decision_error,
)

try:
    city_plan = load_city_wide_ai_plan(
        scenario,
        snapshot_id,
        constraints,
        refresh_token,
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
        render_copilot_panel(
            zones,
            selected,
            scenario,
            constraints,
            refresh_token=refresh_token,
        )
    with audit_column:
        st.markdown(f"#### {selected.name} simulation audit")
        try:
            audit_rows = [
                row
                for row in audit.list_recent()
                if row.get("zone_id") == selected.zone_id
            ]
        except Exception as exc:
            st.warning(
                f"Simulation audit is temporarily unavailable ({type(exc).__name__})."
            )
        else:
            if audit_rows:
                st.dataframe(audit_rows, hide_index=True, width="stretch")
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
