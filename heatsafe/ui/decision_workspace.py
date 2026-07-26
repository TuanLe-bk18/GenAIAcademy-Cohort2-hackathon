from __future__ import annotations

from html import escape
from typing import Protocol
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from heatsafe.currency import vnd_to_usd
from heatsafe.models import (
    DecisionConstraints,
    InterventionEvent,
    RecommendationResult,
    SafePauseProposal,
    ZoneSnapshot,
)
from heatsafe.repository import DemandForecast
from heatsafe.risk import TIER_LABELS, heat_tier
from heatsafe.services.decision_service import SelectedZoneDecision

from .styles import PLOTLY_LAYOUT

HANOI_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TIER_CSS = {
    "NORMAL": "tier-safe",
    "CAUTION": "tier-caution",
    "EXTREME_CAUTION": "tier-caution",
    "DANGER": "tier-danger",
    "EXTREME_DANGER": "tier-extreme",
}


class AuditStore(Protocol):
    def approve(self, proposal: SafePauseProposal) -> InterventionEvent: ...


def _currency(value_vnd: int | float) -> str:
    return f"${vnd_to_usd(value_vnd):,.2f}"


def render_zone_header(
    zone: ZoneSnapshot,
    expected_escalations: float | None = None,
) -> None:
    """Render identity, heat, exposure, and CoolStop context for one zone."""
    tier = heat_tier(zone.heat_index_c)
    risk_display = "—" if expected_escalations is None else f"{expected_escalations:.2f}"
    st.markdown(
        '<section class="ops-panel ops-zone-head">'
        '<div class="ops-eyebrow">Selected decision zone</div>'
        f'<div class="ops-zone-name">{escape(zone.name)} '
        f'<span class="tier-badge {TIER_CSS.get(tier, "tier-safe")}">{escape(TIER_LABELS[tier])}</span></div>'
        '<div class="ops-zone-stats">'
        f'<div><div class="ops-stat-label">Heat Index</div><div class="ops-stat-value">{zone.heat_index_c:.1f}°C</div></div>'
        f'<div><div class="ops-stat-label">Expected escalations</div><div class="ops-stat-value" style="color:var(--ops-heat)">{risk_display}</div></div>'
        f'<div><div class="ops-stat-label">Active drivers</div><div class="ops-stat-value">{zone.active_drivers:,}</div></div>'
        f'<div><div class="ops-stat-label">Exposed 4h+</div><div class="ops-stat-value">{zone.exposed_4h:,}</div></div>'
        f'<div><div class="ops-stat-label">CoolStop</div><div class="ops-stat-value">{escape(zone.coolstop_name)}</div></div>'
        '</div></section>',
        unsafe_allow_html=True,
    )


def render_recommendation(
    recommendation: RecommendationResult | None,
    *,
    error: Exception | str | None = None,
) -> None:
    """Render a verified recommendation, alternatives, or fail-closed state."""
    if error is not None:
        st.warning(
            "Model evidence is unavailable. HeatSafe will not invent a plan; "
            "city monitoring remains available."
        )
        return
    if recommendation is None:
        st.info("A selected-zone recommendation has not been loaded.")
        return

    proposal = recommendation.recommended
    if proposal is None:
        st.error(recommendation.message or "No feasible SafePause plan was returned.")
        if recommendation.alternatives:
            rows = [
                {
                    "Drivers": item.selected_drivers,
                    "Pause": f"{item.pause_minutes}m",
                    "Waves": item.waves,
                    "Guardrail conflict": " · ".join(item.guardrail_notes),
                }
                for item in recommendation.alternatives[:5]
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        return

    waves_html = "".join(
        '<div class="ops-wave-item">'
        f'<div class="ops-wave-title">Wave {wave.wave} · {wave.selected_drivers} drivers</div>'
        f'<div class="ops-wave-meta">+{wave.start_minute}–{wave.end_minute} min · {wave.high_priority_drivers} mandatory</div>'
        '</div>'
        for wave in proposal.wave_plan
    )
    notes = " · ".join(escape(note) for note in proposal.guardrail_notes)
    st.markdown(
        '<section class="ops-rec" style="margin-top:.8rem">'
        '<div class="ops-rec-title">HeatSafe recommends</div>'
        f'<div class="ops-rec-name">SafePause · {proposal.waves} staggered waves</div>'
        f'<div class="ops-copy">{escape(proposal.decision_reason)}</div>'
        '<div class="ops-metric-grid">'
        f'<div class="ops-metric"><div class="ops-metric-label">Drivers selected</div><div class="ops-metric-value">{proposal.selected_drivers}/{proposal.eligible_drivers}</div></div>'
        f'<div class="ops-metric"><div class="ops-metric-label">Mandatory covered</div><div class="ops-metric-value ok">{proposal.mandatory_selected_drivers}/{proposal.mandatory_eligible_drivers}</div></div>'
        f'<div class="ops-metric"><div class="ops-metric-label">Expected prevented</div><div class="ops-metric-value cool">{proposal.expected_risk_events_prevented:.2f}</div></div>'
        f'<div class="ops-metric"><div class="ops-metric-label">Earnings Guard</div><div class="ops-metric-value">{_currency(proposal.earnings_guard_cost_vnd)}</div></div>'
        '</div><div class="ops-eyebrow">Staggered waves · preserves supply</div>'
        f'<div class="ops-wave">{waves_html}</div>'
        f'<div class="ops-guard">● {notes}</div></section>',
        unsafe_allow_html=True,
    )


def render_forecast(forecast: DemandForecast | None) -> None:
    """Render demand median and stress upper bound for the decision horizon."""
    if forecast is None or not forecast.points:
        return
    st.markdown(
        '<div class="ops-eyebrow" style="margin:1rem 0 .5rem">Demand forecast and recommendation evidence</div>',
        unsafe_allow_html=True,
    )
    times = [point.forecast_at.astimezone(HANOI_TZ) for point in forecast.points]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=times,
            y=[point.predicted_requests for point in forecast.points],
            name="Median demand",
            line={"color": "#72cbd0", "width": 2},
            fill="tozeroy",
            fillcolor="rgba(114,203,208,.06)",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=times,
            y=[point.upper_bound for point in forecast.points],
            name="Stress upper bound",
            line={"color": "#e5b158", "width": 1.5, "dash": "dot"},
        )
    )
    figure.update_layout(
        **PLOTLY_LAYOUT,
        margin={"l": 0, "r": 0, "t": 25, "b": 0},
        height=230,
        hovermode="x unified",
        xaxis={"gridcolor": "rgba(255,255,255,.04)"},
        yaxis={"gridcolor": "rgba(255,255,255,.04)", "title": "Requests"},
        legend={"orientation": "h", "y": 1.12},
    )
    st.plotly_chart(figure, width="stretch")


