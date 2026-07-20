from __future__ import annotations

from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

from heatsafe.ai_decision import evaluate_rule_reference, recommend_ai_intervention
from heatsafe.audit import HybridInterventionAuditStore
from heatsafe.copilot import HeatSafeCopilot
from heatsafe.repository import HybridRepository
from heatsafe.risk import TIER_LABELS, heat_tier, operational_priority
from heatsafe.telemetry import log_event

HANOI_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DECISION_HORIZON_MINUTES = 240
COPILOT_STATE_VERSION = 5

TIER_CSS = {
    "NORMAL": "tier-safe",
    "CAUTION": "tier-caution",
    "EXTREME_CAUTION": "tier-caution",
    "DANGER": "tier-danger",
    "EXTREME_DANGER": "tier-extreme",
}

PLOTLY_LAYOUT: dict[str, Any] = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, sans-serif", size=13, color="#c9c2b8"),
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
      --ops-border:#48423a; --ops-border-soft:#3b3731;
      --ops-text:#fffaf0; --ops-muted:#c3bbb0;
      --ops-heat:#e9863a; --ops-cool:#72cbd0; --ops-ok:#67cf9b;
      --ops-warn:#e5b158; --ops-crit:#ec6b61;
      --ops-radius-panel:12px; --ops-radius-card:9px;
    }
    .stApp { background:var(--ops-bg); color:var(--ops-text); }
    .block-container { max-width:1600px; padding:1rem 1.5rem 3rem; }
    [data-testid="stSidebar"] { display:none; }
    [data-testid="stHeader"] { background:transparent; }
    html, body, [class*="css"] { font-size:16px; }
    h1,h2,h3,h4,p { letter-spacing:-.01em; }
    h4 { color:var(--ops-text); font-size:1.3rem; line-height:1.35; font-weight:750; }
    .ops-brand { display:flex; align-items:center; gap:.7rem; min-height:42px; }
    .ops-mark {
      width:30px; height:30px; display:grid; place-items:center; border-radius:7px;
      color:#1b1712; font-weight:800; background:linear-gradient(145deg,#f0a34c,#dc6338);
      box-shadow:inset 0 0 0 1px rgba(255,255,255,.18);
    }
    .ops-title { color:var(--ops-text); font-size:1.08rem; font-weight:700; }
    .ops-subtitle { color:var(--ops-muted); font-size:.82rem; margin-top:2px; }
    .ops-status-row { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin:.2rem 0 .65rem; }
    .ops-pill {
      display:inline-flex; align-items:center; gap:6px; padding:.25rem .55rem;
      border:1px solid var(--ops-border); border-radius:999px; color:var(--ops-muted);
      background:var(--ops-surface); font-size:.78rem; font-weight:500;
    }
    .ops-pill.ok { color:var(--ops-ok); border-color:rgba(103,207,155,.3); background:rgba(103,207,155,.08); }
    .ops-pill.warn { color:var(--ops-warn); border-color:rgba(229,177,88,.3); background:rgba(229,177,88,.08); }
    .ops-panel { border:1px solid var(--ops-border); border-radius:var(--ops-radius-panel); background:var(--ops-surface); overflow:hidden; }
    .ops-panel-head {
      color:var(--ops-text); font-size:.95rem; font-weight:750; text-transform:uppercase;
      letter-spacing:.055em; padding:.9rem 1rem; border-bottom:1px solid var(--ops-border);
      border-left:3px solid var(--ops-heat);
      background:linear-gradient(90deg,rgba(233,134,58,.11),rgba(233,134,58,.015) 55%,transparent);
    }
    .ops-kpis { display:grid; grid-template-columns:repeat(2,1fr); gap:1px; background:var(--ops-border-soft); }
    .ops-kpi { background:var(--ops-surface); padding:.9rem; }
    .ops-kpi-label { color:var(--ops-muted); font-size:.75rem; text-transform:uppercase; letter-spacing:.045em; }
    .ops-kpi-value { color:var(--ops-text); font-size:1.35rem; font-weight:700; margin-top:.28rem; }
    .ops-kpi-value.heat { color:var(--ops-heat); }
    .ops-kpi-value.cool { color:var(--ops-cool); }
    .ops-zone-head { padding:1.1rem 1.2rem; }
    .ops-eyebrow { color:var(--ops-heat); font-size:.82rem; font-weight:700; text-transform:uppercase; letter-spacing:.075em; }
    .ops-zone-name { color:var(--ops-text); font-size:1.7rem; font-weight:700; margin:.25rem 0 .8rem; }
    .ops-zone-stats { display:grid; grid-template-columns:repeat(5,1fr); gap:1rem; }
    .ops-stat-label { color:var(--ops-muted); font-size:.73rem; text-transform:uppercase; letter-spacing:.045em; }
    .ops-stat-value { color:var(--ops-text); font-size:1rem; font-weight:650; margin-top:.2rem; }
    .ops-rec { border:1px solid rgba(233,134,58,.48); border-radius:var(--ops-radius-panel); background:linear-gradient(145deg,rgba(233,134,58,.075),rgba(33,31,28,.95)); padding:1.1rem; }
    .ops-rec-title { color:var(--ops-heat); font-size:.88rem; font-weight:750; text-transform:uppercase; letter-spacing:.065em; }
    .ops-rec-name { color:var(--ops-text); font-size:1.35rem; font-weight:750; margin:.28rem 0; }
    .ops-copy { color:var(--ops-muted); font-size:.88rem; line-height:1.55; }
    .ops-metric-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:.9rem 0; }
    .ops-metric { border:1px solid var(--ops-border-soft); border-radius:var(--ops-radius-card); background:rgba(24,22,19,.42); padding:.75rem .8rem; }
    .ops-metric-label { color:var(--ops-muted); font-size:.71rem; text-transform:uppercase; letter-spacing:.045em; }
    .ops-metric-value { color:var(--ops-text); font-size:1.12rem; font-weight:700; margin-top:.22rem; }
    .ops-metric-value.cool { color:var(--ops-cool); } .ops-metric-value.ok { color:var(--ops-ok); }
    .ops-wave { display:flex; gap:6px; margin-top:.5rem; }
    .ops-wave-item { flex:1; border:1px solid var(--ops-border-soft); border-top:3px solid var(--ops-heat); border-radius:var(--ops-radius-card); padding:.7rem; background:var(--ops-surface-2); }
    .ops-wave-title { color:var(--ops-text); font-size:.82rem; font-weight:650; }
    .ops-wave-meta { color:var(--ops-muted); font-size:.74rem; margin-top:.2rem; }
    .ops-guard { display:flex; align-items:center; gap:6px; color:var(--ops-ok); font-size:.8rem; line-height:1.4; margin-top:.75rem; }
    .ops-impact-row { padding:.8rem .9rem; border-bottom:1px solid var(--ops-border-soft); }
    .ops-impact-top { display:flex; justify-content:space-between; gap:1rem; color:var(--ops-muted); font-size:.82rem; }
    .ops-impact-top b { color:var(--ops-text); }
    .ops-track { height:5px; border-radius:999px; background:var(--ops-surface-2); margin:.4rem 0 .2rem; overflow:hidden; }
    .ops-fill { height:100%; border-radius:999px; background:var(--ops-ok); }
    .ops-caption { color:var(--ops-muted); font-size:.74rem; line-height:1.4; }
    .ops-provenance { color:var(--ops-muted); font-size:.76rem; line-height:1.55; }
    .ops-execution-plan {
      border:1px solid var(--ops-border); border-radius:var(--ops-radius-panel);
      background:var(--ops-surface); overflow:hidden; margin:.65rem 0 .8rem;
    }
    .ops-execution-row {
      display:grid; grid-template-columns:28px 1fr auto; align-items:center; gap:.7rem;
      padding:.72rem .8rem; border-bottom:1px solid var(--ops-border-soft);
    }
    .ops-execution-row:last-child { border-bottom:0; }
    .ops-execution-icon {
      width:28px; height:28px; display:grid; place-items:center; border-radius:8px;
      color:var(--ops-cool); background:rgba(114,203,208,.09);
      border:1px solid rgba(114,203,208,.22); font-size:.82rem; font-weight:700;
    }
    .ops-execution-title { color:var(--ops-text); font-size:.96rem; font-weight:700; }
    .ops-execution-meta { color:var(--ops-muted); font-size:.8rem; line-height:1.4; margin-top:.16rem; }
    .ops-execution-state {
      color:var(--ops-muted); font-size:.69rem; font-weight:650; text-transform:uppercase;
      letter-spacing:.05em; padding:.2rem .42rem; border:1px solid var(--ops-border);
      border-radius:999px;
    }
    .ops-execution-state.ready { color:var(--ops-ok); border-color:rgba(103,207,155,.3); }
    .ops-execution-result {
      border:1px solid rgba(103,207,155,.35); border-radius:var(--ops-radius-panel);
      background:rgba(103,207,155,.07); padding:.8rem .9rem; margin:.65rem 0;
    }
    .ops-execution-result strong { color:var(--ops-ok); font-size:1rem; }
    .ops-execution-result div { color:var(--ops-muted); font-size:.82rem; line-height:1.5; margin-top:.22rem; }
    div[data-testid="stButton"] button {
      border-color:var(--ops-border); background:var(--ops-surface); color:var(--ops-text);
      border-radius:var(--ops-radius-card); min-height:2.75rem; font-size:.82rem; text-align:left;
    }
    div[data-testid="stButton"] button:hover { border-color:rgba(233,134,58,.65); color:var(--ops-text); }
    div[data-testid="stCheckbox"] label p, div[data-testid="stCaptionContainer"] { color:var(--ops-muted); }
    div[data-testid="stDataFrame"], div[data-testid="stChatMessage"] { border:1px solid var(--ops-border); border-radius:var(--ops-radius-panel); overflow:hidden; }
    [data-testid="stExpander"] { border:1px solid var(--ops-border); border-radius:var(--ops-radius-panel); background:var(--ops-surface); }
    [data-testid="stTabs"] button { color:var(--ops-muted); font-size:.95rem; font-weight:650; }
    [data-testid="stTabs"] button[aria-selected="true"] { color:var(--ops-text); font-weight:750; }
    [data-testid="stMarkdownContainer"] h4 {
      color:var(--ops-text); font-size:1.28rem; font-weight:750;
      padding-bottom:.45rem; border-bottom:1px solid var(--ops-border);
    }
    [data-testid="stWidgetLabel"] p,
    [data-testid="stCheckbox"] label p,
    [data-testid="stCaptionContainer"],
    [data-testid="stMarkdownContainer"] li { font-size:.84rem; line-height:1.5; }
    [data-baseweb="input"] input { font-size:.9rem; }
    div[data-testid="stNumberInput"] label p {
      color:var(--ops-muted); font-size:.72rem; font-weight:650;
      text-transform:uppercase; letter-spacing:.04em;
    }
    div[data-testid="stNumberInput"] [data-baseweb="input"] {
      border-color:var(--ops-border); border-radius:var(--ops-radius-card);
      background:var(--ops-surface-2);
    }
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


