from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
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

# Lovable reference: compact, warm operations-console treatment.
st.markdown(
    """
    <style>
    :root {
      --ops-bg:#181613; --ops-surface:#211f1c; --ops-surface-2:#292622;
      --ops-border:#3a3630; --ops-text:#f5f1e8; --ops-muted:#aaa297;
      --ops-heat:#e9863a; --ops-cool:#72cbd0; --ops-ok:#67cf9b;
      --ops-warn:#e5b158; --ops-crit:#ec6b61;
    }
    .stApp { background:var(--ops-bg); color:var(--ops-text); }
    .block-container { max-width:1600px; padding:1rem 1.5rem 3rem; }
    [data-testid="stSidebar"] { display:none; }
    [data-testid="stHeader"] { background:transparent; }
    h1,h2,h3,h4,p { letter-spacing:-.01em; }
    .ops-brand { display:flex; align-items:center; gap:.7rem; min-height:42px; }
    .ops-mark {
      width:30px; height:30px; display:grid; place-items:center; border-radius:7px;
      color:#1b1712; font-weight:800; background:linear-gradient(145deg,#f0a34c,#dc6338);
      box-shadow:inset 0 0 0 1px rgba(255,255,255,.18);
    }
    .ops-title { color:var(--ops-text); font-size:.93rem; font-weight:700; }
    .ops-subtitle { color:var(--ops-muted); font-size:.7rem; margin-top:2px; }
    .ops-status-row { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin:.2rem 0 .65rem; }
    .ops-pill {
      display:inline-flex; align-items:center; gap:6px; padding:.25rem .55rem;
      border:1px solid var(--ops-border); border-radius:999px; color:var(--ops-muted);
      background:var(--ops-surface); font-size:.68rem;
    }
    .ops-pill.ok { color:var(--ops-ok); border-color:rgba(103,207,155,.3); background:rgba(103,207,155,.08); }
    .ops-pill.warn { color:var(--ops-warn); border-color:rgba(229,177,88,.3); background:rgba(229,177,88,.08); }
    .ops-sim-banner {
      border:1px solid var(--ops-border); border-left:3px solid var(--ops-heat);
      background:rgba(233,134,58,.045); color:var(--ops-muted); border-radius:7px;
      padding:.5rem .75rem; font-size:.72rem; margin-bottom:1rem;
    }
    .ops-panel { border:1px solid var(--ops-border); border-radius:9px; background:var(--ops-surface); overflow:hidden; }
    .ops-panel-head {
      color:var(--ops-muted); font-size:.67rem; font-weight:650; text-transform:uppercase;
      letter-spacing:.08em; padding:.65rem .8rem; border-bottom:1px solid var(--ops-border);
    }
    .ops-kpis { display:grid; grid-template-columns:repeat(2,1fr); gap:1px; background:var(--ops-border); }
    .ops-kpi { background:var(--ops-surface); padding:.75rem .8rem; }
    .ops-kpi-label { color:var(--ops-muted); font-size:.64rem; text-transform:uppercase; letter-spacing:.05em; }
    .ops-kpi-value { color:var(--ops-text); font-size:1.22rem; font-weight:700; margin-top:.22rem; }
    .ops-kpi-value.heat { color:var(--ops-heat); }
    .ops-kpi-value.cool { color:var(--ops-cool); }
    .ops-zone-head { padding:1rem 1.1rem; }
    .ops-eyebrow { color:var(--ops-muted); font-size:.65rem; text-transform:uppercase; letter-spacing:.09em; }
    .ops-zone-name { color:var(--ops-text); font-size:1.55rem; font-weight:700; margin:.2rem 0 .7rem; }
    .ops-zone-stats { display:grid; grid-template-columns:repeat(5,1fr); gap:1rem; }
    .ops-stat-label { color:var(--ops-muted); font-size:.62rem; text-transform:uppercase; letter-spacing:.05em; }
    .ops-stat-value { color:var(--ops-text); font-size:.88rem; font-weight:650; margin-top:.15rem; }
    .ops-rec { border:1px solid rgba(233,134,58,.38); border-radius:9px; background:linear-gradient(145deg,rgba(233,134,58,.075),rgba(33,31,28,.95)); padding:1rem; }
    .ops-rec-title { color:var(--ops-heat); font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; }
    .ops-rec-name { color:var(--ops-text); font-size:1.1rem; font-weight:700; margin:.2rem 0; }
    .ops-copy { color:var(--ops-muted); font-size:.75rem; line-height:1.5; }
    .ops-metric-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:7px; margin:.8rem 0; }
    .ops-metric { border:1px solid var(--ops-border); border-radius:7px; background:rgba(24,22,19,.42); padding:.62rem .7rem; }
    .ops-metric-label { color:var(--ops-muted); font-size:.6rem; text-transform:uppercase; letter-spacing:.05em; }
    .ops-metric-value { color:var(--ops-text); font-size:1rem; font-weight:700; margin-top:.18rem; }
    .ops-metric-value.cool { color:var(--ops-cool); } .ops-metric-value.ok { color:var(--ops-ok); }
    .ops-wave { display:flex; gap:6px; margin-top:.5rem; }
    .ops-wave-item { flex:1; border:1px solid var(--ops-border); border-top:3px solid var(--ops-heat); border-radius:6px; padding:.55rem; background:var(--ops-surface-2); }
    .ops-wave-title { color:var(--ops-text); font-size:.72rem; font-weight:650; }
    .ops-wave-meta { color:var(--ops-muted); font-size:.62rem; margin-top:.15rem; }
    .ops-guard { display:flex; align-items:center; gap:6px; color:var(--ops-ok); font-size:.7rem; margin-top:.65rem; }
    .ops-impact-row { padding:.65rem .8rem; border-bottom:1px solid rgba(58,54,48,.65); }
    .ops-impact-top { display:flex; justify-content:space-between; gap:1rem; color:var(--ops-muted); font-size:.7rem; }
    .ops-impact-top b { color:var(--ops-text); }
    .ops-track { height:5px; border-radius:999px; background:var(--ops-surface-2); margin:.4rem 0 .2rem; overflow:hidden; }
    .ops-fill { height:100%; border-radius:999px; background:var(--ops-ok); }
    .ops-caption { color:var(--ops-muted); font-size:.62rem; }
    .ops-provenance { color:var(--ops-muted); font-size:.65rem; line-height:1.55; }
    div[data-testid="stButton"] button {
      border-color:var(--ops-border); background:var(--ops-surface); color:var(--ops-text);
      border-radius:7px; min-height:2.65rem; font-size:.72rem; text-align:left;
    }
    div[data-testid="stButton"] button:hover { border-color:rgba(233,134,58,.65); color:var(--ops-text); }
    div[data-testid="stCheckbox"] label p, div[data-testid="stCaptionContainer"] { color:var(--ops-muted); }
    div[data-testid="stDataFrame"], div[data-testid="stChatMessage"] { border:1px solid var(--ops-border); border-radius:8px; overflow:hidden; }
    [data-testid="stExpander"] { border-color:var(--ops-border); background:var(--ops-surface); }
    hr { border-color:var(--ops-border)!important; }
    @media (max-width:900px) {
      .ops-zone-stats { grid-template-columns:repeat(2,1fr); }
      .ops-metric-grid { grid-template-columns:repeat(2,1fr); }
      .ops-wave { flex-direction:column; }
    }
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


@st.cache_data(ttl=900, show_spinner="Analyzing city-wide AI interventions...")
def load_city_wide_ai_plan(scenario: str, snapshot_id: str):
    repository = HybridRepository(scenario=scenario)
    result = repository.load()
    rows = []
    for zone in sorted(result.zones, key=lambda z: z.heat_index_c, reverse=True):
        try:
            forecast = repository.forecast_demand(zone.zone_id, DECISION_HORIZON_MINUTES)
            predictions = repository.load_driver_predictions(zone.zone_id, snapshot_id)
            if not predictions:
                continue
            from heatsafe.ai_decision import recommend_ai_intervention
            demand = tuple(p.predicted_requests for p in forecast.points)
            upper = tuple(p.upper_bound for p in forecast.points)
            rec = recommend_ai_intervention(zone, predictions, demand_by_interval=demand, upper_demand_by_interval=upper)
            if rec.recommended:
                prop = rec.recommended
                rows.append({
                    "Zone": zone.name,
                    "Heat Index": zone.heat_index_c,
                    "Eligible Drivers": prop.eligible_drivers,
                    "Selected Drivers": prop.selected_drivers,
                    "Mandatory Drivers": prop.high_priority_drivers,
                    "Prevented Risks": prop.expected_risk_events_prevented,
                    "Platform Cost": prop.net_platform_cost_vnd / 25000,
                    "Fulfillment Drop": (prop.baseline_stress_fulfillment_rate - prop.p90_fulfillment_rate) * 100,
                    "ETA Impact": prop.p90_eta_increase_minutes
                })
        except Exception:
            pass
    return rows


# ── Console Header + Data Load ─────────────────────────────────────────────────
brand_col, scenario_col = st.columns([5, 1], vertical_alignment="center")
with brand_col:
    st.markdown(
        '<div class="ops-brand"><div class="ops-mark">H</div><div>'
        '<div class="ops-title">HeatSafe <span style="color:var(--ops-muted);font-weight:500">AI Ops</span></div>'
        '<div class="ops-subtitle">Hanoi fleet operations · extreme heat decision support</div>'
        '</div></div>',
        unsafe_allow_html=True,
    )
with scenario_col:
    scenario = st.selectbox(
        "Operating scenario",
        ("heatwave", "live"),
        format_func=lambda value: "Heatwave replay" if value == "heatwave" else "Live weather",
        label_visibility="collapsed",
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

ordered = sorted(
    zones,
    key=lambda item: zone_risk.get(item.zone_id, float(operational_priority(item))),
    reverse=True,
)
valid_zone_ids = {zone.zone_id for zone in zones}
if st.session_state.get("selected_zone_id") not in valid_zone_ids:
    st.session_state.selected_zone_id = ordered[0].zone_id
selected = next(zone for zone in zones if zone.zone_id == st.session_state.selected_zone_id)
top_zone = ordered[0]
active_drivers = sum(zone.active_drivers for zone in zones)
exposed_4h = sum(zone.exposed_4h for zone in zones)
escalation_val = f"{sum(zone_risk.values()):.1f}" if zone_risk else "—"
action_zones = sum(
    heat_tier(zone.heat_index_c) in {"DANGER", "EXTREME_DANGER"}
    for zone in zones
)

status_tone = "ok" if ai_summary_ready and result.data_fresh else "warn"
status_label = "AI ready" if ai_summary_ready else "AI unavailable · monitoring only"
st.markdown(
    '<div class="ops-status-row">'
    f'<span class="ops-pill {status_tone}">● {status_label}</span>'
    f'<span class="ops-pill">BigQuery · BQML · Gemini</span>'
    f'<span class="ops-pill">Snapshot {snapshot_id[:12]}</span>'
    f'<span class="ops-pill">{result.mode.upper()}</span>'
    '</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="ops-sim-banner"><b style="color:var(--ops-heat)">SIMULATION ENVIRONMENT</b>'
    ' · Confirming a plan records a decision only. No commands are dispatched to drivers.</div>',
    unsafe_allow_html=True,
)
if any(zone.is_simulated for zone in zones):
    st.caption("Heatwave replay uses simulated driver operations and labelled synthetic outcomes.")
if result.freshness_warning:
    st.error(result.freshness_warning)

with st.popover("Decision constraints", use_container_width=False):
    budget_cap = st.number_input(
        "Platform cost cap ($)", min_value=0.0, value=120.0, step=10.0
    )
    partner_per_driver = st.number_input(
        "Partner cash credit / selected driver ($)",
        min_value=0.0,
        value=0.32,
        step=0.04,
    )
    st.caption("Constraints are applied to the selected zone recommendation.")

# ── Selected-Zone AI Decision ──────────────────────────────────────────────────
forecast = None
predictions = ()
ai_error = None
recommendation = None
proposal = None
rule_reference = None
try:
    forecast, predictions = load_ai_context(
        scenario, selected.zone_id, selected.snapshot_id
    )
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
except Exception as exc:
    ai_error = exc
    log_event(
        "ai_decision_context_unavailable",
        severity="WARNING",
        zone_id=selected.zone_id,
        error_type=type(exc).__name__,
    )

# ── Lovable-Inspired Three-Column Console ──────────────────────────────────────
left_col, center_col, right_col = st.columns([1.05, 2.15, 1.05], gap="medium")

with left_col:
    st.markdown(
        '<section class="ops-panel"><div class="ops-panel-head">Hanoi · current snapshot</div>'
        '<div class="ops-kpis">'
        f'<div class="ops-kpi"><div class="ops-kpi-label">Active drivers</div><div class="ops-kpi-value">{active_drivers:,}</div></div>'
        f'<div class="ops-kpi"><div class="ops-kpi-label">Escalations · 60m</div><div class="ops-kpi-value heat">{escalation_val}</div></div>'
        f'<div class="ops-kpi"><div class="ops-kpi-label">Exposed 4h+</div><div class="ops-kpi-value heat">{exposed_4h:,}</div></div>'
        f'<div class="ops-kpi"><div class="ops-kpi-label">Zones needing action</div><div class="ops-kpi-value">{action_zones}/{len(zones)}</div></div>'
        '</div></section>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="ops-panel-head" style="margin-top:.8rem">Zones · sorted by urgency</div>', unsafe_allow_html=True)
    for zone in ordered:
        marker = "●" if zone.zone_id == selected.zone_id else "○"
        expected = zone_risk.get(zone.zone_id)
        risk_text = f" · risk {expected:.2f}" if expected is not None else ""
        if st.button(
            f"{marker} {zone.name} · {zone.heat_index_c:.1f}°C\n{zone.active_drivers} active · {zone.exposed_4h} at 4h+{risk_text}",
            key=f"zone-{zone.zone_id}",
            width="stretch",
        ):
            st.session_state.selected_zone_id = zone.zone_id
            st.rerun()

with center_col:
    tier = heat_tier(selected.heat_index_c)
    tier_label = TIER_LABELS[tier]
    risk_display = (
        f"{zone_risk[selected.zone_id]:.2f}"
        if selected.zone_id in zone_risk
        else "—"
    )
    st.markdown(
        '<section class="ops-panel ops-zone-head">'
        '<div class="ops-eyebrow">Selected decision zone</div>'
        f'<div class="ops-zone-name">{selected.name} '
        f'<span class="tier-badge {TIER_CSS.get(tier, "tier-safe")}">{tier_label}</span></div>'
        '<div class="ops-zone-stats">'
        f'<div><div class="ops-stat-label">Heat Index</div><div class="ops-stat-value">{selected.heat_index_c:.1f}°C</div></div>'
        f'<div><div class="ops-stat-label">Expected escalations</div><div class="ops-stat-value" style="color:var(--ops-heat)">{risk_display}</div></div>'
        f'<div><div class="ops-stat-label">Active drivers</div><div class="ops-stat-value">{selected.active_drivers:,}</div></div>'
        f'<div><div class="ops-stat-label">Exposed 4h+</div><div class="ops-stat-value">{selected.exposed_4h:,}</div></div>'
        f'<div><div class="ops-stat-label">CoolStop</div><div class="ops-stat-value">{selected.coolstop_name}</div></div>'
        '</div></section>',
        unsafe_allow_html=True,
    )

    if ai_error:
        st.warning(
            "Model evidence is unavailable. HeatSafe will not invent a plan; "
            "city monitoring remains available."
        )
    elif proposal:
        waves_html = "".join(
            '<div class="ops-wave-item">'
            f'<div class="ops-wave-title">Wave {wave.wave} · {wave.selected_drivers} drivers</div>'
            f'<div class="ops-wave-meta">+{wave.start_minute}–{wave.end_minute} min · {wave.high_priority_drivers} mandatory</div>'
            '</div>'
            for wave in proposal.wave_plan
        )
        st.markdown(
            '<section class="ops-rec" style="margin-top:.8rem">'
            '<div class="ops-rec-title">HeatSafe recommends</div>'
            f'<div class="ops-rec-name">SafePause · {proposal.waves} staggered waves</div>'
            f'<div class="ops-copy">{proposal.decision_reason}</div>'
            '<div class="ops-metric-grid">'
            f'<div class="ops-metric"><div class="ops-metric-label">Drivers selected</div><div class="ops-metric-value">{proposal.selected_drivers}/{proposal.eligible_drivers}</div></div>'
            f'<div class="ops-metric"><div class="ops-metric-label">Mandatory covered</div><div class="ops-metric-value ok">{proposal.mandatory_selected_drivers}/{proposal.mandatory_eligible_drivers}</div></div>'
            f'<div class="ops-metric"><div class="ops-metric-label">Expected prevented</div><div class="ops-metric-value cool">{proposal.expected_risk_events_prevented:.2f}</div></div>'
            f'<div class="ops-metric"><div class="ops-metric-label">Earnings Guard</div><div class="ops-metric-value">{format_currency_vnd(proposal.earnings_guard_cost_vnd)}</div></div>'
            '</div>'
            '<div class="ops-eyebrow">Staggered waves · preserves supply</div>'
            f'<div class="ops-wave">{waves_html}</div>'
            f'<div class="ops-guard">● {" · ".join(proposal.guardrail_notes)}</div>'
            '</section>',
            unsafe_allow_html=True,
        )
        if forecast and forecast.points:
            with st.expander("Demand forecast and recommendation evidence"):
                times = [
                    point.forecast_at.astimezone(HANOI_TZ)
                    for point in forecast.points
                ]
                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(
                        x=times,
                        y=[point.predicted_requests for point in forecast.points],
                        name="Median demand",
                        line=dict(color="#72cbd0", width=2),
                        fill="tozeroy",
                        fillcolor="rgba(114,203,208,.06)",
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        x=times,
                        y=[point.upper_bound for point in forecast.points],
                        name="Stress upper bound",
                        line=dict(color="#e5b158", width=1.5, dash="dot"),
                    )
                )
                fig.update_layout(
                    **PLOTLY_LAYOUT,
                    margin=dict(l=0, r=0, t=25, b=0),
                    height=230,
                    hovermode="x unified",
                    xaxis=dict(gridcolor="rgba(255,255,255,.04)"),
                    yaxis=dict(gridcolor="rgba(255,255,255,.04)", title="Requests"),
                    legend=dict(orientation="h", y=1.12),
                )
                st.plotly_chart(fig, width="stretch")

        alternatives = [
            {
                "Plan": "Recommended" if item.proposal_id == proposal.proposal_id else "Alternative",
                "Drivers": item.selected_drivers,
                "Pause": f"{item.pause_minutes}m",
                "Waves": item.waves,
                "Risk prevented": round(item.expected_risk_events_prevented, 2),
                "Fulfillment": f"{item.p90_fulfillment_rate:.1%}",
                "ETA": f"+{item.p90_eta_increase_minutes:.1f}m",
                "Net cost": format_currency_vnd(item.net_platform_cost_vnd),
                "Guardrails": "Within limits" if item.within_guardrails else "Conflict",
            }
            for item in (proposal,) + tuple(recommendation.alternatives[:4])
        ]
        st.markdown('<div class="ops-panel-head" style="margin-top:.8rem">Compare plans · driver benefit vs business impact</div>', unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(alternatives), hide_index=True, width="stretch")
    elif recommendation:
        st.error(recommendation.message)
        if recommendation.alternatives:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Drivers": item.selected_drivers,
                            "Pause": f"{item.pause_minutes}m",
                            "Waves": item.waves,
                            "Guardrail conflict": " · ".join(item.guardrail_notes),
                        }
                        for item in recommendation.alternatives[:5]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

with right_col:
    st.markdown('<section class="ops-panel"><div class="ops-panel-head">Business impact · stress case</div>', unsafe_allow_html=True)
    if proposal:
        fulfillment_drop = max(
            0.0,
            (proposal.baseline_stress_fulfillment_rate - proposal.p90_fulfillment_rate)
            * 100,
        )
        cost_pct = min(100.0, proposal.net_platform_cost_vnd / max(budget_cap * 25_000, 1) * 100)
        impact_rows = (
            ("Fulfillment", f"{proposal.p90_fulfillment_rate:.1%}", min(100, fulfillment_drop * 12), "minimum 95%"),
            ("ETA impact", f"+{proposal.p90_eta_increase_minutes:.1f}m", min(100, proposal.p90_eta_increase_minutes / 2 * 100), "maximum +2.0m"),
            ("Net platform cost", format_currency_vnd(proposal.net_platform_cost_vnd), cost_pct, f"limit ${budget_cap:,.0f}"),
        )
        for label, value, width, caption in impact_rows:
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
            f'<div class="ops-stat-value" style="font-size:1rem;color:var(--ops-cool)">{proposal.selected_drivers} drivers protected</div>'
            f'<div class="ops-caption">{proposal.exposure_minutes_avoided:,} recovery minutes · all mandatory 4h+ covered</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown('<div class="ops-impact-row ops-copy">Impact projections require valid model evidence.</div>', unsafe_allow_html=True)
    st.markdown('</section>', unsafe_allow_html=True)

    st.markdown('<div class="ops-panel-head" style="margin-top:.8rem">Confirm decision</div>', unsafe_allow_html=True)
    if proposal:
        st.markdown(
            f'<div class="ops-copy" style="padding:.65rem 0">{selected.name} · {proposal.selected_drivers} drivers · '
            f'{proposal.waves} waves · {proposal.pause_minutes}m recovery</div>',
            unsafe_allow_html=True,
        )
        confirm = st.checkbox(
            "I understand this records a simulation only",
            key=f"confirm-{proposal.proposal_id}",
        )
        if st.button(
            "Record SafePause simulation",
            disabled=not (
                confirm and proposal.within_guardrails and result.data_fresh
            ),
            width="stretch",
            type="primary",
        ):
            event = audit.approve(proposal)
            st.success(
                f"Simulation {event.intervention_id[:8]} recorded. No command sent to drivers."
            )
            st.cache_data.clear()
    else:
        st.button("Record SafePause simulation", disabled=True, width="stretch")
    st.caption("Human confirmation is required. This public demo never dispatches operational commands.")

    st.markdown('<div class="ops-panel-head" style="margin-top:.8rem">Model provenance</div>', unsafe_allow_html=True)
    if proposal:
        st.markdown(
            '<div class="ops-provenance">'
            f'Risk model <b>{proposal.model_version}</b><br>'
            f'Prediction run {proposal.prediction_run_id}<br>'
            'Demand forecast · BigQuery ML TimesFM<br>'
            'Gemini explains allowlisted evidence only.<br><br>'
            'Heat Index is a screening indicator. Counterfactual effects are model estimates, not medical diagnoses or causal proof.'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        st.caption("Model provenance appears when a valid decision is available.")

    recent = audit.list_recent()
    st.markdown('<div class="ops-panel-head" style="margin-top:.8rem">Recent simulations</div>', unsafe_allow_html=True)
    if recent:
        st.dataframe(pd.DataFrame(recent[:3]), hide_index=True, width="stretch")
    else:
        st.caption("No simulated intervention recorded.")

# ── Progressive-Disclosure Evidence ────────────────────────────────────────────
st.divider()
city_tab, drivers_tab, copilot_tab = st.tabs(
    ["City intelligence", "Driver evidence", "Copilot & audit"]
)

with city_tab:
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
                "color": [
                    round(255 * max(.35, intensity)),
                    round(150 * (1 - intensity)),
                    75,
                    210,
                ],
            }
        )
    map_col, plan_col = st.columns([1.2, 1], gap="large")
    with map_col:
        st.pydeck_chart(
            pdk.Deck(
                layers=[
                    pdk.Layer(
                        "ScatterplotLayer",
                        pd.DataFrame(map_rows),
                        get_position=["lon", "lat"],
                        get_fill_color="color",
                        get_radius="800 + active * 2",
                        pickable=True,
                        stroked=True,
                        get_line_color=[255, 255, 255, 80],
                    )
                ],
                initial_view_state=pdk.ViewState(
                    latitude=21.025, longitude=105.81, zoom=10.1, pitch=35
                ),
                map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
                tooltip={
                    "html": "<b>{name}</b><br/>Expected escalations: {expected_events}<br/>Heat Index: {heat_index}°C<br/>Active: {active}",
                    "style": {"backgroundColor": "#211f1c", "color": "white"},
                },
            ),
            height=420,
        )
        st.caption("Priority is based on summed driver-level model probability, not temperature alone.")
    with plan_col:
        city_plan = load_city_wide_ai_plan(scenario, snapshot_id)
        if city_plan:
            df_city = pd.DataFrame(city_plan)
            fig = px.scatter(
                df_city,
                x="Fulfillment Drop",
                y="ETA Impact",
                size="Prevented Risks",
                color="Heat Index",
                hover_name="Zone",
                color_continuous_scale=px.colors.sequential.YlOrRd,
                title="City-wide intervention tradeoffs",
            )
            fig.update_layout(
                **PLOTLY_LAYOUT,
                margin=dict(l=0, r=0, t=45, b=0),
                height=360,
                xaxis=dict(gridcolor="rgba(255,255,255,.06)"),
                yaxis=dict(gridcolor="rgba(255,255,255,.06)"),
            )
            st.plotly_chart(fig, width="stretch")
            with st.expander("City plan data"):
                st.dataframe(df_city, hide_index=True, width="stretch")
        else:
            st.info("City-wide AI predictions are currently unavailable.")

with drivers_tab:
    if proposal:
        compare_rows = [
            {
                "Policy": "Safety-first hybrid",
                "Selected": proposal.selected_drivers,
                "Expected prevented": proposal.expected_risk_events_prevented,
                "Recovery minutes": proposal.exposure_minutes_avoided,
                "Stress fulfillment": f"{proposal.p90_fulfillment_rate:.1%}",
                "ETA": f"+{proposal.p90_eta_increase_minutes:.1f}m",
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
                    "Recovery minutes": rule_reference.exposure_minutes_avoided,
                    "Stress fulfillment": f"{rule_reference.p90_fulfillment_rate:.1%}",
                    "ETA": f"+{rule_reference.p90_eta_increase_minutes:.1f}m",
                    "Net cost": format_currency_vnd(rule_reference.net_platform_cost_vnd),
                    "Feasible": rule_reference.within_guardrails,
                }
            )
        st.markdown("#### Why safety-first changes the decision")
        st.dataframe(pd.DataFrame(compare_rows), hide_index=True, width="stretch")
        driver_rows = [
            {
                "Driver": item.driver_id_hash[:10],
                "Priority": "Mandatory 4h+" if item.priority_tier == "MANDATORY_4H" else "Model eligible",
                "Exposure": f"{item.exposure_minutes}m",
                "Risk before": f"{item.baseline_risk:.1%}",
                "Risk after": f"{item.action_risk:.1%}",
                "Wait cost": f"{item.risk_of_waiting:.1%}",
                "Start": f"+{item.pause_start_delay_minutes}m",
                "Pause": f"{item.pause_duration_minutes}m",
                "Evidence": ", ".join(item.top_factors[:3]),
            }
            for item in sorted(
                proposal.driver_decisions,
                key=lambda item: (
                    item.pause_start_delay_minutes,
                    item.priority_tier != "MANDATORY_4H",
                    -item.baseline_risk,
                ),
            )[:20]
        ]
        st.dataframe(pd.DataFrame(driver_rows), hide_index=True, width="stretch")
        st.caption("Factors explain the no-action prediction; they do not prove why a pause works.")
    else:
        st.info("Driver-level evidence requires a valid recommendation.")

with copilot_tab:
    copilot_col, audit_col = st.columns([1.2, 1], gap="large")
    with copilot_col:
        st.markdown("#### HeatSafe Copilot")
        st.caption("Gemini explains verified BigQuery ML outputs; it cannot approve decisions.")
        if st.session_state.get("copilot_state_version") != COPILOT_STATE_VERSION:
            st.session_state.copilot_state_version = COPILOT_STATE_VERSION
            st.session_state.messages = [
                {"role": "assistant", "content": "Ask why a zone or driver cohort was selected."}
            ]
        for message in st.session_state.messages[-6:]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                if message.get("tool"):
                    st.caption(f"Verified tool trace: {message['tool']}")
        if question := st.chat_input("Why intervene in this zone now?"):
            st.session_state.messages.append({"role": "user", "content": question})
            repository = HybridRepository(scenario=scenario)
            repository.load()
            answer, tool = HeatSafeCopilot(zones, repository).answer(question)
            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "tool": tool}
            )
            st.rerun()
    with audit_col:
        st.markdown("#### Simulation audit log")
        audit_rows = audit.list_recent()
        if audit_rows:
            st.dataframe(pd.DataFrame(audit_rows), hide_index=True, width="stretch")
        else:
            st.info("No simulated intervention recorded.")

refresh_col, policy_col = st.columns([1, 5], vertical_alignment="center")
with refresh_col:
    if st.button("Refresh data", width="stretch"):
        st.cache_data.clear()
        st.rerun()
with policy_col:
    st.caption("AI failure policy: fail closed; monitoring remains available. Source UI direction: Lovable reference console.")
