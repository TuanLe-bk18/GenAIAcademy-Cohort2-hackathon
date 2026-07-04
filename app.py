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
      .stApp { background: #0b1220; color: #e8eef8; }
      [data-testid="stHeader"] { background: transparent; }
      .block-container { padding-top: 1.5rem; max-width: 1500px; }
      .hero { padding: 1.2rem 1.4rem; border: 1px solid #26364f; border-radius: 18px;
              background: linear-gradient(120deg, #111d32, #191b28); margin-bottom: 1rem; }
      .hero h1 { margin: 0; color: #ff8a50; font-size: 2.2rem; }
      .hero p { margin: .35rem 0 0; color: #aebbd0; }
      .source-badge { display:inline-block; padding:.25rem .65rem; border-radius:999px;
                      background:#22324a; color:#bcd2ef; font-size:.78rem; margin-top:.7rem; }
      .panel { border: 1px solid #26364f; border-radius: 16px; padding: 1rem; background:#111a2a; }
      .risk-note { color:#96a7bf; font-size:.82rem; }
      div[data-testid="stMetric"] { background:#111a2a; border:1px solid #26364f;
                                    padding:.8rem 1rem; border-radius:14px; }
      .good { color:#59d69a; font-weight:700; }
      .warn { color:#ffb45c; font-weight:700; }
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


def format_vnd(value: int) -> str:
    return f"{value:,.0f} ₫".replace(",", ".")


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
    st.error("Không có dữ liệu vận hành.")
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
    st.info(f"Cloud data chưa sẵn sàng; đang dùng demo snapshot. Lý do: {result.fallback_reason}")
if result.freshness_warning:
    log_event("stale_live_snapshot", severity="WARNING", reason=result.freshness_warning)
    st.error(result.freshness_warning)
if any(zone.is_simulated for zone in zones):
    st.warning(
        "Demo scenario: một phần dữ liệu vận hành hoặc thời tiết là mô phỏng; "
        "xem source và timestamp trước khi phê duyệt."
    )
metric_cols = st.columns(4)
metric_cols[0].metric("Tài xế đang hoạt động", f"{active_drivers:,}")
metric_cols[1].metric("Hotspot nguy hiểm", danger_zones, delta=f"Top: {top_zone.name}", delta_color="off")
metric_cols[2].metric("Cohort ưu tiên", f"{priority_drivers:,}", delta="Hoạt động ≥2 giờ", delta_color="off")
metric_cols[3].metric("Đã mô phỏng", f"{protected_drivers:,}", delta="Decision audit", delta_color="off")

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
    st.caption("Heat Index là chỉ báo screening. Operational priority không phải chẩn đoán y khoa.")

with detail_col:
    st.subheader("Zone Decision Panel")
    zone_name = st.selectbox(
        "Khu vực",
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
    st.markdown(
        f"""
        <div class="panel">
          <div class="risk-note">OPERATIONAL PRIORITY</div>
          <h2 style="margin:.2rem 0;color:#ff9a62">{score}/100 · {priority_label(score)}</h2>
          <p><b>{selected.heat_index_c:.1f}°C</b> Heat Index · {TIER_LABELS[tier]}</p>
          <p>{selected.active_drivers} active · {selected.exposed_2h} exposed ≥2h · {selected.exposed_4h} exposed ≥4h</p>
          <p>{demand_forecast.predicted_requests if demand_forecast else 'Unavailable'} forecast requests / 30 min</p>
          <p>CoolStop: <b>{selected.coolstop_name}</b> <small>(illustrative)</small></p>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.divider()
st.subheader("SafePause Simulator")
st.caption(
    "Smart Pause + Earnings Guard + CoolStop Partner. "
    f"Demand source: {demand_forecast.source if demand_forecast else 'Unavailable'}. "
    "Tất cả impact bên dưới là scenario estimate."
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
    st.caption("Khoảng 90% áp dụng cho từng mốc 15 phút; không phải confidence interval của tổng 30 phút.")

control_cols = st.columns(4)
pause_minutes = control_cols[0].select_slider("Pause duration", options=[10, 15, 20, 25, 30], value=20)
waves = control_cols[1].select_slider("Staggered waves", options=[1, 2, 3, 4, 5], value=3)
budget_cap = control_cols[2].number_input("Platform cost cap (VND)", min_value=0, value=1_000_000, step=100_000)
sponsor_per_driver = control_cols[3].number_input("Partner contribution / driver", min_value=0, value=8_000, step=1_000)

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
        budget_cap_vnd=budget_cap,
        sponsor_per_driver_vnd=sponsor_per_driver,
    )
    st.session_state.proposal_key = proposal_key
proposal = st.session_state.proposal

impact_cols = st.columns(6)
impact_cols[0].metric("Eligible", proposal.eligible_drivers)
impact_cols[1].metric("Exposure avoided", f"{proposal.exposure_minutes_avoided:,} min")
impact_cols[2].metric("Trips reassigned", proposal.reassigned_trips)
impact_cols[3].metric("Missed trips", proposal.missed_trips)
impact_cols[4].metric("Net platform cost", format_vnd(proposal.net_platform_cost_vnd))
impact_cols[5].metric("Projected fulfillment", f"{proposal.projected_fulfillment_rate:.1%}")

cost_df = pd.DataFrame(
    [
        {"Component": "Earnings Guard", "VND": proposal.earnings_guard_cost_vnd},
        {"Component": "Lost platform contribution", "VND": proposal.lost_contribution_vnd},
        {"Component": "Partner sponsorship", "VND": -proposal.partner_sponsorship_vnd},
        {"Component": "Partner hydration value (in-kind)", "VND": proposal.partner_hydration_value_vnd},
    ]
)
cost_col, approval_col = st.columns([1.2, 1], gap="large")
with cost_col:
    st.bar_chart(cost_df.set_index("Component"), horizontal=True, color="#ff7a45")
with approval_col:
    guardrail_class = "good" if proposal.within_guardrails else "warn"
    st.markdown(
        f'<div class="panel"><div class="{guardrail_class}">{proposal.guardrail_notes[0]}</div>'
        f"<p>ETA impact: +{proposal.projected_eta_increase_minutes:.1f} phút · "
        f"Pause in {proposal.waves} wave(s), tối đa {proposal.planned_paused_driver_slots} tài xế/wave.</p></div>",
        unsafe_allow_html=True,
    )
    st.info("Demo only: thao tác này chỉ ghi một decision audit mô phỏng, không gửi lệnh tới tài xế.")
    confirm = st.checkbox("Tôi xác nhận đây là can thiệp mô phỏng")
    if st.button(
        "Record simulated intervention",
        type="primary",
        disabled=not (
            confirm
            and forecast_available
            and result.data_fresh
            and proposal.within_guardrails
            and proposal.eligible_drivers > 0
        ),
        width="stretch",
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
            f"Recorded simulation {event.intervention_id[:8]} · không có operational command được gửi."
        )
        st.cache_data.clear()
        st.rerun()

st.divider()
copilot_col, audit_col = st.columns([1.2, 1], gap="large")

with copilot_col:
    st.subheader("HeatSafe Copilot")
    st.caption("Chỉ gọi approved analytics tools; không sinh hoặc chạy SQL.")
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": "Hỏi tôi khu vực ưu tiên, nguyên nhân hoặc chi phí SafePause."}
        ]
    for message in st.session_state.messages[-6:]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("tool"):
                st.caption(f"Decision tool trace: {message['tool']}")
    if question := st.chat_input("Ví dụ: Khu vực nào cần can thiệp trước và chi phí bao nhiêu?"):
        st.session_state.messages.append({"role": "user", "content": question})
        with st.spinner("Đang phân tích snapshot..."):
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
        audit_df["net_platform_cost_vnd"] = audit_df["net_platform_cost_vnd"].map(format_vnd)
        st.dataframe(audit_df, width="stretch", hide_index=True)
    else:
        st.info("Chưa có intervention nào được phê duyệt.")

with st.sidebar:
    st.header("Demo controls")
    st.write(f"Mode: **{result.mode}**")
    st.write(f"Snapshot: `{zones[0].observed_at.isoformat()}`")
    st.write(f"Snapshot ID: `{zones[0].snapshot_id}`")
    st.write(f"Weather time: `{zones[0].weather_observed_at.isoformat()}`")
    st.write(f"Operations time: `{zones[0].operations_observed_at.isoformat()}`")
    st.write(f"Source: {zones[0].source}")
    st.write(f"Audit backend: **{audit.backend}**")
    st.caption("Set HEATSAFE_MODE=cloud to require BigQuery or snapshot for fully offline demo.")
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()