@st.cache_data(ttl=900, show_spinner=False)
def load_model_evaluation_history(scenario: str):
    repository = HybridRepository(scenario=scenario)
    repository.load()
    return repository.load_model_evaluations(limit=10)


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
zone_selection_context = f"{scenario}:{snapshot_id}"
if (
    st.session_state.get("zone_selection_context") != zone_selection_context
    or st.session_state.get("selected_zone_id") not in valid_zone_ids
):
    st.session_state.zone_selection_context = zone_selection_context
    st.session_state.selected_zone_id = ordered[0].zone_id
selected = next(zone for zone in zones if zone.zone_id == st.session_state.selected_zone_id)

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
if result.freshness_warning:
    st.error(result.freshness_warning)

if "decision_budget_cap" not in st.session_state:
    st.session_state.decision_budget_cap = 120.0
if "decision_partner_credit" not in st.session_state:
    st.session_state.decision_partner_credit = 0.32
budget_cap = float(st.session_state.decision_budget_cap)
partner_per_driver = float(st.session_state.decision_partner_credit)

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

# ── Progressive-Disclosure Evidence ────────────────────────────────────────────
st.divider()
city_tab, drivers_tab, copilot_tab, model_tab = st.tabs(
    ["CITY INTELLIGENCE", "DRIVER EVIDENCE", "COPILOT & AUDIT", "MODEL PERFORMANCE"]
)

