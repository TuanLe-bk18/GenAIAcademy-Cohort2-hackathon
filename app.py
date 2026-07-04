from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pydeck as pdk
import streamlit as st

from heatsafe.audit import HybridInterventionAuditStore
from heatsafe.copilot import HeatSafeCopilot
from heatsafe.config import Settings
from heatsafe.repository import HybridRepository
from heatsafe.risk import (
    TIER_COLORS,
    TIER_LABELS,
    heat_tier,
    operational_priority,
    priority_label,
)
from heatsafe.safepause import simulate_safepause
from heatsafe.telemetry import log_event

st.set_page_config(page_title="HeatSafe Ops", page_icon="☀️", layout="wide")

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
      html, body, [class*="css"]  { font-family: 'Inter', sans-serif; }
      .stApp { 
        background-color: #0b1220;
        background-image: radial-gradient(circle at 15% 50%, rgba(255, 138, 80, 0.08), transparent 25%),
                          radial-gradient(circle at 85% 30%, rgba(89, 214, 154, 0.08), transparent 25%);
        background-attachment: fixed;
        color: #e8eef8; 
      }
      [data-testid="stMetricValue"] { font-size: 1.8rem !important; }
      [data-testid="stMetricLabel"] { font-size: 0.85rem !important; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      [data-testid="stHeader"] { background: transparent; }
      .block-container { padding-top: 1.5rem; max-width: 1500px; }
      .hero { 
        padding: 1.5rem 2rem; 
        border: 1px solid rgba(255, 255, 255, 0.08); 
        border-radius: 20px;
        background: rgba(17, 29, 50, 0.6); 
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
        margin-bottom: 1.5rem; 
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }
      .hero:hover { transform: translateY(-2px); box-shadow: 0 12px 40px 0 rgba(0, 0, 0, 0.4); }
      .hero h1 { margin: 0; color: #ff8a50; font-size: 2.4rem; font-weight: 700; letter-spacing: -0.02em; }
      .hero p { margin: 0.5rem 0 0; color: #aebbd0; font-size: 1.1rem; }
      .source-badge { 
        display: inline-block; padding: 0.3rem 0.8rem; border-radius: 999px;
        background: rgba(34, 50, 74, 0.8); color: #bcd2ef; font-size: 0.8rem; 
        margin-top: 1rem; border: 1px solid rgba(188, 210, 239, 0.2); 
      }
      .panel { 
        border: 1px solid rgba(255, 255, 255, 0.05); 
        border-radius: 18px; 
        padding: 1.2rem; 
        background: rgba(17, 26, 42, 0.7); 
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        box-shadow: 0 4px 24px -1px rgba(0, 0, 0, 0.2);
        transition: border-color 0.3s ease;
      }
      .panel:hover { border-color: rgba(255, 255, 255, 0.15); }
      .risk-note { color: #96a7bf; font-size: 0.85rem; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; }
      div[data-testid="stMetric"] { 
        background: rgba(17, 26, 42, 0.7); 
        border: 1px solid rgba(255, 255, 255, 0.05);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        padding: 1rem 1.2rem; 
        border-radius: 16px; 
        box-shadow: 0 4px 16px rgba(0,0,0,0.1);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }
      div[data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(0,0,0,0.15);
        border-color: rgba(255, 255, 255, 0.1);
      }
      .good { color: #59d69a; font-weight: 700; text-shadow: 0 0 10px rgba(89,214,154,0.3); }
      .warn { color: #ffb45c; font-weight: 700; text-shadow: 0 0 10px rgba(255,180,92,0.3); }
      /* Highlight Number Input boxes */
      div[data-testid="stNumberInput"] > div {
        background: rgba(17, 26, 42, 0.6) !important;
        border: 1px solid rgba(255, 138, 80, 0.4) !important;
        border-radius: 8px !important;
        transition: all 0.3s ease !important;
      }
      div[data-testid="stNumberInput"] > div:hover {
        border-color: rgba(255, 138, 80, 0.6) !important;
        box-shadow: 0 0 8px rgba(255, 138, 80, 0.2) !important;
      }
      div[data-testid="stNumberInput"] > div:focus-within {
        border-color: rgba(255, 138, 80, 1.0) !important;
        box-shadow: 0 0 12px rgba(255, 138, 80, 0.4) !important;
      }
      div[data-testid="stNumberInput"] label p {
        font-weight: 600 !important;
        color: #ff9a62 !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=300, show_spinner=False)
def load_snapshot(scenario: str):
    return HybridRepository(scenario=scenario).load()


@st.cache_data(ttl=900, show_spinner=False)
def load_demand_forecast(zone_id: str, horizon_minutes: int, scenario: str):
    repository = HybridRepository(scenario=scenario)
    repository.load()
    return repository.forecast_demand(zone_id, horizon_minutes)


def format_currency(value: float) -> str:
    if pd.isna(value):
        return "N/A"
    return f"${value / 25000:,.2f}"


settings = Settings.from_env()
scenario = st.sidebar.selectbox(
    "Operating scenario",
    options=["heatwave", "live"],
    index=0 if settings.scenario == "heatwave" else 1,
    format_func=lambda value: "Heatwave replay" if value == "heatwave" else "Live weather",
)
result = load_snapshot(scenario)
zones = result.zones
audit = HybridInterventionAuditStore()

if not zones:
    st.error("No operational data available.")
    st.stop()

top_zone = max(zones, key=lambda zone: (operational_priority(zone), zone.heat_index_c))
active_drivers = sum(zone.active_drivers for zone in zones)
priority_drivers = sum(zone.exposed_2h for zone in zones if operational_priority(zone) >= 70)
danger_zones = sum(heat_tier(zone.heat_index_c) in {"DANGER", "EXTREME_DANGER"} for zone in zones)
protected_drivers = audit.protected_driver_count()

st.markdown(
    f"""
    <section class="hero">
      <h1>☀️ HeatSafe Ops</h1>
      <p>Profit-aware heat capacity orchestration for two-wheel ride-hailing operations.</p>
      <span class="source-badge">{result.mode.upper()} · {result.source_label}</span>
    </section>
    """,
    unsafe_allow_html=True,
)

if result.fallback_reason:
    log_event("snapshot_fallback_used", severity="WARNING", reason=result.fallback_reason)
    st.info(f"Cloud data unavailable; using demo snapshot. Reason: {result.fallback_reason}")
if result.freshness_warning:
    log_event("stale_live_snapshot", severity="WARNING", reason=result.freshness_warning)
    st.error(result.freshness_warning)
if any(zone.is_simulated for zone in zones):
    st.warning(
        "Demo scenario: partial operational or weather data is simulated; "
        "review source and timestamp before approval."
    )
metric_cols = st.columns(4)
metric_cols[0].metric("Active drivers", f"{active_drivers:,}")
metric_cols[1].metric("Dangerous hotspots", danger_zones, delta=f"Top: {top_zone.name}", delta_color="off")
metric_cols[2].metric("Priority cohort", f"{priority_drivers:,}", delta="Active ≥2 hours", delta_color="off")
metric_cols[3].metric("Simulated", f"{protected_drivers:,}", delta="Decision audit", delta_color="off")

map_col, detail_col = st.columns([1.8, 1], gap="large")

map_rows = []
for zone in zones:
    tier = heat_tier(zone.heat_index_c)
    map_rows.append(
        {
            "zone_id": zone.zone_id,
            "name": zone.name,
            "lat": zone.latitude,
            "lon": zone.longitude,
            "heat_index_c": zone.heat_index_c,
            "tier_label": TIER_LABELS[tier],
            "priority": operational_priority(zone),
            "active_drivers": zone.active_drivers,
            "color": TIER_COLORS[tier],
        }
    )
map_df = pd.DataFrame(map_rows)

with map_col:
    st.subheader("Operational Heat Map")
    heat_layer = pdk.Layer(
        "ScatterplotLayer",
        map_df,
        get_position=["lon", "lat"],
        get_fill_color="color",
        get_radius="900 + active_drivers * 2",
        pickable=True,
        stroked=True,
        get_line_color=[255, 255, 255, 90],
        line_width_min_pixels=1,
    )
    st.pydeck_chart(
        pdk.Deck(
            layers=[heat_layer],
            initial_view_state=pdk.ViewState(latitude=21.025, longitude=105.81, zoom=10.1, pitch=35),
            map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
            tooltip={
                "html": "<b>{name}</b><br/>Heat Index: {heat_index_c}°C<br/>{tier_label}<br/>Priority: {priority}/100<br/>Active: {active_drivers}",
                "style": {"backgroundColor": "#111827", "color": "white"},
            },
        ),
        width="stretch",
        height=480,
    )
    st.caption("Heat Index is a screening indicator. Operational priority is not a medical diagnosis.")

with detail_col:
    st.subheader("Zone Decision Panel")
    zone_name = st.selectbox(
        "Zone",
        [
            zone.name
            for zone in sorted(
                zones,
                key=lambda item: (operational_priority(item), item.heat_index_c),
                reverse=True,
            )
        ],
    )
    selected = next(zone for zone in zones if zone.name == zone_name)
    try:
        demand_forecast = load_demand_forecast(selected.zone_id, 30, scenario)
        forecast_available = True
        selected_with_forecast = replace(
            selected, forecast_requests_30m=demand_forecast.predicted_requests
        )
    except Exception as exc:
        demand_forecast = None
        forecast_available = False
        selected_with_forecast = selected
        log_event(
            "demand_forecast_unavailable",
            severity="WARNING",
            zone_id=selected.zone_id,
            error_type=type(exc).__name__,
        )
        st.error(f"Demand forecast unavailable: {exc}")
    tier = heat_tier(selected.heat_index_c)
    score = operational_priority(selected)
    coolstop_name = selected.coolstop_name
    st.markdown(
        f'<div class="panel" style="margin-top: 1rem; display: flex; flex-direction: column; gap: 1rem;">'
        f'  <div>'
        f'    <div style="font-size: 0.75rem; letter-spacing: 1px; color: #8e9bb0; text-transform: uppercase; font-weight: 600; margin-bottom: 0.2rem;">Operational Priority</div>'
        f'    <div style="font-size: 2.2rem; font-weight: 700; color: #ff9a62; line-height: 1.2;">{score}/100 <span style="font-size: 1.2rem; font-weight: 500; opacity: 0.9;">· {priority_label(score)}</span></div>'
        f'  </div>'
        f'  <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem; padding: 1rem; background: rgba(0,0,0,0.2); border-radius: 8px;">'
        f'    <div><div style="font-size: 0.8rem; color: #8e9bb0; margin-bottom: 0.2rem;">Heat Index</div><div style="font-size: 1.1rem; font-weight: 600;">{selected.heat_index_c:.1f}°C <span style="font-size: 0.9rem; font-weight: 400; color: {TIER_COLORS[tier]}">({TIER_LABELS[tier]})</span></div></div>'
        f'    <div><div style="font-size: 0.8rem; color: #8e9bb0; margin-bottom: 0.2rem;">Demand (30m)</div><div style="font-size: 1.1rem; font-weight: 600;">{selected_with_forecast.forecast_requests_30m if forecast_available else "N/A"} <span style="font-size: 0.9rem; font-weight: 400;">reqs</span></div></div>'
        f'  </div>'
        f'  <div style="display: flex; justify-content: space-between; font-size: 0.9rem; color: #e8eef8; background: rgba(0,0,0,0.1); padding: 0.8rem 1rem; border-radius: 8px;">'
        f'    <span style="text-align: center;"><b>{selected.active_drivers}</b><br/><span style="font-size: 0.75rem; color: #8e9bb0;">Active</span></span>'
        f'    <span style="text-align: center;"><b>{selected.exposed_2h}</b><br/><span style="font-size: 0.75rem; color: #8e9bb0;">Exp ≥2h</span></span>'
        f'    <span style="text-align: center;"><b>{selected.exposed_4h}</b><br/><span style="font-size: 0.75rem; color: #8e9bb0;">Exp ≥4h</span></span>'
        f'  </div>'
        f'  <div style="margin-top: 0.5rem; padding-top: 1rem; border-top: 1px solid rgba(255,255,255,0.1); font-size: 0.9rem;">'
        f'    <span style="color: #8e9bb0;">CoolStop:</span> <b>{coolstop_name}</b> <span style="font-size:0.8rem; color:#8e9bb0; float: right;">(illustrative)</span>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

st.divider()
st.subheader("SafePause Simulator")
st.caption(
    "Smart Pause + Earnings Guard + CoolStop Partner. "
    f"Demand source: {demand_forecast.source if demand_forecast else 'Unavailable'}. "
    "All impacts below are scenario estimates."
)
if demand_forecast and demand_forecast.points:
    forecast_points = pd.DataFrame(
        [
            {
                "Time": point.forecast_at,
                "Median": point.predicted_requests,
                "Lower (pointwise)": point.lower_bound,
                "Upper (pointwise)": point.upper_bound,
            }
            for point in demand_forecast.points
        ]
    ).set_index("Time")
    st.line_chart(forecast_points)
    st.caption("90% bound applies to each 15-minute interval; it is not a confidence interval for the full 30 minutes.")

control_cols = st.columns(4)
pause_minutes = control_cols[0].number_input("Pause duration (min)", min_value=5, max_value=60, value=20, step=5)
waves = control_cols[1].number_input("Staggered waves", min_value=1, max_value=10, value=3, step=1)
budget_cap = control_cols[2].number_input("Platform cost cap ($)", min_value=0.0, value=40.0, step=4.0)
sponsor_per_driver = control_cols[3].number_input("Partner contribution / driver ($)", min_value=0, value=8, step=1)

proposal_key = (
    selected.zone_id,
    selected.snapshot_id,
    demand_forecast.predicted_requests if demand_forecast else None,
    pause_minutes,
    waves,
    budget_cap,
    sponsor_per_driver,
)
if st.session_state.get("proposal_key") != proposal_key:
    st.session_state.proposal = simulate_safepause(
        selected_with_forecast,
        pause_minutes=pause_minutes,
        waves=waves,
        budget_cap_vnd=int(budget_cap * 25000),
        sponsor_per_driver_vnd=int(sponsor_per_driver * 25000),
    )
    st.session_state.proposal_key = proposal_key
proposal = st.session_state.proposal

# --- Sleeek Dashboard Results Layout ---
earnings_guard = proposal.earnings_guard_cost_vnd / 25000
lost_contrib = proposal.lost_contribution_vnd / 25000
partner_fund = -proposal.partner_sponsorship_vnd / 25000
hydration = proposal.partner_hydration_value_vnd / 25000

costs = [
    {"name": "Earnings Guard", "val": earnings_guard, "color": "#ff7a45"},
    {"name": "Lost Contrib.", "val": lost_contrib, "color": "#ff9a62"},
    {"name": "Hydration (In-Kind)", "val": hydration, "color": "#36cfc9"},
    {"name": "Partner Fund (Credit)", "val": partner_fund, "color": "#52c41a" if partner_fund < 0 else "#ff7a45"},
]
max_val = max(abs(c["val"]) for c in costs) or 1.0

bar_html = []
for c in costs:
    pct = (abs(c["val"]) / max_val) * 100
    val_str = f"-${abs(c['val']):,.2f}" if c["val"] < 0 else f"+${c['val']:,.2f}"
    align = "flex-end" if c["val"] < 0 else "flex-start"
    bar_html.append(
        f'<div style="margin-bottom: 0.8rem;">'
        f'<div style="display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 0.2rem;">'
        f'<span style="color: #aebbd0; font-weight: 500;">{c["name"]}</span>'
        f'<span style="font-family: monospace; font-weight: 600; color: {c["color"]};">{val_str}</span>'
        f'</div>'
        f'<div style="background: rgba(255,255,255,0.05); height: 6px; border-radius: 3px; overflow: hidden; display: flex; justify-content: {align};">'
        f'<div style="background: {c["color"]}; width: {pct:.1f}%; height: 100%; border-radius: 3px;"></div>'
        f'</div>'
        f'</div>'
    )

cost_bars_html = (
    f'<div class="panel" style="padding: 1.2rem; display: flex; flex-direction: column; gap: 0.8rem; background: rgba(30, 41, 59, 0.45); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 12px; height: 100%;">'
    f'<div style="font-size: 0.75rem; letter-spacing: 1px; color: #8e9bb0; text-transform: uppercase; font-weight: 600; margin-bottom: 0.2rem;">Cost Breakdown ($)</div>'
    f'<div style="display: flex; flex-direction: column; justify-content: center; height: 100%;">'
    f'{"".join(bar_html)}'
    f'</div>'
    f'</div>'
)

metrics_html = (
    f'<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 0.8rem; margin-bottom: 1.2rem; width: 100%;">'
    f'<div class="panel" style="padding: 0.8rem; text-align: center; display: flex; flex-direction: column; justify-content: center; min-height: 80px; background: rgba(30, 41, 59, 0.4); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 10px;">'
    f'<div style="font-size: 0.7rem; color: #8e9bb0; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; margin-bottom: 0.2rem;">Eligible</div>'
    f'<div style="font-size: 1.6rem; font-weight: 700; color: #fff;">{proposal.eligible_drivers}</div>'
    f'</div>'
    f'<div class="panel" style="padding: 0.8rem; text-align: center; display: flex; flex-direction: column; justify-content: center; min-height: 80px; background: rgba(30, 41, 59, 0.4); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 10px;">'
    f'<div style="font-size: 0.7rem; color: #8e9bb0; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; margin-bottom: 0.2rem;">Avoided</div>'
    f'<div style="font-size: 1.6rem; font-weight: 700; color: #36cfc9;">{proposal.exposure_minutes_avoided:,}m</div>'
    f'</div>'
    f'<div class="panel" style="padding: 0.8rem; text-align: center; display: flex; flex-direction: column; justify-content: center; min-height: 80px; background: rgba(30, 41, 59, 0.4); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 10px;">'
    f'<div style="font-size: 0.7rem; color: #8e9bb0; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; margin-bottom: 0.2rem;">Reassigned</div>'
    f'<div style="font-size: 1.6rem; font-weight: 700; color: #ff9a62;">{proposal.reassigned_trips}</div>'
    f'</div>'
    f'<div class="panel" style="padding: 0.8rem; text-align: center; display: flex; flex-direction: column; justify-content: center; min-height: 80px; background: rgba(30, 41, 59, 0.4); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 10px;">'
    f'<div style="font-size: 0.7rem; color: #8e9bb0; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; margin-bottom: 0.2rem;">Missed</div>'
    f'<div style="font-size: 1.6rem; font-weight: 700; color: #ff4d4f;">{proposal.missed_trips}</div>'
    f'</div>'
    f'<div class="panel" style="padding: 0.8rem; text-align: center; display: flex; flex-direction: column; justify-content: center; min-height: 80px; background: rgba(30, 41, 59, 0.4); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 10px;">'
    f'<div style="font-size: 0.7rem; color: #8e9bb0; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; margin-bottom: 0.2rem;">Net Cost</div>'
    f'<div style="font-size: 1.6rem; font-weight: 700; color: #fff;">{format_currency(proposal.net_platform_cost_vnd)}</div>'
    f'</div>'
    f'<div class="panel" style="padding: 0.8rem; text-align: center; display: flex; flex-direction: column; justify-content: center; min-height: 80px; background: rgba(30, 41, 59, 0.4); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 10px;">'
    f'<div style="font-size: 0.7rem; color: #8e9bb0; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; margin-bottom: 0.2rem;">Fulfillment</div>'
    f'<div style="font-size: 1.6rem; font-weight: 700; color: #52c41a;">{proposal.projected_fulfillment_rate:.1%}</div>'
    f'</div>'
    f'</div>'
)

st.markdown(metrics_html, unsafe_allow_html=True)

cost_col, approval_col = st.columns([1.2, 1], gap="large")
with cost_col:
    st.markdown(cost_bars_html, unsafe_allow_html=True)

with approval_col:
    dot_color = "#52c41a" if proposal.within_guardrails else "#ff4d4f"
    status_text_color = "#52c41a" if proposal.within_guardrails else "#ff4d4f"
    
    style_block_html = (
        f'<style>'
        f'@keyframes pulse {{'
        f'  0% {{ transform: scale(0.9); opacity: 0.6; }}'
        f'  50% {{ transform: scale(1.1); opacity: 1; }}'
        f'  100% {{ transform: scale(0.9); opacity: 0.6; }}'
        f'}}'
        f'</style>'
    )
    status_card_html = (
        f'<div class="panel" style="padding: 1.2rem; display: flex; flex-direction: column; gap: 0.8rem; background: rgba(30, 41, 59, 0.45); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 12px; margin-bottom: 0.8rem;">'
        f'<div style="display: flex; align-items: center; gap: 0.6rem;">'
        f'<span style="height: 8px; width: 8px; border-radius: 50%; background-color: {dot_color}; display: inline-block; box-shadow: 0 0 8px {dot_color}; animation: pulse 2s infinite;"></span>'
        f'<span style="font-weight: 600; font-size: 0.95rem; color: {status_text_color};">{proposal.guardrail_notes[0]}</span>'
        f'</div>'
        f'<div style="font-size: 0.85rem; color: #aebbd0; display: flex; flex-direction: column; gap: 0.4rem; border-top: 1px solid rgba(255, 255, 255, 0.05); padding-top: 0.6rem;">'
        f'<div><span style="color: #8e9bb0;">ETA Impact:</span> <b>+{proposal.projected_eta_increase_minutes:.1f} min</b></div>'
        f'<div><span style="color: #8e9bb0;">Logistics:</span> <b>{proposal.waves}</b> staggered waves · Max <b>{proposal.planned_paused_driver_slots}</b> drivers/wave</div>'
        f'</div>'
        f'</div>'
    )
    st.markdown(style_block_html + status_card_html, unsafe_allow_html=True)
    
    st.caption("⚠️ **Demo Mode:** Records a simulated decision audit; no commands sent to drivers.")
    confirm = st.checkbox("I confirm this is a simulated intervention")
    st.markdown("""
    <style>
      div.stButton > button:first-child {
        width: 100%;
        background: linear-gradient(135deg, #ff7a45 0%, #ff9a62 100%);
        border: none;
        color: white;
        font-weight: 600;
        padding: 0.6rem 1.2rem;
        border-radius: 8px;
        transition: all 0.3s ease;
        box-shadow: 0 4px 12px rgba(255, 122, 69, 0.2);
      }
      div.stButton > button:first-child:hover {
        background: linear-gradient(135deg, #ff9a62 0%, #ffb38a 100%) !important;
        box-shadow: 0 6px 16px rgba(255, 122, 69, 0.4) !important;
        transform: translateY(-1px);
        color: white !important;
      }
      div.stButton > button:first-child:active {
        transform: translateY(1px);
      }
      div.stButton > button:first-child:disabled {
        background: rgba(255, 255, 255, 0.05) !important;
        color: rgba(255, 255, 255, 0.25) !important;
        border: 1px solid rgba(255, 255, 255, 0.05) !important;
        box-shadow: none !important;
        transform: none !important;
      }
    </style>
    """, unsafe_allow_html=True)
    
    if st.button(
        "Record simulated intervention",
        disabled=not (
            confirm
            and forecast_available
            and result.data_fresh
            and proposal.within_guardrails
            and proposal.eligible_drivers > 0
        ),
        use_container_width=True
    ):
        event = audit.approve(proposal)
        log_event(
            "simulated_intervention_recorded",
            intervention_id=event.intervention_id,
            proposal_id=proposal.proposal_id,
            zone_id=proposal.zone_id,
            audit_backend=audit.backend,
        )
        st.success(
            f"Recorded simulation {event.intervention_id[:8]} · no operational command sent."
        )
        st.cache_data.clear()
        st.rerun()

st.divider()
copilot_col, audit_col = st.columns([1.2, 1], gap="large")

with copilot_col:
    st.subheader("HeatSafe Copilot")
    st.caption("Only calls approved analytics tools; does not generate or execute SQL.")
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": "Ask me about priority zones, causes, or SafePause costs."}
        ]
    for message in st.session_state.messages[-6:]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("tool"):
                st.caption(f"Decision tool trace: {message['tool']}")
    if question := st.chat_input("Example: Which zone needs intervention first and what is the cost?"):
        st.session_state.messages.append({"role": "user", "content": question})
        with st.spinner("Analyzing snapshot..."):
            repository = HybridRepository(scenario=scenario)
            repository.load()
            answer, tool = HeatSafeCopilot(zones, repository).answer(question)
        st.session_state.messages.append({"role": "assistant", "content": answer, "tool": tool})
        st.rerun()

with audit_col:
    st.subheader("Intervention Audit")
    audit_rows = audit.list_recent()
    if audit_rows:
        audit_df = pd.DataFrame(audit_rows)
        audit_df["net_platform_cost"] = audit_df["net_platform_cost_vnd"].map(format_currency)
        st.dataframe(audit_df.drop(columns=["net_platform_cost_vnd"]), width="stretch", hide_index=True)
    else:
        st.info("No intervention has been approved yet.")

with st.sidebar:
    st.header("Demo controls")
    st.markdown(
        f"""
        <div style="background: rgba(17, 26, 42, 0.7); border: 1px solid rgba(255, 255, 255, 0.05); padding: 1.2rem; border-radius: 14px; font-size: 0.9rem; color: #aebbd0; line-height: 1.6; margin-bottom: 1rem; box-shadow: 0 4px 20px rgba(0,0,0,0.15);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.6rem;">
                <span>System Mode:</span> 
                <span style="background: rgba(89, 214, 154, 0.15); color: #59d69a; padding: 0.1rem 0.6rem; border-radius: 999px; font-weight: 600; font-size: 0.8rem; text-transform: uppercase;">{result.mode}</span>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem;">
                <span>Audit Backend:</span> 
                <span style="color: #fff; font-weight: 600;">{audit.backend}</span>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem;">
                <span>Data Source:</span> 
                <span style="color: #fff; font-weight: 600;">{zones[0].source.split(' ')[0]}</span>
            </div>
            <hr style="border: 0; height: 1px; background: rgba(255,255,255,0.1); margin: 1rem 0;">
            <div style="margin-bottom: 0.8rem;">
                <span style="display:block; font-size:0.75rem; color:#96a7bf; text-transform:uppercase; font-weight:600; letter-spacing:0.05em; margin-bottom:0.2rem;">Trace ID</span>
                <span style="color: #ffb45c; font-family: monospace; font-size:0.85rem; word-break: break-all; background: rgba(0,0,0,0.2); padding: 0.2rem 0.4rem; border-radius: 4px;">{zones[0].snapshot_id}</span>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem;">
                <div>
                    <span style="display:block; font-size:0.7rem; color:#96a7bf; text-transform:uppercase; font-weight:600;">Weather Time</span>
                    <span style="color: #fff; font-family: monospace; font-size:0.8rem;">{zones[0].weather_observed_at.strftime('%H:%M:%S')}</span>
                </div>
                <div>
                    <span style="display:block; font-size:0.7rem; color:#96a7bf; text-transform:uppercase; font-weight:600;">Ops Time</span>
                    <span style="color: #fff; font-family: monospace; font-size:0.8rem;">{zones[0].operations_observed_at.strftime('%H:%M:%S')}</span>
                </div>
            </div>
            <div style="margin-top: 0.5rem; text-align: center; font-size: 0.75rem; color: #6f809a;">
                Date: {zones[0].operations_observed_at.strftime('%Y-%m-%d UTC')}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )
    st.caption("Set HEATSAFE_MODE=cloud to require BigQuery or snapshot for fully offline demo.")
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()
