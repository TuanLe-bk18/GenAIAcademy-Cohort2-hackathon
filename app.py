from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd
import pydeck as pdk
import streamlit as st

from heatsafe.ai_decision import evaluate_rule_reference, recommend_ai_intervention
from heatsafe.audit import HybridInterventionAuditStore
from heatsafe.copilot import HeatSafeCopilot
from heatsafe.repository import AIModelUnavailable, HybridRepository
from heatsafe.risk import TIER_LABELS, heat_tier, operational_priority
from heatsafe.telemetry import log_event

HANOI_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DECISION_HORIZON_MINUTES = 240
COPILOT_STATE_VERSION = 3

st.set_page_config(page_title="HeatSafe AI Ops", page_icon="☀️", layout="wide")
st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
      html, body, [class*="css"] { font-family: Inter, sans-serif; }
      .stApp { background: #0b1220; color: #e8eef8; }
      .block-container { max-width: 1500px; padding-top: 1.4rem; }
      .hero { padding: 1.4rem 1.8rem; border-radius: 18px; margin-bottom: 1rem;
        border: 1px solid rgba(255,255,255,.08); background: linear-gradient(135deg,#111d32,#102329); }
      .hero h1 { margin: 0; color: #ff8a50; }
      .hero p { color: #aebbd0; margin: .45rem 0 0; }
      .badge { display:inline-block; margin-top:.7rem; padding:.25rem .65rem; border-radius:999px;
        color:#8ee7c0; background:rgba(40,180,125,.12); border:1px solid rgba(80,220,160,.25); font-size:.78rem; }
      div[data-testid="stMetric"] { background:#111a2a; border:1px solid rgba(255,255,255,.07);
        padding:.8rem 1rem; border-radius:12px; }
      [data-testid="stHeader"] { background: transparent; }
    </style>
    """,
    unsafe_allow_html=True,
)


def format_currency_vnd(value: float) -> str:
    return f"${value / 25_000:,.2f}"


@st.cache_data(ttl=300, show_spinner=False)
def load_snapshot(scenario: str):
    repository = HybridRepository(scenario=scenario)
    return repository.load()


@st.cache_data(ttl=900, show_spinner=False)
def load_ai_context(scenario: str, zone_id: str, snapshot_id: str):
    repository = HybridRepository(scenario=scenario)
    repository.load()
    forecast = repository.forecast_demand(zone_id, DECISION_HORIZON_MINUTES)
    predictions = repository.load_driver_predictions(zone_id, snapshot_id)
    return forecast, predictions


@st.cache_data(ttl=900, show_spinner=False)
def load_zone_ai_summary(scenario: str, snapshot_id: str):
    repository = HybridRepository(scenario=scenario)
    repository.load()
    return repository.load_zone_risk_summary(snapshot_id)


scenario = st.sidebar.selectbox(
    "Operating scenario",
    ("heatwave", "live"),
    format_func=lambda value: "Heatwave replay" if value == "heatwave" else "Live weather",
)
result = load_snapshot(scenario)
zones = result.zones
audit = HybridInterventionAuditStore()
snapshot_id = zones[0].snapshot_id

try:
    zone_risk = load_zone_ai_summary(scenario, snapshot_id)
    ai_summary_ready = True
except Exception as exc:
    zone_risk = {}
    ai_summary_ready = False
    log_event("ai_zone_summary_unavailable", severity="WARNING", error_type=type(exc).__name__)

top_zone = max(
    zones,
    key=lambda zone: zone_risk.get(
        zone.zone_id, float(operational_priority(zone))
    ),
)
active_drivers = sum(zone.active_drivers for zone in zones)

st.markdown(
    f"""
    <section class="hero">
      <h1>HeatSafe AI Ops</h1>
      <p>Predict who will need heat recovery, when to intervene, and how to preserve service capacity.</p>
      <span class="badge">{'AI READY' if ai_summary_ready else 'MONITORING ONLY'} · BigQuery · {result.mode.upper()}</span>
    </section>
    """,
    unsafe_allow_html=True,
)
if any(zone.is_simulated for zone in zones):
    st.warning("Demo replay: driver operations and outcomes are simulated; BigQuery ML inference runs on labelled synthetic data.")
if result.freshness_warning:
    st.error(result.freshness_warning)

summary_cols = st.columns(4)
summary_cols[0].metric("Active drivers", f"{active_drivers:,}")
summary_cols[1].metric("AI expected escalations", f"{sum(zone_risk.values()):.1f}" if zone_risk else "Unavailable")
summary_cols[2].metric("Highest predicted risk", top_zone.name)
summary_cols[3].metric("Simulated decisions", f"{audit.protected_driver_count():,}")

map_col, zone_col = st.columns([1.7, 1], gap="large")
map_rows = []
max_risk = max(zone_risk.values(), default=1.0)
for zone in zones:
    expected = zone_risk.get(zone.zone_id)
    intensity = (expected or 0.0) / max_risk
    map_rows.append(
        {
            "name": zone.name,
            "lat": zone.latitude,
            "lon": zone.longitude,
            "expected_events": round(expected, 2) if expected is not None else None,
            "heat_index": zone.heat_index_c,
            "active": zone.active_drivers,
            "color": [round(255 * max(.35, intensity)), round(150 * (1 - intensity)), 75, 210],
        }
    )

with map_col:
    st.subheader("AI Heat-Risk Map")
    map_df = pd.DataFrame(map_rows)
    st.pydeck_chart(
        pdk.Deck(
            layers=[
                pdk.Layer(
                    "ScatterplotLayer",
                    map_df,
                    get_position=["lon", "lat"],
                    get_fill_color="color",
                    get_radius="800 + active * 2",
                    pickable=True,
                    stroked=True,
                    get_line_color=[255, 255, 255, 80],
                )
            ],
            initial_view_state=pdk.ViewState(latitude=21.025, longitude=105.81, zoom=10.1, pitch=35),
            map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
            tooltip={
                "html": "<b>{name}</b><br/>Expected escalations: {expected_events}<br/>Heat Index: {heat_index}°C<br/>Active: {active}",
                "style": {"backgroundColor": "#111827", "color": "white"},
            },
        ),
        height=455,
    )
    st.caption("Map priority comes from summed driver-level model probability, not a Heat Index threshold.")

with zone_col:
    st.subheader("Decision zone")
    ordered = sorted(
        zones,
        key=lambda item: zone_risk.get(item.zone_id, float(operational_priority(item))),
        reverse=True,
    )
    zone_name = st.selectbox("Zone", [zone.name for zone in ordered])
    selected = next(zone for zone in zones if zone.name == zone_name)
    tier = heat_tier(selected.heat_index_c)
    st.metric("Expected risk escalations (60m)", f"{zone_risk[selected.zone_id]:.2f}" if selected.zone_id in zone_risk else "Unavailable")
    detail = pd.DataFrame(
        {
            "Metric": ["Heat Index", "Active", "Exposure ≥2h", "Exposure ≥4h", "CoolStop"],
            "Value": [
                f"{selected.heat_index_c:.1f}°C · {TIER_LABELS[tier]}",
                f"{selected.active_drivers:,}",
                f"{selected.exposed_2h:,}",
                f"{selected.exposed_4h:,}",
                selected.coolstop_name,
            ],
        }
    )
    st.dataframe(detail, hide_index=True, width="stretch")

st.divider()
st.subheader("AI Counterfactual Intervention")
constraint_cols = st.columns(2)
budget_cap = constraint_cols[0].number_input("Platform cost cap ($)", min_value=0.0, value=120.0, step=10.0)
partner_per_driver = constraint_cols[1].number_input(
    "Partner cash credit / selected driver ($)", min_value=0.0, value=0.32, step=0.04
)

forecast = None
predictions = ()
ai_error = None
try:
    forecast, predictions = load_ai_context(scenario, selected.zone_id, selected.snapshot_id)
except Exception as exc:
    ai_error = exc
    log_event(
        "ai_decision_context_unavailable",
        severity="WARNING",
        zone_id=selected.zone_id,
        error_type=type(exc).__name__,
    )

if forecast and forecast.points:
    chart = pd.DataFrame(
        [
            {
                "Time": point.forecast_at.astimezone(HANOI_TZ),
                "Median demand": point.predicted_requests,
                "90% upper demand": point.upper_bound,
            }
            for point in forecast.points
        ]
    ).set_index("Time")
    st.line_chart(chart, height=260)

if ai_error:
    st.error(f"AI decision unavailable—monitoring only. {type(ai_error).__name__}: {ai_error}")
    recommendation = None
    proposal = None
    rule_reference = None
else:
    demand = tuple(point.predicted_requests for point in forecast.points)
    upper = tuple(point.upper_bound for point in forecast.points)
    recommendation = recommend_ai_intervention(
        selected,
        predictions,
        demand_by_interval=demand,
        upper_demand_by_interval=upper,
        budget_cap_vnd=int(budget_cap * 25_000),
        sponsor_per_driver_vnd=int(partner_per_driver * 25_000),
    )
    proposal = recommendation.recommended
    rule_reference = evaluate_rule_reference(
        selected,
        predictions,
        demand_by_interval=demand,
        upper_demand_by_interval=upper,
        budget_cap_vnd=int(budget_cap * 25_000),
        sponsor_per_driver_vnd=int(partner_per_driver * 25_000),
    )
    if recommendation.status == "FEASIBLE":
        st.success(recommendation.message)
    else:
        st.error(recommendation.message)

if proposal:
    metrics = st.columns(7)
    metrics[0].metric(
        "Selected by policy", f"{proposal.selected_drivers}/{proposal.eligible_drivers}"
    )
    metrics[1].metric(
        "Mandatory 4h+ covered",
        f"{proposal.mandatory_selected_drivers}/{proposal.mandatory_eligible_drivers}",
    )
    metrics[2].metric("Expected events prevented", f"{proposal.expected_risk_events_prevented:.2f}")
    metrics[3].metric("Protected rest", f"{proposal.exposure_minutes_avoided:,} min")
    metrics[4].metric(
        "Stress fulfillment",
        f"{proposal.p90_fulfillment_rate:.1%}",
        delta=f"{(proposal.p90_fulfillment_rate - proposal.baseline_stress_fulfillment_rate) * 100:+.1f} pp",
    )
    metrics[5].metric("Stress ETA", f"+{proposal.p90_eta_increase_minutes:.1f} min")
    metrics[6].metric("Net platform cost", format_currency_vnd(proposal.net_platform_cost_vnd))
    st.caption(
        f"Model {proposal.model_version} · prediction run {proposal.prediction_run_id} · "
        "counterfactual effects are model estimates, not medical diagnoses or causal proof."
    )

    compare_rows = [
        {
            "Policy": "Safety-first hybrid",
            "Selected": proposal.selected_drivers,
            "Expected prevented": proposal.expected_risk_events_prevented,
            "Rest minutes": proposal.exposure_minutes_avoided,
            "Stress fulfill": f"{proposal.p90_fulfillment_rate:.1%}",
            "ETA delta": f"+{proposal.p90_eta_increase_minutes:.1f}m",
            "Net cost": format_currency_vnd(proposal.net_platform_cost_vnd),
            "Feasible": proposal.within_guardrails,
        }
    ]
    if rule_reference:
        compare_rows.append(
            {
                "Policy": "Rule: pause everyone ≥2h",
                "Selected": rule_reference.selected_drivers,
                "Expected prevented": rule_reference.expected_risk_events_prevented,
                "Rest minutes": rule_reference.exposure_minutes_avoided,
                "Stress fulfill": f"{rule_reference.p90_fulfillment_rate:.1%}",
                "ETA delta": f"+{rule_reference.p90_eta_increase_minutes:.1f}m",
                "Net cost": format_currency_vnd(rule_reference.net_platform_cost_vnd),
                "Feasible": rule_reference.within_guardrails,
            }
        )
    st.markdown("#### Why safety-first changes the decision")
    st.dataframe(pd.DataFrame(compare_rows), hide_index=True, width="stretch")

    driver_col, wave_col = st.columns([1.5, 1], gap="large")
    with driver_col:
        st.markdown("#### Safety-first driver actions")
        driver_rows = [
            {
                "Driver": item.driver_id_hash[:10],
                "Priority": (
                    "Mandatory 4h+"
                    if item.priority_tier == "MANDATORY_4H"
                    else "Model eligible"
                ),
                "Exposure": f"{item.exposure_minutes}m",
                "Risk before": f"{item.baseline_risk:.1%}",
                "Risk after": f"{item.action_risk:.1%}",
                "Pause benefit": f"{item.risk_reduction:.1%}",
                "Wait cost": f"{item.risk_of_waiting:.1%}",
                "Start": f"+{item.pause_start_delay_minutes}m",
                "Pause": f"{item.pause_duration_minutes}m",
                "Baseline risk factors": ", ".join(item.top_factors[:3]),
                "Wave reason": item.assignment_reason,
            }
            for item in sorted(
                proposal.driver_decisions,
                key=lambda item: (
                    item.pause_start_delay_minutes,
                    item.priority_tier != "MANDATORY_4H",
                    -item.baseline_risk,
                    -item.exposure_minutes,
                    item.driver_id_hash,
                ),
            )[:20]
        ]
        st.dataframe(pd.DataFrame(driver_rows), hide_index=True, width="stretch")
        st.caption(
            "Baseline risk factors explain the no-action prediction only; they do not "
            "prove why a pause works. Wait cost is the model-estimated increase versus "
            "an immediate pause."
        )
    with wave_col:
        st.markdown("#### Safety-first waves")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Wave": wave.wave,
                        "Start": f"+{wave.start_minute}m",
                        "End": f"+{wave.end_minute}m",
                        "Drivers": wave.selected_drivers,
                        "Mandatory 4h+": wave.high_priority_drivers,
                    }
                    for wave in proposal.wave_plan
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    alt_col, cost_col = st.columns([1.4, 1], gap="large")
    with alt_col:
        st.markdown("#### Feasible AI alternatives")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Drivers": item.selected_drivers,
                        "Pause": f"{item.pause_minutes}m",
                        "Waves": item.waves,
                        "Expected prevented": item.expected_risk_events_prevented,
                        "Stress fulfill": f"{item.p90_fulfillment_rate:.1%}",
                        "ETA": f"+{item.p90_eta_increase_minutes:.1f}m",
                        "Cost": format_currency_vnd(item.net_platform_cost_vnd),
                    }
                    for item in recommendation.alternatives
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    with cost_col:
        st.markdown("#### Platform cash reconciliation")
        cash = pd.DataFrame(
            {
                "Component": ["Earnings Guard", "Lost contribution", "Partner cash credit"],
                "USD": [
                    proposal.earnings_guard_cost_vnd / 25_000,
                    proposal.lost_contribution_vnd / 25_000,
                    -proposal.partner_sponsorship_vnd / 25_000,
                ],
            }
        ).set_index("Component")
        st.bar_chart(cash, height=240)
        st.metric("Reconciled net platform cost", format_currency_vnd(proposal.net_platform_cost_vnd))
        st.caption(
            f"Partner hydration is an in-kind value of {format_currency_vnd(proposal.partner_hydration_value_vnd)} "
            "and is intentionally excluded from platform cash cost."
        )

    if proposal.guardrail_notes:
        st.info(" · ".join(proposal.guardrail_notes))
    confirm = st.checkbox("I confirm this records a simulated intervention only")
    if st.button(
        "Record AI-scored simulation",
        disabled=not (confirm and proposal.within_guardrails and result.data_fresh),
        use_container_width=True,
    ):
        event = audit.approve(proposal)
        st.success(f"Recorded {event.intervention_id[:8]} · no command sent to drivers.")
        st.cache_data.clear()
        st.rerun()
elif recommendation and recommendation.alternatives:
    st.markdown("#### Closest alternatives — not recommendations")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Drivers": item.selected_drivers,
                    "Pause": f"{item.pause_minutes}m",
                    "Waves": item.waves,
                    "Violations": " · ".join(item.guardrail_notes),
                }
                for item in recommendation.alternatives
            ]
        ),
        hide_index=True,
        width="stretch",
    )

st.divider()
copilot_col, audit_col = st.columns([1.2, 1], gap="large")
with copilot_col:
    st.subheader("HeatSafe Copilot")
    st.caption("Gemini explains verified BigQuery ML outputs; it does not create or approve decisions.")
    if st.session_state.get("copilot_state_version") != COPILOT_STATE_VERSION:
        st.session_state.copilot_state_version = COPILOT_STATE_VERSION
        st.session_state.messages = [
            {"role": "assistant", "content": "Ask why a zone or driver cohort was selected by the AI model."}
        ]
    for message in st.session_state.messages[-6:]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("tool"):
                st.caption(f"Verified tool trace: {message['tool']}")
    if question := st.chat_input("Example: Why intervene in this zone now?"):
        st.session_state.messages.append({"role": "user", "content": question})
        repository = HybridRepository(scenario=scenario)
        repository.load()
        answer, tool = HeatSafeCopilot(zones, repository).answer(question)
        st.session_state.messages.append({"role": "assistant", "content": answer, "tool": tool})
        st.rerun()

with audit_col:
    st.subheader("Decision audit")
    audit_rows = audit.list_recent()
    if audit_rows:
        st.dataframe(pd.DataFrame(audit_rows), hide_index=True, width="stretch")
    else:
        st.info("No simulated intervention recorded.")

with st.sidebar:
    st.caption(f"Snapshot: {snapshot_id}")
    st.caption("AI failure policy: fail closed; monitoring remains available.")
    if st.button("Refresh materialized data"):
        st.cache_data.clear()
        st.rerun()