with city_tab:
    selection_context = f"{scenario}:{snapshot_id}"
    map_rows = []
    max_risk = max(zone_risk.values(), default=1.0)
    for zone in zones:
        expected = zone_risk.get(zone.zone_id)
        intensity = (expected or 0.0) / max_risk
        map_rows.append({
            "zone_id": zone.zone_id, "name": zone.name,
            "lat": zone.latitude, "lon": zone.longitude,
            "expected_events": round(expected, 2) if expected is not None else None,
            "heat_index": zone.heat_index_c, "active": zone.active_drivers,
            "color": [round(255 * max(.35, intensity)), round(150 * (1 - intensity)), 75, 210]
        })
    map_col, scatter_col, bar_col = st.columns([1, 1, 1], gap="medium")
    with map_col:
        map_event = st.pydeck_chart(
            pdk.Deck(
                layers=[
                    pdk.Layer(
                        "ScatterplotLayer", pd.DataFrame(map_rows), id="city-zones",
                        get_position=["lon", "lat"],
                        get_fill_color="color", get_radius="800 + active * 2", pickable=True,
                        stroked=True, get_line_color=[255, 255, 255, 80]
                    )
                ],
                initial_view_state=pdk.ViewState(latitude=21.025, longitude=105.81, zoom=10.1, pitch=35),
                map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
                tooltip=cast(Any, {"html": "<b>{name}</b><br/>Expected escalations: {expected_events}<br/>Heat Index: {heat_index}°C<br/>Active: {active}", "style": {"backgroundColor": "#211f1c", "color": "white"}}),
            ),
            height=360,
            key=f"city-zone-map:{selection_context}:{selected.zone_id}",
            on_select="rerun",
            selection_mode="single-object",
        )
        selected_objects = map_event.get("selection", {}).get("objects", {}).get("city-zones", [])
        clicked_zone_id = selected_objects[0].get("zone_id") if selected_objects else None
        if clicked_zone_id in valid_zone_ids and clicked_zone_id != selected.zone_id:
            st.session_state.selected_zone_id = clicked_zone_id
            st.rerun()
        st.caption(
            f"Selected: {selected.name}. Priority uses summed driver-level model probability."
        )

    highlighted_zone = selected.name

    city_plan = load_city_wide_ai_plan(scenario, snapshot_id)
    if city_plan:
        df_city = pd.DataFrame(city_plan)
        df_city = df_city.sort_values("Selected Drivers", ascending=False)
        with scatter_col:
            fig = px.scatter(
                df_city, x="Fulfillment Drop", y="ETA Impact", size="Prevented Risks", color="Heat Index",
                hover_name="Zone", color_continuous_scale=px.colors.sequential.YlOrRd, title="City-wide intervention tradeoffs",
                labels={"Fulfillment Drop": "Fulfillment Drop (%)", "ETA Impact": "ETA Impact (mins)", "Prevented Risks": "Expected Escalations Prevented", "Heat Index": "Heat Index (°C)"}
            )
            if highlighted_zone in set(df_city["Zone"]):
                scatter_opacity = [
                    1.0 if zone == highlighted_zone else 0.22
                    for zone in df_city["Zone"]
                ]
                fig.update_traces(marker_opacity=scatter_opacity)
                focused = df_city.loc[df_city["Zone"] == highlighted_zone].iloc[0]
                fig.add_trace(
                    go.Scatter(
                        x=[focused["Fulfillment Drop"]],
                        y=[focused["ETA Impact"]],
                        mode="markers",
                        marker=dict(
                            size=28,
                            color="rgba(0,0,0,0)",
                            line=dict(color="#f5f1e8", width=3),
                        ),
                        hoverinfo="skip",
                        showlegend=False,
                    )
                )
            fig.update_layout(
                **PLOTLY_LAYOUT, margin=dict(l=0, r=0, t=75, b=0), height=360,
                xaxis=dict(gridcolor="rgba(255,255,255,.06)"),
                yaxis=dict(gridcolor="rgba(255,255,255,.06)"),
                title_x=0.5, title_xanchor="center",
                coloraxis_colorbar=dict(title_side="right")
            )
            fig.add_vline(x=2.0, line_dash="dash", line_color="red", opacity=0.5)
            fig.add_hline(y=2.0, line_dash="dash", line_color="red", opacity=0.5)
            st.plotly_chart(fig, width="stretch")

        with bar_col:
            import plotly.graph_objects as go
            fig_plan = go.Figure()
            df_city["Model-prioritized Drivers"] = df_city["Selected Drivers"] - df_city["Mandatory Drivers"]
            bar_opacity = [
                1.0 if not highlighted_zone or zone == highlighted_zone else 0.22
                for zone in df_city["Zone"]
            ]
            fig_plan.add_trace(go.Bar(name="Mandatory Drivers", x=df_city["Zone"], y=df_city["Mandatory Drivers"], marker=dict(opacity=bar_opacity), hovertemplate="Mandatory: %{y}<br>Total Selected: %{customdata}", customdata=df_city["Selected Drivers"]))
            fig_plan.add_trace(go.Bar(name="Model-prioritized Drivers", x=df_city["Zone"], y=df_city["Model-prioritized Drivers"], marker=dict(opacity=bar_opacity), hovertemplate="Model-prioritized: %{y}<br>Total Selected: %{customdata}", customdata=df_city["Selected Drivers"]))

            fig_plan.update_layout(
                barmode='stack',
                title="SafePause Coverage & Estimated Risk Reduction",
                title_x=0.5,
                title_xanchor="center",
                **PLOTLY_LAYOUT, margin=dict(l=0, r=0, t=75, b=0), height=360,
                legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="right", x=1)
            )
            fig_plan.update_xaxes(title_text="Zone", gridcolor="rgba(255,255,255,.06)")
            fig_plan.update_yaxes(title_text="Selected Drivers", gridcolor="rgba(255,255,255,.06)")
            st.plotly_chart(fig_plan, width="stretch")
    else:
        with scatter_col:
            st.info("City-wide AI predictions are currently unavailable.")