def render_business_impact(
    proposal: SafePauseProposal | None,
    constraints: DecisionConstraints,
) -> None:
    """Render stress-case SLA, cost, and driver-benefit guardrails."""
    st.markdown(
        '<section class="ops-panel"><div class="ops-panel-head">Business impact · stress case</div>',
        unsafe_allow_html=True,
    )
    if proposal is None:
        st.markdown(
            '<div class="ops-impact-row ops-copy">Impact projections require valid model evidence.</div></section>',
            unsafe_allow_html=True,
        )
        return

    fulfillment_drop = max(
        0.0,
        (proposal.baseline_stress_fulfillment_rate - proposal.p90_fulfillment_rate)
        * 100,
    )
    cost_percent = min(
        100.0,
        proposal.net_platform_cost_vnd / max(constraints.budget_cap_vnd, 1) * 100,
    )
    rows = (
        (
            "Fulfillment drop",
            f"{fulfillment_drop:.1f}%",
            min(100.0, fulfillment_drop / 2.0 * 100),
            "maximum 2.0%",
        ),
        (
            "ETA impact",
            f"+{proposal.p90_eta_increase_minutes:.1f}m",
            min(100.0, proposal.p90_eta_increase_minutes / 2.0 * 100),
            "maximum +2.0m",
        ),
        (
            "Net platform cost",
            _currency(proposal.net_platform_cost_vnd),
            cost_percent,
            f"limit {_currency(constraints.budget_cap_vnd)}",
        ),
    )
    for label, value, width, caption in rows:
        st.markdown(
            '<div class="ops-impact-row">'
            f'<div class="ops-impact-top"><span>{label}</span><b>{value}</b></div>'
            f'<div class="ops-track"><div class="ops-fill" style="width:{width:.0f}%"></div></div>'
            f'<div class="ops-caption">{caption}</div></div>',
            unsafe_allow_html=True,
        )
    st.markdown(
        '<div class="ops-impact-row" style="background:rgba(114,203,208,.045)">'
        '<div class="ops-eyebrow" style="color:var(--ops-cool)">Driver benefit</div>'
        f'<div class="ops-stat-value" style="color:var(--ops-cool)">{proposal.selected_drivers} drivers protected</div>'
        f'<div class="ops-caption">{proposal.exposure_minutes_avoided:,} recovery minutes · all mandatory 4h+ covered</div>'
        '</div></section>',
        unsafe_allow_html=True,
    )


