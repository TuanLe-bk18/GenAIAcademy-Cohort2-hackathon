from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
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

TIER_CSS = {
    "NORMAL": "tier-safe",
    "CAUTION": "tier-caution",
    "EXTREME_CAUTION": "tier-caution",
    "DANGER": "tier-danger",
    "EXTREME_DANGER": "tier-extreme",
}

PLOTLY_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, sans-serif"),
)

st.set_page_config(page_title="HeatSafe AI Ops", page_icon="☀️", layout="wide")

# ── Design System ──────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] {
      font-family: Inter, -apple-system, system-ui, sans-serif;
      font-variant-numeric: tabular-nums;
    }
    .stApp { background: #080e1a; color: #e8eef8; }
    .block-container { max-width: 1500px; padding-top: 1.2rem; }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stSidebar"] { background: #0a1020; border-right: 1px solid rgba(255,255,255,.06); }

    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(255,255,255,.1); border-radius: 3px; }

    /* Hero */
    .hero {
      padding: 1.5rem 2rem; border-radius: 16px; margin-bottom: 1.2rem;
      border: 1px solid rgba(255,255,255,.06);
      background: linear-gradient(135deg, #0d1a2d 0%, #0f2027 100%);
    }
    .hero h1 { margin:0; color:#ff8a50; font-size:1.65rem; font-weight:700; letter-spacing:-0.02em; }
    .hero p  { color:#7a8ba8; margin:.4rem 0 0; font-size:.88rem; }
    .status-badge {
      display:inline-flex; align-items:center; gap:6px; margin-top:.75rem;
      padding:.25rem .7rem; border-radius:999px; font-size:.73rem; font-weight:500;
      color:#34d399; background:rgba(52,211,153,.08); border:1px solid rgba(52,211,153,.2);
    }
    .status-dot {
      width:7px; height:7px; border-radius:50%; background:#34d399;
      animation: pulse 2s ease-in-out infinite;
    }
    @keyframes pulse {
      0%,100% { opacity:1; box-shadow:0 0 0 0 rgba(52,211,153,.4); }
      50%     { opacity:.6; box-shadow:0 0 0 5px rgba(52,211,153,0); }
    }

    /* Metric Cards */
    .metric-grid   { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:1rem; }
    .metric-grid-7 { display:grid; grid-template-columns:repeat(7,1fr); gap:10px; margin-bottom:1rem; }
    .metric-card {
      background:#0f1729; border:1px solid rgba(255,255,255,.06);
      border-radius:12px; padding:.9rem 1.1rem; transition:border-color .2s;
    }
    .metric-card:hover { border-color:rgba(255,138,80,.25); }
    .metric-grid-7 .metric-card { padding:.7rem .85rem; }
    .metric-label {
      display:block; color:#5a6e8a; font-size:.72rem; font-weight:500;
      text-transform:uppercase; letter-spacing:.04em; margin-bottom:.3rem;
    }
    .metric-value {
      display:block; color:#e8eef8; font-size:1.45rem; font-weight:700; line-height:1.2;
    }
    .metric-grid-7 .metric-value { font-size:1.1rem; }
    .metric-value.accent { color:#ff8a50; }
    .metric-value.danger { color:#ef4444; }
    .metric-value.safe   { color:#34d399; }
    .metric-sub { display:block; color:#4a5a75; font-size:.7rem; margin-top:.15rem; }

    /* Zone Detail Grid */
    .zone-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .zone-item {
      background:#0f1729; border:1px solid rgba(255,255,255,.06);
      border-radius:8px; padding:.6rem .85rem;
    }
    .zone-item.full { grid-column:1/-1; }
    .zone-label { display:block; color:#4a5a75; font-size:.68rem; text-transform:uppercase; letter-spacing:.04em; }
    .zone-val   { display:block; color:#e8eef8; font-size:.95rem; font-weight:600; margin-top:2px; }

    /* Tier Badges */
    .tier-badge {
      display:inline-block; padding:.12rem .45rem; border-radius:6px;
      font-size:.7rem; font-weight:600; margin-left:.35rem; vertical-align:middle;
    }
    .tier-safe    { background:rgba(52,211,153,.1);  color:#6ee7b7; border:1px solid rgba(52,211,153,.2); }
    .tier-caution { background:rgba(245,158,11,.1);  color:#fbbf24; border:1px solid rgba(245,158,11,.25); }
    .tier-danger  { background:rgba(239,68,68,.1);   color:#f87171; border:1px solid rgba(239,68,68,.25); }
    .tier-extreme { background:rgba(220,38,38,.14);  color:#fca5a5; border:1px solid rgba(220,38,38,.3); }

    /* Section Headers */
    .section-header {
      display:flex; align-items:center; gap:8px;
      margin:1.4rem 0 .75rem; padding-bottom:.55rem;
      border-bottom:1px solid rgba(255,255,255,.06);
      font-size:1.1rem; font-weight:600; color:#e8eef8;
    }

    /* Guardrail Badges */
    .guardrail-badge {
      padding:.5rem .9rem; border-radius:10px;
      font-size:.8rem; font-weight:500; margin:.5rem 0;
    }
    .guardrail-pass { background:rgba(52,211,153,.07); border:1px solid rgba(52,211,153,.18); color:#34d399; }
    .guardrail-fail { background:rgba(239,68,68,.07);  border:1px solid rgba(239,68,68,.18);  color:#ef4444; }

    /* Streamlit widget overrides */
    div[data-testid="stMetric"] {
      background:#0f1729; border:1px solid rgba(255,255,255,.06);
      padding:.8rem 1rem; border-radius:12px;
    }
    div[data-testid="stChatMessage"] {
      border:1px solid rgba(255,255,255,.06); border-radius:12px; margin-bottom:.5rem;
    }
    .stDivider { opacity:.12; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Helpers ────────────────────────────────────────────────────────────────────
def format_currency_vnd(value: float) -> str:
    return f"${value / 25_000:,.2f}"


def _style_risk(val: str) -> str:
    """Color-code risk percentage cells: red for high, green for low."""
    try:
        v = float(val.strip("%")) / 100
        r = int(200 * min(v * 2, 1))
        g = int(200 * min((1 - v) * 2, 1))
        return f"background-color: rgba({r},{g},80,.18); color: white"
    except (ValueError, AttributeError):
        return ""


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


# ── Data Load ──────────────────────────────────────────────────────────────────
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

# ── Hero ───────────────────────────────────────────────────────────────────────
ai_label = "AI READY" if ai_summary_ready else "MONITORING ONLY"
st.markdown(
    f'<section class="hero">'
    f'<h1>HeatSafe AI Ops</h1>'
    f'<p>Predict who needs heat recovery, when to intervene, and how to preserve service capacity.</p>'
    f'<span class="status-badge"><span class="status-dot"></span> {ai_label} · BigQuery · {result.mode.upper()}</span>'
    f'</section>',
    unsafe_allow_html=True,
)
if any(zone.is_simulated for zone in zones):
    st.warning("Demo replay: driver operations and outcomes are simulated; BigQuery ML inference runs on labelled synthetic data.")
if result.freshness_warning:
    st.error(result.freshness_warning)

# ── Top Metrics ────────────────────────────────────────────────────────────────
escalation_val = f"{sum(zone_risk.values()):.1f}" if zone_risk else "—"
decision_count = f"{audit.protected_driver_count():,}"
st.markdown(
    f'<div class="metric-grid">'
    f'<div class="metric-card"><span class="metric-label">Active Drivers</span><span class="metric-value">{active_drivers:,}</span></div>'
    f'<div class="metric-card"><span class="metric-label">AI Expected Escalations</span><span class="metric-value accent">{escalation_val}</span></div>'
    f'<div class="metric-card"><span class="metric-label">Highest Predicted Risk</span><span class="metric-value danger">{top_zone.name}</span></div>'
    f'<div class="metric-card"><span class="metric-label">Simulated Decisions</span><span class="metric-value">{decision_count}</span></div>'
    f'</div>',
    unsafe_allow_html=True,
)

# ── Map + Zone Detail ─────────────────────────────────────────────────────────
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
    st.markdown('<div class="section-header">🗺️ AI Heat-Risk Map</div>', unsafe_allow_html=True)
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
    st.markdown('<div class="section-header">📍 Decision Zone</div>', unsafe_allow_html=True)
    ordered = sorted(
        zones,
        key=lambda item: zone_risk.get(item.zone_id, float(operational_priority(item))),
        reverse=True,
    )
    zone_name = st.selectbox("Zone", [zone.name for zone in ordered])
    selected = next(zone for zone in zones if zone.name == zone_name)
    tier = heat_tier(selected.heat_index_c)
    tier_label = TIER_LABELS[tier]
    tier_class = TIER_CSS.get(tier, "tier-safe")
    risk_display = f"{zone_risk[selected.zone_id]:.2f}" if selected.zone_id in zone_risk else "—"
    st.markdown(
        f'<div class="zone-grid">'
        f'<div class="zone-item full"><span class="zone-label">Expected Risk Escalations (60 min)</span><span class="zone-val" style="color:#ff8a50;font-size:1.25rem">{risk_display}</span></div>'
        f'<div class="zone-item"><span class="zone-label">Heat Index</span><span class="zone-val">{selected.heat_index_c:.1f}°C <span class="tier-badge {tier_class}">{tier_label}</span></span></div>'
        f'<div class="zone-item"><span class="zone-label">Active Drivers</span><span class="zone-val">{selected.active_drivers:,}</span></div>'
        f'<div class="zone-item"><span class="zone-label">Exposure ≥ 2h</span><span class="zone-val">{selected.exposed_2h:,}</span></div>'
        f'<div class="zone-item"><span class="zone-label">Exposure ≥ 4h</span><span class="zone-val">{selected.exposed_4h:,}</span></div>'
        f'<div class="zone-item full"><span class="zone-label">CoolStop</span><span class="zone-val">{selected.coolstop_name}</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

# ── Intervention ───────────────────────────────────────────────────────────────
st.divider()
st.markdown('<div class="section-header">🧪 AI Counterfactual Intervention</div>', unsafe_allow_html=True)
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
    times = [point.forecast_at.astimezone(HANOI_TZ) for point in forecast.points]
    median_vals = [point.predicted_requests for point in forecast.points]
    upper_vals = [point.upper_bound for point in forecast.points]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=times, y=median_vals, name="Median demand",
        line=dict(color="#60a5fa", width=2),
        fill="tozeroy", fillcolor="rgba(96,165,250,.06)",
    ))
    fig.add_trace(go.Scatter(
        x=times, y=upper_vals, name="90% upper bound",
        line=dict(color="#f59e0b", width=1.5, dash="dot"),
    ))
    fig.update_layout(
        **PLOTLY_LAYOUT,
        margin=dict(l=0, r=0, t=30, b=0), height=260,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=11)),
        xaxis=dict(gridcolor="rgba(255,255,255,.04)", showline=False),
        yaxis=dict(gridcolor="rgba(255,255,255,.04)", showline=False, title="Requests"),
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")

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

# ── Proposal Detail ────────────────────────────────────────────────────────────
if proposal:
    fulfill_delta = (proposal.p90_fulfillment_rate - proposal.baseline_stress_fulfillment_rate) * 100
    st.markdown(
        f'<div class="metric-grid-7">'
        f'<div class="metric-card"><span class="metric-label">Selected</span><span class="metric-value">{proposal.selected_drivers}/{proposal.eligible_drivers}</span></div>'
        f'<div class="metric-card"><span class="metric-label">Mandatory 4h+</span><span class="metric-value">{proposal.mandatory_selected_drivers}/{proposal.mandatory_eligible_drivers}</span></div>'
        f'<div class="metric-card"><span class="metric-label">Events Prevented</span><span class="metric-value safe">{proposal.expected_risk_events_prevented:.2f}</span></div>'
        f'<div class="metric-card"><span class="metric-label">Protected Rest</span><span class="metric-value">{proposal.exposure_minutes_avoided:,} min</span></div>'
        f'<div class="metric-card"><span class="metric-label">Stress Fulfillment</span><span class="metric-value">{proposal.p90_fulfillment_rate:.1%}</span><span class="metric-sub">{fulfill_delta:+.1f} pp</span></div>'
        f'<div class="metric-card"><span class="metric-label">Stress ETA</span><span class="metric-value">+{proposal.p90_eta_increase_minutes:.1f} min</span></div>'
        f'<div class="metric-card"><span class="metric-label">Net Cost</span><span class="metric-value accent">{format_currency_vnd(proposal.net_platform_cost_vnd)}</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"Model {proposal.model_version} · prediction run {proposal.prediction_run_id} · "
        "counterfactual effects are model estimates, not medical diagnoses or causal proof."
    )

    # ── Comparison ─────────────────────────────────────────────────────────────
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

    # ── Driver Table + Wave Plan ───────────────────────────────────────────────
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
        driver_df = pd.DataFrame(driver_rows)
        try:
            styled = driver_df.style.map(
                _style_risk, subset=["Risk before", "Risk after"]
            )
            st.dataframe(styled, hide_index=True, width="stretch")
        except Exception:
            st.dataframe(driver_df, hide_index=True, width="stretch")
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

    # ── Alternatives + Cost ────────────────────────────────────────────────────
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
        components = ["Earnings Guard", "Lost contribution", "Partner credit"]
        values = [
            proposal.earnings_guard_cost_vnd / 25_000,
            proposal.lost_contribution_vnd / 25_000,
            -proposal.partner_sponsorship_vnd / 25_000,
        ]
        colors = ["#f59e0b", "#ef4444", "#34d399"]
        fig = go.Figure(go.Bar(
            y=components, x=values, orientation="h",
            marker_color=colors,
            text=[f"${abs(v):,.2f}" for v in values],
            textposition="auto",
            textfont=dict(color="white", size=11),
        ))
        fig.update_layout(
            **PLOTLY_LAYOUT,
            margin=dict(l=0, r=10, t=10, b=0), height=220,
            xaxis=dict(title="USD", gridcolor="rgba(255,255,255,.04)", showline=False,
                       zeroline=True, zerolinecolor="rgba(255,255,255,.1)"),
            yaxis=dict(gridcolor="rgba(255,255,255,.04)", showline=False, autorange="reversed"),
            showlegend=False,
        )
        st.plotly_chart(fig, width="stretch")
        st.markdown(
            f'<div class="metric-card" style="text-align:center;margin-top:.4rem">'
            f'<span class="metric-label">Net Platform Cost</span>'
            f'<span class="metric-value accent">{format_currency_vnd(proposal.net_platform_cost_vnd)}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"Partner hydration is an in-kind value of {format_currency_vnd(proposal.partner_hydration_value_vnd)} "
            "and is intentionally excluded from platform cash cost."
        )

    # ── Guardrails + Record ────────────────────────────────────────────────────
    if proposal.guardrail_notes:
        notes_text = " · ".join(proposal.guardrail_notes)
        if proposal.within_guardrails:
            st.markdown(f'<div class="guardrail-badge guardrail-pass">✅ {notes_text}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="guardrail-badge guardrail-fail">❌ {notes_text}</div>', unsafe_allow_html=True)
    confirm = st.checkbox("I confirm this records a simulated intervention only")
    if st.button(
        "Record AI-scored simulation",
        disabled=not (confirm and proposal.within_guardrails and result.data_fresh),
        width="stretch",
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

# ── Copilot + Audit ────────────────────────────────────────────────────────────
st.divider()
copilot_col, audit_col = st.columns([1.2, 1], gap="large")
with copilot_col:
    st.markdown('<div class="section-header">🤖 HeatSafe Copilot</div>', unsafe_allow_html=True)
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
    st.markdown('<div class="section-header">📋 Decision Audit</div>', unsafe_allow_html=True)
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