with drivers_tab:
    if proposal:
        st.markdown("#### Selected Driver list for SafePause")
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
            )
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
        copilot_context = f"{COPILOT_STATE_VERSION}:{scenario}:{selected.zone_id}"
        if st.session_state.get("copilot_context") != copilot_context:
            st.session_state.copilot_context = copilot_context
            st.session_state.messages = []
        suggested_questions = (
            f"Why is {selected.name} prioritized now?",
            f"Forecast demand in {selected.name} for the next 60 minutes",
            f"Compare SafePause options in {selected.name} with a budget of $100",
            "Which area should we intervene in over the next 90 minutes with a budget of $150?",
        )
        st.markdown(
            '<div class="ops-eyebrow" style="margin:.35rem 0 .45rem">Suggested questions</div>',
            unsafe_allow_html=True,
        )
        suggested_question = None
        suggestion_columns = st.columns(2, gap="small")
        for index, prompt in enumerate(suggested_questions):
            with suggestion_columns[index % 2]:
                if st.button(
                    prompt,
                    key=f"copilot-suggestion-{copilot_context}-{index}",
                    width="stretch",
                ):
                    suggested_question = prompt
        for message in st.session_state.messages[-6:]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                if message.get("tool"):
                    st.caption(f"Verified tool trace: {message['tool']}")
        typed_question = st.chat_input("Ask HeatSafe Copilot...")
        question = suggested_question or typed_question
        if question:
            st.session_state.messages.append({"role": "user", "content": question})
            repository = HybridRepository(scenario=scenario)
            repository.load()
            answer, tool = HeatSafeCopilot(zones, repository).answer(question)
            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "tool": tool}
            )
            st.rerun()
    with audit_col:
        st.markdown(f"#### {selected.name} simulation audit")
        audit_rows = [
            row for row in audit.list_recent()
            if row.get("zone_id") == selected.zone_id
        ]
        if audit_rows:
            st.dataframe(pd.DataFrame(audit_rows), hide_index=True, width="stretch")
        else:
            st.info("No simulated intervention recorded.")