def render_execution(
    zone: ZoneSnapshot,
    proposal: SafePauseProposal | None,
    audit_store: AuditStore | None,
    *,
    data_fresh: bool,
) -> InterventionEvent | None:
    """Render simulated execution and record an idempotent audit approval."""
    st.markdown(
        '<div class="ops-panel-head" style="margin-top:.8rem">Execute SafePause</div>',
        unsafe_allow_html=True,
    )
    if proposal is None:
        st.button("Activate SafePause", disabled=True, width="stretch")
        st.caption(
            "Demo execution only — no driver notification, hydration order, or operational command is sent."
        )
        return None

    st.markdown(
        f'<div class="ops-copy" style="padding:.65rem 0">{escape(zone.name)} · '
        f'{proposal.selected_drivers} drivers · {proposal.waves} waves · '
        f'{proposal.pause_minutes}m recovery</div>'
        '<div class="ops-execution-plan">'
        '<div class="ops-execution-row"><div class="ops-execution-icon">H</div>'
        '<div><div class="ops-execution-title">Activate hydration support</div>'
        f'<div class="ops-execution-meta">{proposal.selected_drivers} drivers · {_currency(proposal.partner_hydration_value_vnd)} partner value</div></div>'
        '<div class="ops-execution-state ready">Ready</div></div>'
        '<div class="ops-execution-row"><div class="ops-execution-icon">N</div>'
        '<div><div class="ops-execution-title">Notify selected drivers</div>'
        f'<div class="ops-execution-meta">Safety guidance and assigned recovery window · {proposal.selected_drivers} recipients</div></div>'
        '<div class="ops-execution-state ready">Ready</div></div>'
        '<div class="ops-execution-row"><div class="ops-execution-icon">W</div>'
        '<div><div class="ops-execution-title">Schedule staggered pause waves</div>'
        f'<div class="ops-execution-meta">{proposal.waves} waves · first wave starts now · supply guardrails active</div></div>'
        '<div class="ops-execution-state ready">Ready</div></div></div>',
        unsafe_allow_html=True,
    )
    confirm = st.checkbox(
        "I confirm this rollout plan and understand this is a demo environment",
        key=f"confirm-{proposal.proposal_id}",
    )
    event: InterventionEvent | None = None
    if st.button(
        "Activate SafePause",
        key=f"activate-{proposal.proposal_id}",
        disabled=not (
            confirm
            and proposal.within_guardrails
            and data_fresh
            and audit_store is not None
        ),
        width="stretch",
        type="primary",
    ):
        try:
            event = audit_store.approve(proposal) if audit_store is not None else None
        except Exception as exc:
            st.error(f"Could not record the simulated approval ({type(exc).__name__}).")
        else:
            if event is not None:
                st.session_state["simulated_execution"] = {
                    "proposal_id": proposal.proposal_id,
                    "intervention_id": event.intervention_id,
                }

    execution = st.session_state.get("simulated_execution")
    if execution and execution.get("proposal_id") == proposal.proposal_id:
        intervention_id = escape(str(execution.get("intervention_id", "unknown")))
        st.markdown(
            '<div class="ops-execution-result"><strong>SafePause activated · simulation</strong>'
            f'<div>Hydration support activated for {proposal.selected_drivers} drivers<br>'
            f'Driver notifications queued · {proposal.selected_drivers} recipients<br>'
            f'{proposal.waves} recovery waves scheduled · Audit {intervention_id[:8]}</div></div>',
            unsafe_allow_html=True,
        )
    st.caption(
        "Demo execution only — the workflow is recorded for audit, but no driver notification, "
        "hydration order, or operational command is sent."
    )
    return event


def render_decision_workspace(
    zone: ZoneSnapshot,
    decision: SelectedZoneDecision | None,
    constraints: DecisionConstraints,
    *,
    expected_escalations: float | None = None,
    audit_store: AuditStore | None = None,
    data_fresh: bool = True,
    show_execution: bool = True,
    show_recommendation: bool = True,
    error: Exception | str | None = None,
) -> InterventionEvent | None:
    """Compose the selected-zone decision workspace from focused renderers."""
    recommendation = decision.recommendation if decision is not None else None
    proposal = decision.proposal if decision is not None else None
    forecast = decision.forecast if decision is not None else None
    center, right = st.columns([2.2, 1], gap="medium")
    with center:
        render_zone_header(zone, expected_escalations)
        if show_recommendation:
            render_recommendation(recommendation, error=error)
        else:
            st.info(
                "Predictive watch · forecast and risk evidence are updating. "
                "No SafePause plan is presented before the decision tick."
            )
        render_forecast(forecast)
    with right:
        render_business_impact(proposal, constraints)
        if not show_execution:
            st.caption(
                "Use the operational clock decision controls above to activate "
                "SafePause or continue without intervention."
            )
            return None
        return render_execution(
            zone, proposal, audit_store, data_fresh=data_fresh
        )


__all__ = [
    "AuditStore",
    "render_business_impact",
    "render_decision_workspace",
    "render_execution",
    "render_forecast",
    "render_recommendation",
    "render_zone_header",
]