with model_tab:
    try:
        evaluations = load_model_evaluation_history(scenario)
    except Exception as exc:
        evaluations = []
        log_event(
            "model_evaluations_unavailable",
            severity="WARNING",
            error_type=type(exc).__name__,
        )

    if evaluations:
        active_version = proposal.model_version if proposal else evaluations[0]["model_version"]
        active_evaluation = next(
            (
                item
                for item in evaluations
                if item["model_version"] == active_version
            ),
            evaluations[0],
        )
        active_version = active_evaluation["model_version"]
        metric_specs = (
            ("ROC AUC", "roc_auc"),
            ("F1", "f1_score"),
            ("Precision", "precision"),
            ("Recall", "recall"),
            ("Log loss", "log_loss"),
        )
        metric_columns = st.columns(len(metric_specs))
        for column, (label, field) in zip(metric_columns, metric_specs):
            value = active_evaluation.get(field)
            column.metric(label, "—" if value is None else f"{float(value):.3f}")

        score_rows = [
            {"Metric": label, "Score": float(active_evaluation[field])}
            for label, field in metric_specs[:-1]
            if active_evaluation.get(field) is not None
        ]
        history = pd.DataFrame(evaluations)
        history["evaluated_at"] = pd.to_datetime(history["evaluated_at"])

        history_table = history.rename(
            columns={
                "model_version": "Model version",
                "evaluated_at": "Evaluated at",
                "roc_auc": "ROC AUC",
                "f1_score": "F1",
                "precision": "Precision",
                "recall": "Recall",
                "accuracy": "Accuracy",
                "log_loss": "Log loss",
            }
        )
        styled_table = history_table[
            [
                "Model version", "Evaluated at", "ROC AUC", "F1",
                "Precision", "Recall", "Accuracy", "Log loss",
            ]
        ].style.set_properties(**{'font-weight': 'bold', 'color': '#72cbd0'}, subset=['Model version'])

        st.dataframe(
            styled_table,
            hide_index=True,
            use_container_width=True,
        )
        prediction_run = proposal.prediction_run_id if proposal else "not available"
        st.caption(
            f"Active risk model: {active_version} · Prediction run: {prediction_run} · "
            f"BigQuery ML boosted-tree · Evaluation data: "
            f"{'simulated' if active_evaluation.get('is_simulated') else 'production'}"
        )
    else:
        st.info("Model evaluation metrics are available in cloud mode after BigQuery ML training.")


# ── Selected-Zone Decision Workspace ───────────────────────────────────────────
center_col, right_col = st.columns([2.2, 1], gap="medium")

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
            st.markdown('<div class="ops-eyebrow" style="margin-top: 1rem; margin-bottom: 0.5rem;">Demand forecast and recommendation evidence</div>', unsafe_allow_html=True)
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
    cost_control_col, partner_control_col = st.columns(2, gap="small")
    with cost_control_col:
        st.number_input(
            "Cost cap ($)",
            min_value=0.0,
            step=10.0,
            key="decision_budget_cap",
        )
    with partner_control_col:
        st.number_input(
            "Partner / driver ($)",
            min_value=0.0,
            step=0.04,
            key="decision_partner_credit",
        )
    if proposal:
        fulfillment_drop = max(
            0.0,
            (proposal.baseline_stress_fulfillment_rate - proposal.p90_fulfillment_rate)
            * 100,
        )
        cost_pct = min(100.0, proposal.net_platform_cost_vnd / max(budget_cap * 25_000, 1) * 100)
        impact_rows = (
            ("Fulfillment drop", f"{fulfillment_drop:.1f}%", min(100, fulfillment_drop / 2.0 * 100), "maximum 2.0%"),
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

    st.markdown('<div class="ops-panel-head" style="margin-top:.8rem">Execute SafePause</div>', unsafe_allow_html=True)
    if proposal:
        st.markdown(
            f'<div class="ops-copy" style="padding:.65rem 0">{selected.name} · {proposal.selected_drivers} drivers · '
            f'{proposal.waves} waves · {proposal.pause_minutes}m recovery</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="ops-execution-plan">'
            '<div class="ops-execution-row">'
            '<div class="ops-execution-icon">H</div>'
            '<div><div class="ops-execution-title">Activate hydration support</div>'
            f'<div class="ops-execution-meta">{proposal.selected_drivers} drivers · {format_currency_vnd(proposal.partner_hydration_value_vnd)} partner value</div></div>'
            '<div class="ops-execution-state ready">Ready</div></div>'
            '<div class="ops-execution-row">'
            '<div class="ops-execution-icon">N</div>'
            '<div><div class="ops-execution-title">Notify selected drivers</div>'
            f'<div class="ops-execution-meta">Safety guidance and assigned recovery window · {proposal.selected_drivers} recipients</div></div>'
            '<div class="ops-execution-state ready">Ready</div></div>'
            '<div class="ops-execution-row">'
            '<div class="ops-execution-icon">W</div>'
            '<div><div class="ops-execution-title">Schedule staggered pause waves</div>'
            f'<div class="ops-execution-meta">{proposal.waves} waves · first wave starts now · supply guardrails active</div></div>'
            '<div class="ops-execution-state ready">Ready</div></div>'
            '</div>',
            unsafe_allow_html=True,
        )
        confirm = st.checkbox(
            "I confirm this rollout plan and understand this is a demo environment",
            key=f"confirm-{proposal.proposal_id}",
        )
        if st.button(
            "Activate SafePause",
            disabled=not (
                confirm and proposal.within_guardrails and result.data_fresh
            ),
            width="stretch",
            type="primary",
        ):
            event = audit.approve(proposal)
            st.session_state["simulated_execution"] = {
                "proposal_id": proposal.proposal_id,
                "intervention_id": event.intervention_id,
            }
            st.cache_data.clear()
        execution = st.session_state.get("simulated_execution")
        if execution and execution.get("proposal_id") == proposal.proposal_id:
            st.markdown(
                '<div class="ops-execution-result">'
                '<strong>SafePause activated · simulation</strong>'
                f'<div>Hydration support activated for {proposal.selected_drivers} drivers<br>'
                f'Driver notifications queued · {proposal.selected_drivers} recipients<br>'
                f'{proposal.waves} recovery waves scheduled · Audit {execution["intervention_id"][:8]}</div>'
                '</div>',
                unsafe_allow_html=True,
            )
    else:
        st.button("Activate SafePause", disabled=True, width="stretch")
    st.caption("Demo execution only — the workflow is recorded for audit, but no driver notification, hydration order or operational command is sent.")

refresh_col, policy_col = st.columns([1, 5], vertical_alignment="center")
with refresh_col:
    if st.button("Refresh data", width="stretch"):
        st.cache_data.clear()
        st.rerun()
with policy_col:
    st.caption("AI failure policy: fail closed; monitoring remains available. Source UI direction: Lovable reference console.")
