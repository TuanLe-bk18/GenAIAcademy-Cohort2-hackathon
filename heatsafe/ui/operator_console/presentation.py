"""Browser-local, display-only operator playback.

This CCv2 surface advances a precomputed timeline in the browser and emits only the
current tick, branch, and selected zone back to Streamlit for replay-bound features.
Playback never reruns the simulator or optimizer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import streamlit as st

from .geography import load_hanoi_operator_districts
from .view_models import OperatorConsoleView

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TIMELINE = (
    ROOT
    / "data"
    / "scenarios"
    / "hanoi_heatwave_v1"
    / "operator_presentation_timeline.json"
)


@dataclass(frozen=True)
class OperatorDashboardResult:
    """Bounded intents and replay context emitted by the shared dashboard."""

    selected_zone_id: str | None = None
    decision_action: str | None = None
    replay_tick_index: int | None = None
    replay_branch: str | None = None


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def build_current_dashboard_payload(
    view: OperatorConsoleView,
    *,
    decision_available: bool,
    recording: bool,
    recorded_action: str | None,
) -> dict[str, Any]:
    """Adapt the live operator view to the same browser payload used by replay."""
    selected = view.selected_area
    zones = [
        {
            "id": area.zone_id,
            "name": area.name,
            "latitude": area.latitude,
            "longitude": area.longitude,
            "active_drivers": area.active_drivers,
            "heat_index_c": area.heat_index_c,
            "heat_state": area.heat_state_label,
            "urgent_drivers": area.drivers_needing_break_now,
            "exposed_2h": area.exposed_2h,
            "forecast_requests_30m": area.forecast_requests_30m,
            "needs_protection_120m": area.expected_needing_protection_count,
            "included": area.included_in_plan,
            "selected": area.selected,
            "plan_status": area.plan_status_label,
        }
        for area in view.map_areas
    ]
    decision_views: dict[str, Any] = {}
    if selected is not None:
        decision_views[selected.zone_id] = {
            "recommendation": _json_safe(view.recommendation),
            "insights": _json_safe(view.decision_insights),
        }
    urgent = sum(area.drivers_needing_break_now for area in view.map_areas)
    frame = {
        "tick": 0,
        "time": view.operational_time_label,
        "time_label": view.operational_time_label,
        "status": view.readiness_state,
        "branch": "CURRENT",
        "city": {
            "urgent_drivers": urgent,
            "at_risk_15m": view.city_kpis.at_risk_within_15m,
            "requests_15m": sum(
                area.forecast_requests_30m for area in view.map_areas
            ),
        },
        "zones": zones,
    }
    return {
        "schema_version": "operator-dashboard-v1",
        "mode": "current",
        "range_label": view.operational_time_label,
        "decision_time_label": view.operational_time_label,
        "plan_status": view.readiness_state,
        "synthetic_disclosure": view.synthetic_disclosure,
        "pre_decision": [frame],
        "branches": {"ACTIVATE": [], "CONTINUE": []},
        "decision_views": decision_views,
        "current_kpis": _json_safe(view.city_kpis.cards),
        "current_actions": {
            "available": decision_available,
            "recording": recording,
            "recorded_action": recorded_action,
        },
        "district_boundaries": load_hanoi_operator_districts(),
    }

_HTML = """
<main class="playback-root" aria-label="HeatSafe operator dashboard">
  <section class="playback-toolbar" aria-label="Playback controls">
    <div class="playback-clock">
      <span class="live-dot" aria-hidden="true"></span>
      <div>
        <div class="toolbar-kicker">EVENT REPLAY · <span data-range></span></div>
        <div class="toolbar-time">Now <strong data-time></strong> · Recommendation at <span data-decision-time></span></div>
      </div>
    </div>
    <div class="playback-actions">
      <button type="button" data-action="play"><span data-play-label>Play</span></button>
      <button type="button" data-action="next">Next 15 min</button>
      <button type="button" data-action="reset">Reset</button>
      <label>Speed
        <select data-speed aria-label="Playback speed">
          <option value="slow">Slow</option>
          <option value="normal" selected>Normal</option>
          <option value="fast">Fast</option>
        </select>
      </label>
    </div>
    <div class="progress-track" aria-hidden="true"><span data-progress></span></div>
  </section>

  <section class="action-strip" aria-label="SafePause decision">
    <button type="button" class="primary" data-choice="ACTIVATE">Activate SafePause</button>
    <button type="button" data-choice="CONTINUE">Continue Monitoring</button>
  </section>

  <section class="kpi-grid" aria-label="City status">
    <article class="kpi-card critical">
      <span data-kpi-label-0>Mandatory breaks now</span><strong data-kpi-urgent>0 drivers</strong><small data-kpi-note-0>Across Hanoi</small>
    </article>
    <article class="kpi-card coverage">
      <span data-kpi-label-1>Preventive risk</span><strong data-kpi-coverage>Not available</strong><small data-kpi-note-1>15-minute projection unavailable</small>
    </article>
    <article class="kpi-card budget">
      <span data-kpi-label-2>Active drivers</span><strong data-kpi-budget>—</strong><small data-kpi-note-2>online now</small>
    </article>
  </section>

  <section class="workspace-grid">
    <article class="panel map-panel">
      <header><div><span class="eyebrow">Live conditions</span><h2>Hanoi operating areas</h2></div><label class="map-layer-label">Map layer<select data-map-metric aria-label="Map layer"><option value="heat">Heat index</option><option value="urgent">Need a break now</option><option value="exposure">2h exposure</option><option value="demand">Forecast demand</option><option value="protection">Needs protection by 120m</option><option value="status">SafePause status</option></select></label></header>
      <div class="map-stage">
        <div class="city-map" data-city-map>
          <div class="city-basemap" data-map-basemap aria-hidden="true"></div>
          <svg data-map viewBox="0 0 720 360" role="img" aria-label="Hanoi city basemap with ten interactive operating districts">
            <defs>
              <filter id="map-glow"><feGaussianBlur stdDeviation="5" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
            </defs>
            <g data-map-zones></g>
          </svg>
          <div class="map-controls" aria-label="Map controls">
            <button type="button" class="map-all-districts" data-map-all aria-label="Show all districts">All Districts</button>
            <button type="button" data-map-zoom-in aria-label="Zoom in">+</button>
            <button type="button" data-map-zoom-out aria-label="Zoom out">−</button>
          </div>
          <span class="map-attribution">© OpenStreetMap · © CARTO</span>
        </div>
        <div class="map-legend"><span><i class="legend-hot"></i><span data-map-legend>Heat index</span></span><span><i class="legend-selected"></i>Selected / included</span></div>
      </div>
      <div class="map-selection-summary" aria-live="polite">
        <strong data-map-summary-title>Assessing current conditions</strong>
        <span data-map-summary-context>Recommendation is loading for this tick.</span>
      </div>
    </article>

    <article class="panel insight-panel">
      <header>
        <div><span class="eyebrow">Decision evidence</span><h2>Why this plan</h2></div>
        <div class="insight-controls">
          <label>View<select data-insight-view aria-label="Plan explanation"><option value="timing">Timing</option><option value="tradeoffs">Trade-offs</option><option value="stress">Stress test</option><option value="outcome">Outcome</option></select></label>
        </div>
      </header>
      <p class="insight-caption" data-insight-caption></p>
      <svg data-insight viewBox="0 0 640 390" role="img" aria-label="Decision explanation chart">
        <g data-insight-grid></g>
        <g data-insight-marks></g>
        <g data-insight-labels></g>
      </svg>
      <div class="insight-empty" data-insight-empty hidden>Supporting comparison evidence is not available yet.</div>
    </article>
  </section>

</main>
"""

_CSS = """
:host {
  color: var(--st-text-color, #f8fafc);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
button, select { font: inherit; }
.playback-root {
  --page:var(--st-background-color,#07110c);
  --surface:var(--st-secondary-background-color,#102018);
  --raised:var(--st-gray-background-color,#182a20);
  --border:var(--st-border-color,#2d4838);
  --muted:var(--st-gray-text-color,#9bafa2);
  --text:var(--st-text-color,#f2f7f3);
  --primary:var(--st-primary-color,#43b66e);
  --primary-bg:var(--st-green-background-color,rgba(67,182,110,.12));
  --safe:var(--st-green-text-color,#43b66e);
  --safe-bg:var(--st-green-background-color,rgba(67,182,110,.12));
  --heat:var(--st-orange-color,#e67e32);
  --heat-bg:var(--st-orange-background-color,rgba(230,126,50,.12));
  --warning:var(--st-yellow-text-color,#c69700);
  --critical:var(--st-red-color,#d94a3a);
  --critical-text:var(--st-red-text-color,#d94a3a);
  --critical-bg:var(--st-red-background-color,rgba(217,74,58,.12));
  --context:var(--st-blue-color,#2786a6);
  --context-text:var(--st-blue-text-color,#2786a6);
  --context-bg:var(--st-blue-background-color,rgba(39,134,166,.12));
  --map-canvas:var(--page);
  --map-grid:var(--border);
  --map-river-bed:var(--context-bg);
  --map-river:var(--context);
  color:var(--text);
  display:grid;
  gap:12px;
  padding:1px 0 12px;
}
.playback-root[data-mode="current"] .playback-toolbar { display:none; }
.playback-toolbar { position:relative; display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:72px; padding:12px 16px 16px; border:1px solid var(--border); border-radius:13px; background:var(--surface); overflow:hidden; }
.playback-clock { display:flex; align-items:center; gap:12px; }
.live-dot { width:10px; height:10px; border-radius:50%; background:var(--primary); box-shadow:0 0 0 5px var(--primary-bg); }
.toolbar-kicker { color:var(--text); font-weight:750; font-size:14px; }
.toolbar-time { color:var(--muted); font-size:13px; margin-top:3px; }
.toolbar-time strong { color:var(--safe); font-size:17px; font-variant-numeric:tabular-nums; }
.playback-actions { display:flex; align-items:center; flex-wrap:wrap; justify-content:flex-end; gap:8px; }
.playback-actions button, .action-strip button { border:1px solid var(--border); border-radius:8px; background:var(--surface); color:var(--text); cursor:pointer; min-height:36px; padding:7px 12px; transition:background-color .18s ease, border-color .18s ease, opacity .18s ease; }
.playback-actions button:hover, .action-strip button:hover { background:var(--raised); border-color:var(--primary); }
.playback-actions button:focus-visible, .action-strip button:focus-visible, select:focus-visible { outline:2px solid var(--primary); outline-offset:2px; }
.playback-actions button:disabled, .action-strip button:disabled { opacity:.42; cursor:not-allowed; }
.playback-actions label { display:flex; align-items:center; gap:6px; color:var(--muted); font-size:12px; }
.playback-actions select { border:1px solid var(--border); border-radius:8px; background:var(--surface); color:var(--text); padding:7px 8px; cursor:pointer; }
.progress-track { position:absolute; height:4px; background:var(--raised); left:0; right:0; bottom:0; }
.progress-track span { display:block; height:100%; width:0; background:var(--primary); transition:width .42s cubic-bezier(.2,.8,.2,1); }
.action-strip{display:grid;grid-template-columns:1fr 1fr;gap:8px}.action-strip button{width:100%;color:var(--warning);font-weight:700}.action-strip .primary{color:var(--warning);background:var(--primary);border-color:var(--primary)}.action-strip .primary:hover{background:var(--safe)}.action-strip button.selected{border-color:var(--safe);box-shadow:inset 0 0 0 1px var(--safe)}
.kpi-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
.kpi-card { min-height:104px; padding:13px 15px; border:1px solid var(--border); border-radius:12px; background:var(--surface); display:flex; flex-direction:column; justify-content:center; overflow:hidden; position:relative; }
.kpi-card::before { content:""; position:absolute; inset:0 auto 0 0; width:4px; background:var(--primary); }
.kpi-card.critical::before { background:var(--critical); }.kpi-card.budget::before { background:var(--heat); }
.kpi-card span,.kpi-card small { color:var(--muted); font-size:12px; }.kpi-card strong { margin:5px 0 2px; font-size:25px; letter-spacing:-.03em; font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.workspace-grid { display:grid; grid-template-columns:minmax(0,1.08fr) minmax(390px,1fr); gap:12px; align-items:stretch; }
.panel { border:1px solid var(--border); border-radius:13px; background:var(--surface); overflow:hidden; }
.panel > header { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; padding:14px 16px 10px; }
.panel h2 { font-size:17px; line-height:1.25; margin:2px 0 0; }.eyebrow { color:var(--muted); font-size:10px; letter-spacing:.11em; text-transform:uppercase; font-weight:750; }
.map-panel header > span { color:var(--muted); font-size:12px; }.map-layer-label { display:flex; align-items:center; gap:6px; color:var(--muted); font-size:11px; white-space:nowrap; }.map-layer-label select { min-height:30px; border:1px solid var(--border); border-radius:7px; padding:4px 7px; background:var(--surface); color:var(--text); }
.map-stage { padding:0 12px; position:relative; }.city-map{position:relative;width:100%;height:315px;overflow:hidden;border:1px solid var(--border);border-radius:11px;background:var(--map-canvas)}.city-basemap,.city-map svg{position:absolute;inset:0;width:100%;height:100%}.city-basemap{overflow:hidden;background:var(--map-canvas)}.city-basemap img{position:absolute;width:256px;height:256px;max-width:none;user-select:none;pointer-events:none}.city-map svg{display:block;z-index:1}.map-controls{position:absolute;z-index:3;top:10px;right:10px;display:flex;border:1px solid rgba(255,255,255,.62);border-radius:7px;overflow:hidden;box-shadow:0 2px 7px rgba(0,0,0,.28)}.map-controls button{min-width:32px;height:31px;border:0;border-right:1px solid #c7d0cc;background:rgba(255,255,255,.94);color:#15201d;font-size:20px;line-height:1;cursor:pointer}.map-controls button:last-child{border-right:0}.map-controls .map-all-districts{width:auto;padding:0 10px;font-size:11px;font-weight:750}.map-controls .map-all-districts.active{background:#087f92;color:#fff}.map-controls button:hover{background:#fff;color:#087f92}.map-controls button:disabled{opacity:.48;cursor:not-allowed}.map-attribution{position:absolute;z-index:2;right:6px;bottom:4px;padding:1px 4px;border-radius:3px;background:rgba(255,255,255,.82);color:#33443e;font-size:8px}.map-zone { cursor:pointer; }.map-zone path { transition:fill .32s ease, stroke .24s ease, stroke-width .24s ease, opacity .24s ease;vector-effect:non-scaling-stroke}.map-zone:hover path,.map-zone:focus-visible path{stroke:#087f92!important;stroke-width:4.5px!important;filter:url(#map-glow)}.map-zone text { fill:#17211e; font-size:10px; font-weight:750; pointer-events:none; paint-order:stroke; stroke:rgba(255,255,255,.92); stroke-width:3px; stroke-linejoin:round; }
.map-legend,.trend-legend { display:flex; flex-wrap:wrap; gap:12px; color:var(--muted); font-size:10px; }.map-legend { position:absolute; left:24px; bottom:10px; padding:5px 8px; border:1px solid var(--border); border-radius:7px; background:var(--surface); }
.map-legend i,.trend-legend i { display:inline-block; width:9px; height:9px; margin-right:5px; border-radius:50%; vertical-align:-1px; }.legend-hot{background:var(--heat)}.legend-selected{border:2px solid var(--primary)}.legend-urgent{background:var(--heat)}.legend-demand{background:var(--context)}
.map-selection-summary{margin:10px 12px 12px;padding:11px 13px;border:1px solid var(--border);border-left:3px solid var(--primary);border-radius:9px;background:var(--primary-bg)}.map-selection-summary strong{display:block;font-size:14px;line-height:1.3}.map-selection-summary span{display:block;margin-top:4px;color:var(--muted);font-size:11px;line-height:1.4}
.insight-controls{display:flex;align-items:flex-end;flex-wrap:wrap;justify-content:flex-end;gap:7px}.insight-controls label{display:flex;flex-direction:column;gap:3px;color:var(--muted);font-size:10px}.insight-controls select{min-height:30px;max-width:145px;border:1px solid var(--border);border-radius:7px;padding:4px 7px;background:var(--surface);color:var(--text)}.insight-caption{min-height:18px;margin:0;padding:0 16px 4px;color:var(--muted);font-size:11px}.insight-panel svg{display:block;width:100%;height:365px;padding:0 8px 8px}.insight-empty{margin:20px 16px;padding:14px;border:1px solid var(--border);border-radius:9px;color:var(--muted);background:var(--raised)}.insight-grid{stroke:var(--border);stroke-width:1}.insight-axis{fill:var(--muted);font-size:10px}.insight-title{fill:var(--text);font-size:11px;font-weight:700}.insight-value{fill:var(--text);font-size:10px;font-weight:700}.insight-expected{fill:none;stroke:var(--context);stroke-width:3;stroke-linejoin:round;stroke-linecap:round}.insight-high{fill:none;stroke:var(--heat);stroke-width:2;stroke-dasharray:5 4;stroke-linejoin:round;stroke-linecap:round}.insight-selected{fill:var(--context)}.insight-included{fill:var(--heat)}.insight-neutral{fill:var(--muted);opacity:.72}.insight-limit{stroke:var(--critical);stroke-width:2;stroke-dasharray:5 4}.insight-point{stroke:var(--surface);stroke-width:2}
.trend-panel > header { align-items:center; }.trend-panel svg { display:block; width:100%; height:205px; padding:0 8px 8px; }.chart-grid line { stroke:var(--border); stroke-width:1; }.urgent-line,.demand-line { fill:none; stroke-linejoin:round; stroke-linecap:round; }.urgent-line{stroke:var(--heat);stroke-width:3}.demand-line{stroke:var(--context);stroke-width:2;stroke-dasharray:5 4}.chart-cursor{stroke:var(--muted);stroke-width:1;stroke-dasharray:3 4;opacity:.72;transition:x1 .42s cubic-bezier(.2,.8,.2,1),x2 .42s cubic-bezier(.2,.8,.2,1)}.urgent-dot{fill:var(--heat);transition:cx .42s cubic-bezier(.2,.8,.2,1),cy .42s cubic-bezier(.2,.8,.2,1)}.demand-dot{fill:var(--context);transition:cx .42s cubic-bezier(.2,.8,.2,1),cy .42s cubic-bezier(.2,.8,.2,1)}.chart-value{fill:var(--heat);font-size:11px;font-weight:700}.demand-value{fill:var(--context)}[data-chart-reveal]{transition:clip-path .42s cubic-bezier(.2,.8,.2,1)}[data-trend] > text{fill:var(--muted);font-size:10px}
[hidden]{display:none!important}
@media(max-width:1100px){.playback-toolbar{align-items:flex-start;flex-direction:column}.playback-actions{justify-content:flex-start}.workspace-grid{grid-template-columns:1fr}.map-stage svg{height:300px}.kpi-card strong{font-size:21px}}
@media(max-width:680px){.kpi-grid{grid-template-columns:1fr}.playback-actions{display:grid;grid-template-columns:1fr 1fr;width:100%}.playback-actions label{grid-column:span 2}.insight-controls{justify-content:flex-start}.panel>header{flex-direction:column}.insight-panel svg{height:330px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;transition-duration:.01ms!important;scroll-behavior:auto!important}}
"""

_JS = """
const INSTANCES = new WeakMap()
const NS = "http://www.w3.org/2000/svg"
const SPEEDS = { slow: 2600, normal: 1600, fast: 900 }

function q(root, selector) { return root.querySelector(selector) }
function token(root, name, fallback) {
  const themed = q(root, ".playback-root") || root
  return getComputedStyle(themed).getPropertyValue(name).trim() || fallback
}
function heatColor(root, value) {
  if (value >= 52) return token(root, "--critical-text", "#b42318")
  if (value >= 39) return token(root, "--critical", "#d94a3a")
  if (value >= 32) return token(root, "--heat", "#e67e32")
  if (value >= 27) return token(root, "--warning", "#c69700")
  return token(root, "--muted", "#728079")
}
function setText(root, selector, value) {
  const node = q(root, selector)
  if (node) node.textContent = value == null ? "—" : String(value)
}
function isCurrent(state) { return state.data.mode === "current" }
function selectZone(state, zoneId) {
  state.selectedZone = zoneId
  render(state)
  if (isCurrent(state)) state.setTriggerValue?.("selected_zone_id", zoneId)
  else emitReplayState(state)
}
function selectAllDistricts(state) {
  state.selectedZone = null
  render(state)
}
function tweenNumber(node, next) {
  if (!node) return
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches
  const previous = Number(node.dataset.value || next)
  node.dataset.value = String(next)
  if (reduced || previous === next) { node.textContent = Number(next).toLocaleString(); return }
  const started = performance.now()
  const run = (now) => {
    const progress = Math.min(1, (now - started) / 360)
    const eased = 1 - Math.pow(1 - progress, 3)
    node.textContent = Math.round(previous + (next - previous) * eased).toLocaleString()
    if (progress < 1 && Number(node.dataset.value) === Number(next)) requestAnimationFrame(run)
  }
  requestAnimationFrame(run)
}
function sequence(state) {
  const pre = state.data.pre_decision || []
  const branch = state.choice ? (state.data.branches?.[state.choice] || []) : []
  return pre.concat(branch)
}
function displayFrames(state) {
  const pre = state.data.pre_decision || []
  const branchName = state.choice || "CONTINUE"
  return pre.concat(state.data.branches?.[branchName] || [])
}
function currentFrame(state) {
  const frames = displayFrames(state)
  return frames[Math.min(state.index, Math.max(0, frames.length - 1))]
}
function emitReplayState(state) {
  if (isCurrent(state)) return
  const frame = currentFrame(state)
  if (!frame || !state.selectedZone) return
  const value = {
    tick: Number(frame.tick),
    selected_zone_id: state.selectedZone,
    branch: state.choice || frame.branch || "PRE_DECISION",
  }
  const signature = JSON.stringify(value)
  if (signature === state.lastEmittedReplayState) return
  state.lastEmittedReplayState = signature
  state.setStateValue?.("replay_state", value)
}
function stop(state) {
  if (state.timer) clearInterval(state.timer)
  state.timer = null
  state.running = false
}
function canAdvance(state) {
  const preEnd = (state.data.pre_decision || []).length - 1
  if (state.index === preEnd && !state.choice) return false
  return state.index < sequence(state).length - 1
}
function step(state) {
  if (!canAdvance(state)) { stop(state); render(state); return }
  state.index += 1
  render(state)
  emitReplayState(state)
  if (!canAdvance(state)) { stop(state); render(state) }
}
function start(state) {
  if (!canAdvance(state)) return
  stop(state)
  state.running = true
  state.timer = setInterval(() => step(state), SPEEDS[state.speed] || SPEEDS.normal)
  render(state)
}
function selectedZone(state, frame) {
  const zones = frame?.zones || []
  return zones.find((item) => item.id === state.selectedZone) || null
}
function preventiveRiskAtTick(state, frame) {
  if (isCurrent(state)) {
    const value = frame.city?.at_risk_15m
    return value == null || !Number.isFinite(Number(value)) ? null : Number(value)
  }
  const event = (state.data.rolling_events || []).find(
    (item) => Number(item.tick) === Number(frame.tick)
  )
  const value = event?.new_preventive_count
  return value == null || !Number.isFinite(Number(value)) ? null : Number(value)
}
function citywideRecommendation(state, frame) {
  const mandatory = Math.max(0, Number(frame.city?.urgent_drivers || 0))
  const atRisk = preventiveRiskAtTick(state, frame)
  const recorded = isCurrent(state)
    ? state.data.current_actions?.recorded_action
    : state.choice
  if (recorded === "ACTIVATE") {
    if (mandatory > 0) {
      return {
        title: "Update SafePause coverage",
        copy: atRisk == null
          ? `${mandatory.toLocaleString()} drivers still require a mandatory break now.`
          : `${mandatory.toLocaleString()} drivers require a mandatory break now; ${atRisk.toLocaleString()} more are at risk within 15 minutes.`,
      }
    }
    if (atRisk != null && atRisk > 0) {
      return {
        title: "SafePause activated",
        copy: `${atRisk.toLocaleString()} at-risk drivers are included in the next preventive wave.`,
      }
    }
    return {
      title: "SafePause coverage is holding",
      copy: "No additional mandatory or preventive cohort is verified for this tick.",
    }
  }
  if (mandatory > 0) {
    return {
      title: "Start mandatory breaks now",
      copy: atRisk == null || atRisk === 0
        ? `${mandatory.toLocaleString()} drivers require an immediate mandatory break.`
        : `${mandatory.toLocaleString()} drivers require a mandatory break now; ${atRisk.toLocaleString()} more are at risk within 15 minutes.`,
    }
  }
  if (atRisk != null && atRisk > 0) {
    return {
      title: "Activate SafePause now",
      copy: `${atRisk.toLocaleString()} drivers are projected to require a mandatory break within 15 minutes.`,
    }
  }
  return {
    title: "Continue monitoring",
    copy: atRisk == null
      ? "No verified preventive recommendation is available for this tick."
      : "No driver currently requires a mandatory or preventive break.",
  }
}
function worldPoint(longitude, latitude, zoom) {
  const size = 256 * Math.pow(2, zoom)
  const boundedLatitude = Math.max(-85.0511, Math.min(85.0511, Number(latitude)))
  const sin = Math.sin(boundedLatitude * Math.PI / 180)
  return [
    ((Number(longitude) + 180) / 360) * size,
    (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * size,
  ]
}
function mapProjection(state) {
  const zoom = state.mapZoom || 11
  const viewport = q(state.root, "[data-city-map]")
  const width = Math.max(320, Math.round(viewport?.clientWidth || 720))
  const height = Math.max(240, Math.round(viewport?.clientHeight || 315))
  const center = worldPoint(105.81, 21.01, zoom)
  return { zoom, centerX:center[0], centerY:center[1], width, height }
}
function projectedPoint(longitude, latitude, projection) {
  const point = worldPoint(longitude, latitude, projection.zoom)
  return [
    point[0] - projection.centerX + projection.width / 2,
    point[1] - projection.centerY + projection.height / 2,
  ]
}
function mapPoint(zone, projection) {
  return projectedPoint(zone.longitude, zone.latitude, projection)
}
function pathForGeometry(geometry, projection) {
  const rings = geometry?.type === "Polygon" ? geometry.coordinates : geometry?.type === "MultiPolygon" ? geometry.coordinates.flat() : []
  return rings.map((ring) => ring.map((point, index) => {
    const [x, y] = projectedPoint(point[0], point[1], projection)
    return `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(" ") + " Z").join(" ")
}
function isDarkBasemap(root) {
  const color = getComputedStyle(q(root, "[data-city-map]")).backgroundColor
  const values = color.match(/[\\d.]+/g)?.slice(0, 3).map(Number)
  if (!values || values.length < 3) return true
  return values[0] * .299 + values[1] * .587 + values[2] * .114 < 145
}
function renderBasemap(state, projection) {
  const layer = q(state.root, "[data-map-basemap]")
  const variant = isDarkBasemap(state.root) ? "dark_all" : "light_all"
  const startX = Math.floor((projection.centerX - projection.width / 2) / 256)
  const endX = Math.floor((projection.centerX + projection.width / 2) / 256)
  const startY = Math.floor((projection.centerY - projection.height / 2) / 256)
  const endY = Math.floor((projection.centerY + projection.height / 2) / 256)
  const key = `${variant}:${projection.zoom}:${startX}:${endX}:${startY}:${endY}`
  if (layer.dataset.tiles === key) return
  layer.dataset.tiles = key
  layer.replaceChildren()
  const tileCount = Math.pow(2, projection.zoom)
  for (let tileY = startY; tileY <= endY; tileY += 1) {
    if (tileY < 0 || tileY >= tileCount) continue
    for (let tileX = startX; tileX <= endX; tileX += 1) {
      const wrappedX = ((tileX % tileCount) + tileCount) % tileCount
      const tile = state.root.ownerDocument.createElement("img")
      tile.alt = ""
      tile.decoding = "async"
      tile.src = `https://a.basemaps.cartocdn.com/${variant}/${projection.zoom}/${wrappedX}/${tileY}@2x.png`
      tile.style.left = `${tileX * 256 - projection.centerX + projection.width / 2}px`
      tile.style.top = `${tileY * 256 - projection.centerY + projection.height / 2}px`
      layer.appendChild(tile)
    }
  }
}
function mapMetric(zone, metric) {
  if (metric === "urgent") return [Number(zone.urgent_drivers || 0), `${Number(zone.urgent_drivers || 0).toLocaleString()} need a break now`, "Drivers needing a break"]
  if (metric === "exposure") {
    const value = zone.exposed_2h
    return [value == null ? null : Number(value), value == null ? "Updating" : `${Number(value).toLocaleString()} with 2h exposure`, "Drivers with 2h exposure"]
  }
  if (metric === "demand") {
    const value = zone.forecast_requests_30m ?? zone.requests_15m
    const unit = zone.forecast_requests_30m == null ? "15 min" : "30 min"
    return [value == null ? null : Number(value), value == null ? "Updating" : `${Number(value).toLocaleString()} requests / ${unit}`, "Forecast demand"]
  }
  if (metric === "protection") {
    const value = zone.needs_protection_120m
    return [value == null ? null : Number(value), value == null ? "Updating" : `${Number(value).toLocaleString()} need protection by 120m`, "Needs protection by 120m"]
  }
  if (metric === "status") return [zone.included ? 2 : 1, zone.included ? "Included in the SafePause plan" : "Monitoring only", "SafePause status"]
  return [Number(zone.heat_index_c || 0), `${Number(zone.heat_index_c || 0).toFixed(1)}°C`, "Heat index"]
}
function mapColor(root, zone, zones, metric) {
  if (metric === "heat") return heatColor(root, Number(zone.heat_index_c))
  if (metric === "status") return zone.included ? token(root, "--heat", "#f0a35a") : token(root, "--context", "#4da7b3")
  const values = zones.map((item) => mapMetric(item, metric)[0]).filter((value) => value != null)
  if (!values.length || mapMetric(zone, metric)[0] == null) return token(root, "--muted", "#728079")
  const min = Math.min(...values), max = Math.max(...values)
  const ratio = (mapMetric(zone, metric)[0] - min) / Math.max(.001, max - min)
  const start = ratio < .5 ? [77,167,179] : [240,163,90]
  const end = ratio < .5 ? [240,163,90] : [239,106,91]
  const mix = ratio < .5 ? ratio * 2 : (ratio - .5) * 2
  return `rgb(${start.map((value, index) => Math.round(value + (end[index] - value) * mix)).join(",")})`
}
function renderMap(state, frame) {
  const root = state.root
  const layer = q(root, "[data-map-zones]")
  const zones = frame.zones || []
  const metric = state.mapMetric || "heat"
  const metricSelect = q(root, "[data-map-metric]")
  const availability = {
    exposure: zones.some((zone) => zone.exposed_2h != null),
    protection: zones.some((zone) => zone.needs_protection_120m != null),
  }
  Array.from(metricSelect.options).forEach((option) => {
    if (option.value in availability) option.disabled = !availability[option.value]
  })
  const boundaries = new Map((state.data.district_boundaries?.features || []).map((feature) => [feature.properties?.zone_id, feature]))
  const projection = mapProjection(state)
  q(root, "[data-map]").setAttribute("viewBox", `0 0 ${projection.width} ${projection.height}`)
  renderBasemap(state, projection)
  q(root, "[data-map-zoom-in]").disabled = projection.zoom >= 12
  q(root, "[data-map-zoom-out]").disabled = projection.zoom <= 10
  q(root, "[data-map-all]").classList.toggle("active", !state.selectedZone)
  const ids = new Set(zones.map((item) => item.id))
  layer.querySelectorAll(".map-zone").forEach((node) => { if (!ids.has(node.dataset.id)) node.remove() })
  zones.forEach((zone) => {
    let group = layer.querySelector(`[data-id="${zone.id}"]`)
    if (!group) {
      group = document.createElementNS(NS, "g")
      group.setAttribute("class", "map-zone")
      group.dataset.id = zone.id
      const path = document.createElementNS(NS, "path")
      const label = document.createElementNS(NS, "text")
      label.setAttribute("text-anchor", "middle")
      group.append(path, label)
      group.setAttribute("tabindex", "0")
      group.setAttribute("role", "button")
      group.onclick = () => selectZone(state, zone.id)
      group.onkeydown = (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectZone(state, zone.id) }
      }
      layer.appendChild(group)
    }
    const [x, y] = mapPoint(zone, projection)
    const path = group.querySelector("path")
    const label = group.querySelector("text")
    const boundary = boundaries.get(zone.id)
    const selected = state.selectedZone === zone.id
    const metricValue = mapMetric(zone, metric)
    group.setAttribute("aria-label", `${zone.name}, ${metricValue[1]}`)
    path.setAttribute("d", boundary ? pathForGeometry(boundary.geometry, projection) : `M${x - 8},${y} a8,8 0 1,0 16,0 a8,8 0 1,0 -16,0`)
    path.setAttribute("fill", mapColor(root, zone, zones, metric))
    path.setAttribute("fill-opacity", selected ? ".95" : ".72")
    path.setAttribute("stroke", selected ? token(root, "--context", "#4da7b3") : zone.included ? token(root, "--heat", "#f0a35a") : token(root, "--muted", "#728079"))
    path.setAttribute("stroke-width", selected ? "3.5" : zone.included ? "2.5" : "1")
    if (selected) path.setAttribute("filter", "url(#map-glow)"); else path.removeAttribute("filter")
    label.setAttribute("x", x); label.setAttribute("y", y)
    label.textContent = zone.name
  })
  const selected = zones.find((zone) => zone.id === state.selectedZone)
  const districtRecommendation = state.data.decision_views?.[selected?.id]?.recommendation
  if (selected) {
    const canActivate = Boolean(districtRecommendation?.can_activate)
    setText(root, "[data-map-summary-title]", canActivate ? `Activate SafePause in ${selected.name}` : `Continue monitoring ${selected.name}`)
    setText(root, "[data-map-summary-context]", districtRecommendation
      ? canActivate
        ? `${Number(districtRecommendation.driver_count || 0).toLocaleString()} drivers · ${districtRecommendation.start_time_label} start · ${districtRecommendation.break_length_label}.`
        : districtRecommendation.explanation
      : "District recommendation is loading.")
  } else {
    const recommendation = citywideRecommendation(state, frame)
    setText(root, "[data-map-summary-title]", recommendation.title)
    setText(root, "[data-map-summary-context]", recommendation.copy)
  }
  setText(root, "[data-map-summary]", `${zones.length} operating areas · 15-minute conditions`)
  setText(root, "[data-map-legend]", mapMetric(zones[0] || {}, metric)[2])
}
function svgElement(tag, attributes={}, textValue=null) {
  const node = document.createElementNS(NS, tag)
  Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, String(value)))
  if (textValue != null) node.textContent = String(textValue)
  return node
}
function insightLayers(root) {
  const grid = q(root, "[data-insight-grid]")
  const marks = q(root, "[data-insight-marks]")
  const labels = q(root, "[data-insight-labels]")
  grid.replaceChildren(); marks.replaceChildren(); labels.replaceChildren()
  return { grid, marks, labels }
}
function chartLine(values, x0, x1, y0, y1) {
  const numeric = values.map((value) => Number(value || 0))
  const low = Math.min(0, ...numeric), high = Math.max(1, ...numeric)
  return numeric.map((value, index) => {
    const x = x0 + (index / Math.max(1, numeric.length - 1)) * (x1 - x0)
    const y = y1 - ((value - low) / Math.max(1, high - low)) * (y1 - y0)
    return [x, y]
  })
}
function path(pointsValue) {
  return pointsValue.map(([x,y], index) => `${index ? "L" : "M"} ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ")
}
function addGrid(layers, yValues) {
  yValues.forEach((y) => layers.grid.appendChild(svgElement("line", {x1:52,y1:y,x2:620,y2:y,class:"insight-grid"})))
}
function renderAllDistrictsInsight(state, frame, layers) {
  const zones = frame.zones || []
  if (!zones.length) return false
  const width = 540 / Math.max(1, zones.length), barWidth = Math.min(34, width * .58)
  const series = [
    { title:"Drivers needing a break now", values:zones.map((zone) => Number(zone.urgent_drivers || 0)), top:52, bottom:174 },
    { title:"Forecast demand", values:zones.map((zone) => Number(zone.forecast_requests_30m ?? zone.requests_15m ?? 0)), top:222, bottom:344 },
  ]
  addGrid(layers, [52,113,174,222,283,344])
  series.forEach((seriesItem) => {
    const max = Math.max(1, ...seriesItem.values)
    layers.labels.appendChild(svgElement("text", {x:52,y:seriesItem.top-13,class:"insight-title"}, seriesItem.title))
    seriesItem.values.forEach((value, index) => {
      const zone = zones[index], x = 60 + index * width + (width - barWidth) / 2
      const height = (value / max) * (seriesItem.bottom - seriesItem.top)
      const className = state.selectedZone === zone.id ? "insight-selected" : zone.included ? "insight-included" : "insight-neutral"
      layers.marks.appendChild(svgElement("rect", {x,y:seriesItem.bottom-height,width:barWidth,height,class:className,rx:3}))
      layers.labels.appendChild(svgElement("text", {x:x+barWidth/2,y:seriesItem.bottom-height-5,"text-anchor":"middle",class:"insight-value"}, value.toLocaleString()))
    })
  })
  zones.forEach((zone, index) => {
    const x = 60 + index * width + width / 2
    layers.labels.appendChild(svgElement("text", {x,y:365,"text-anchor":"end",transform:`rotate(-34 ${x} 365)`,class:"insight-axis"}, zone.name))
  })
  return true
}
function renderTimingInsight(insights, layers) {
  const options = insights?.timing_options || []
  if (!options.length) return false
  addGrid(layers, [52,105,158,220,276,332])
  layers.labels.appendChild(svgElement("text", {x:52,y:35,class:"insight-title"}, "Demand around each start option"))
  layers.labels.appendChild(svgElement("text", {x:52,y:203,class:"insight-title"}, "Projected drivers at the safety limit"))
  const expected = chartLine(options.map((item) => item.expected_demand), 62, 606, 52, 158)
  const high = chartLine(options.map((item) => item.high_demand), 62, 606, 52, 158)
  layers.marks.appendChild(svgElement("path", {d:path(expected),class:"insight-expected"}))
  layers.marks.appendChild(svgElement("path", {d:path(high),class:"insight-high"}))
  expected.forEach(([x,y], index) => {
    layers.marks.appendChild(svgElement("circle", {cx:x,cy:y,r:4,class:"insight-selected insight-point"}))
    layers.labels.appendChild(svgElement("text", {x,y:y-9,"text-anchor":"middle",class:"insight-value"}, Number(options[index].expected_demand || 0).toLocaleString()))
  })
  const values = options.map((item) => Number(item.projected_drivers_at_limit ?? item.drivers_protected ?? 0))
  const max = Math.max(1, ...values), width = 500 / Math.max(1, values.length)
  values.forEach((value, index) => {
    const item = options[index], x = 76 + index * width, height = (value / max) * 106
    layers.marks.appendChild(svgElement("rect", {x,y:332-height,width:Math.min(58,width*.55),height,rx:3,class:item.selected?"insight-included":item.feasible?"insight-selected":"insight-neutral"}))
    layers.labels.appendChild(svgElement("text", {x:x+Math.min(58,width*.55)/2,y:350,"text-anchor":"middle",class:"insight-axis"}, item.start_time_label))
    layers.labels.appendChild(svgElement("text", {x:x+Math.min(58,width*.55)/2,y:332-height-5,"text-anchor":"middle",class:"insight-value"}, Math.round(value).toLocaleString()))
  })
  return true
}
function renderTradeoffInsight(insights, layers) {
  const options = insights?.portfolio_options || []
  if (!options.length) return false
  addGrid(layers, [65,130,195,260,325])
  layers.labels.appendChild(svgElement("text", {x:52,y:34,class:"insight-title"}, "Cost versus heat exposure avoided"))
  const maxX=Math.max(1,...options.map((item)=>Number(item.high_demand_cost_usd||0)))
  const maxY=Math.max(1,...options.map((item)=>Number(item.exposure_hours_avoided||0)))
  options.forEach((item) => {
    const x=62+(Number(item.high_demand_cost_usd||0)/maxX)*540
    const y=325-(Number(item.exposure_hours_avoided||0)/maxY)*260
    const className=item.selected?"insight-included":item.feasible?"insight-selected":"insight-neutral"
    layers.marks.appendChild(svgElement("circle",{cx:x,cy:y,r:item.selected?10:Math.max(4,Math.min(8,4+Number(item.protected_drivers||0)/70)),class:`${className} insight-point`}))
    if(item.selected) layers.labels.appendChild(svgElement("text",{x,y:y-15,"text-anchor":"middle",class:"insight-value"},"Selected plan"))
  })
  const limit=Number(insights.budget_limit_usd||0)
  if(limit>0){const x=62+(limit/maxX)*540;layers.marks.appendChild(svgElement("line",{x1:x,y1:52,x2:x,y2:332,class:"insight-limit"}));layers.labels.appendChild(svgElement("text",{x:x-4,y:48,"text-anchor":"end",class:"insight-axis"},"Budget limit"))}
  layers.labels.appendChild(svgElement("text",{x:332,y:370,"text-anchor":"middle",class:"insight-axis"},"Estimated high-demand cost →"))
  return true
}
function renderStressInsight(insights, layers) {
  const metrics=insights?.stress_metrics||[]
  if(!metrics.length)return false
  layers.labels.appendChild(svgElement("text",{x:52,y:34,class:"insight-title"},"High-demand case as a share of each operating limit"))
  metrics.forEach((item,index)=>{
    const y=72+index*75,limit=Number(item.limit_value||1),expected=Math.min(1.3,Number(item.expected_value||0)/limit),high=Math.min(1.3,Number(item.high_demand_value||0)/limit)
    layers.labels.appendChild(svgElement("text",{x:52,y:y-9,class:"insight-axis"},item.label))
    layers.marks.appendChild(svgElement("rect",{x:52,y,width:Math.max(2,expected*400),height:16,rx:3,class:"insight-selected"}))
    layers.marks.appendChild(svgElement("rect",{x:52,y:y+20,width:Math.max(2,high*400),height:16,rx:3,class:item.passed?"insight-included":"insight-neutral"}))
    layers.labels.appendChild(svgElement("text",{x:462,y:y+13,class:"insight-value"},item.expected_label))
    layers.labels.appendChild(svgElement("text",{x:462,y:y+33,class:"insight-value"},`${item.high_demand_label} · ${item.passed?"Pass":"Blocked"}`))
  })
  layers.marks.appendChild(svgElement("line",{x1:452,y1:46,x2:452,y2:365,class:"insight-limit"}))
  layers.labels.appendChild(svgElement("text",{x:452,y:382,"text-anchor":"middle",class:"insight-axis"},"100% limit"))
  return true
}
function renderOutcomeInsight(state, insights, layers) {
  let withValues=[],withoutValues=[],labels=[]
  const pointsValue=insights?.outcome?.points||[]
  if(pointsValue.length>1){
    withValues=pointsValue.map((item)=>item.with_safepause);withoutValues=pointsValue.map((item)=>item.without_safepause);labels=pointsValue.map((item)=>item.at)
  }else{
    const pre=state.data.pre_decision||[],withFrames=pre.concat(state.data.branches?.ACTIVATE||[]),withoutFrames=pre.concat(state.data.branches?.CONTINUE||[])
    if(withFrames.length<2||withoutFrames.length<2)return false
    withValues=withFrames.map((item)=>item.city?.urgent_drivers||0);withoutValues=withoutFrames.map((item)=>item.city?.urgent_drivers||0);labels=withFrames.map((item)=>item.time_label)
  }
  addGrid(layers,[65,130,195,260,325])
  layers.labels.appendChild(svgElement("text",{x:52,y:34,class:"insight-title"},"With SafePause versus without SafePause"))
  const all=withValues.concat(withoutValues),max=Math.max(1,...all)
  const scale=(values)=>values.map((value,index)=>[62+(index/Math.max(1,values.length-1))*540,325-(Number(value)/max)*260])
  const withPoints=scale(withValues),withoutPoints=scale(withoutValues)
  layers.marks.appendChild(svgElement("path",{d:path(withoutPoints),class:"insight-high"}))
  layers.marks.appendChild(svgElement("path",{d:path(withPoints),class:"insight-expected"}))
  layers.labels.appendChild(svgElement("text",{x:62,y:365,class:"insight-axis"},String(labels[0]||"")))
  layers.labels.appendChild(svgElement("text",{x:602,y:365,"text-anchor":"end",class:"insight-axis"},String(labels.at(-1)||"")))
  return true
}
function renderInsights(state, frame, zone) {
  const root=state.root,layers=insightLayers(root)
  const fallbackView=Object.values(state.data.decision_views||{})[0]
  const insights=state.data.decision_views?.[zone?.id]?.insights || fallbackView?.insights
  const empty=q(root,"[data-insight-empty]"),svg=q(root,"[data-insight]")
  let rendered=false
  if(state.insightView==="timing"&&!zone){
    setText(root,"[data-insight-caption]","All districts · amber areas are included in the current plan.")
    rendered=renderAllDistrictsInsight(state,frame,layers)
  }else if(state.insightView==="timing"){
    setText(root,"[data-insight-caption]",`${zone.name} · expected and high-demand forecast.`)
    rendered=renderTimingInsight(insights,layers)
  }else if(state.insightView==="tradeoffs"){
    setText(root,"[data-insight-caption]","City-wide portfolio · each point is one evaluated plan combination.")
    rendered=renderTradeoffInsight(insights,layers)
  }else if(state.insightView==="stress"){
    setText(root,"[data-insight-caption]",zone ? `${zone.name} plus city budget · 100% marks each operating limit.` : "City plan · 100% marks each operating limit.")
    rendered=renderStressInsight(insights,layers)
  }else{
    setText(root,"[data-insight-caption]","Same scenario · comparison branches only after the recorded choice.")
    rendered=renderOutcomeInsight(state,insights,layers)
  }
  svg.hidden=!rendered;empty.hidden=rendered
}
function renderActions(state, frame, zone) {
  const root = state.root
  const citywide = !zone
  const decisionIndex = (state.data.pre_decision || []).length - 1
  const atDecision = isCurrent(state) || state.index >= decisionIndex
  const decisionViews = Object.values(state.data.decision_views || {})
  const decision = state.data.decision_views?.[zone?.id]?.recommendation
    || decisionViews.find((item) => item?.recommendation?.can_activate)?.recommendation
    || decisionViews[0]?.recommendation
  const currentActions = state.data.current_actions || {}
  const activate = q(root, '[data-choice="ACTIVATE"]')
  const continueButton = q(root, '[data-choice="CONTINUE"]')
  const canActivate = citywide
    ? Boolean(isCurrent(state) ? currentActions.available : decision?.can_activate)
    : Boolean(decision?.can_activate)
  const recorded = isCurrent(state) ? currentActions.recorded_action : state.choice
  const available = isCurrent(state) ? Boolean(currentActions.available) : atDecision
  const locked = Boolean(recorded) || Boolean(currentActions.recording)
  activate.disabled = !available || !canActivate || locked
  continueButton.disabled = !available || locked
  activate.classList.toggle("selected", recorded === "ACTIVATE")
  continueButton.classList.toggle("selected", recorded === "CONTINUE")
}
function svgNode(name, attrs = {}, text = null) {
  const node = document.createElementNS(NS, name)
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)))
  if (text != null) node.textContent = String(text)
  return node
}
function chartPath(values, x0, x1, top, bottom) {
  const max = Math.max(1, ...values)
  return values.map((value, index) => {
    const x = x0 + (index / Math.max(1, values.length - 1)) * (x1 - x0)
    const y = bottom - (Number(value) / max) * (bottom - top)
    return [x, y]
  })
}
function pathD(points) {
  return points.map(([x, y], index) => `${index ? "L" : "M"} ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ")
}
function addChartText(layer, x, y, text, className = "insight-axis", anchor = "start") {
  layer.appendChild(svgNode("text", { x, y, class: className, "text-anchor": anchor }, text))
}
function addGridAlternative(grid) {
  ;[58, 152, 225, 342].forEach((y) => grid.appendChild(svgNode("line", { x1: 52, y1: y, x2: 620, y2: y, class: "insight-grid" })))
}
function districtBarClass(zone, state) {
  if (zone.id === state.selectedZone) return "insight-selected"
  if (zone.included) return "insight-included"
  return "insight-neutral"
}
function renderDistrictComparison(state, frame, marks, labels) {
  const zones = frame.zones || []
  if (!zones.length) return false
  const urgentMax = Math.max(1, ...zones.map((zone) => Number(zone.urgent_drivers || 0)))
  const demandMax = Math.max(1, ...zones.map((zone) => Number(zone.forecast_requests_30m ?? zone.requests_15m ?? 0)))
  const step = 560 / zones.length
  const width = Math.max(9, step * .58)
  addChartText(labels, 52, 34, "Drivers needing a break now", "insight-title")
  addChartText(labels, 52, 205, "Forecast demand", "insight-title")
  zones.forEach((zone, index) => {
    const x = 58 + index * step + (step - width) / 2
    const urgent = Number(zone.urgent_drivers || 0)
    const demand = Number(zone.forecast_requests_30m ?? zone.requests_15m ?? 0)
    const urgentHeight = (urgent / urgentMax) * 88
    const demandHeight = (demand / demandMax) * 88
    marks.appendChild(svgNode("rect", { x, y: 152 - urgentHeight, width, height: urgentHeight, rx: 3, class: districtBarClass(zone, state) }))
    marks.appendChild(svgNode("rect", { x, y: 342 - demandHeight, width, height: demandHeight, rx: 3, class: districtBarClass(zone, state) }))
    addChartText(labels, x + width / 2, 166, urgent.toLocaleString(), "insight-value", "middle")
    addChartText(labels, x + width / 2, 356, demand.toLocaleString(), "insight-value", "middle")
    addChartText(labels, x + width / 2, 378, zone.name, "insight-axis", "middle")
  })
  return true
}
function renderTiming(state, frame, insights, marks, labels) {
  if (!state.selectedZone) return renderDistrictComparison(state, frame, marks, labels)
  const options = insights?.timing_options || []
  if (!options.length) return false
  const expected = options.map((item) => Number(item.expected_demand || 0))
  const high = options.map((item) => Number(item.high_demand ?? item.expected_demand ?? 0))
  const safety = options.map((item) => Number(item.projected_drivers_at_limit ?? item.drivers_protected ?? 0))
  const expectedPoints = chartPath(expected, 62, 608, 55, 151)
  const highPoints = chartPath(high, 62, 608, 55, 151)
  const safetyMax = Math.max(1, ...safety)
  const step = 546 / options.length
  addChartText(labels, 52, 34, "Demand around each start option", "insight-title")
  addChartText(labels, 52, 205, "Projected drivers at the safety limit", "insight-title")
  marks.appendChild(svgNode("path", { d: pathD(expectedPoints), class: "insight-expected" }))
  marks.appendChild(svgNode("path", { d: pathD(highPoints), class: "insight-high" }))
  expectedPoints.forEach(([x, y]) => marks.appendChild(svgNode("circle", { cx: x, cy: y, r: 4, class: "insight-selected insight-point" })))
  options.forEach((item, index) => {
    const width = Math.max(18, step * .48)
    const x = 62 + index * step + (step - width) / 2
    const height = (safety[index] / safetyMax) * 100
    const className = item.selected ? "insight-included" : item.feasible ? "insight-selected" : "insight-neutral"
    marks.appendChild(svgNode("rect", { x, y: 342 - height, width, height, rx: 3, class: className }))
    addChartText(labels, x + width / 2, 358, Number(safety[index]).toFixed(0), "insight-value", "middle")
    addChartText(labels, x + width / 2, 378, item.start_time_label, "insight-axis", "middle")
  })
  return true
}
function renderTradeoffs(insights, marks, labels) {
  const options = insights?.portfolio_options || []
  if (!options.length) return false
  const maxCost = Math.max(1, Number(insights.budget_limit_usd || 0), ...options.map((item) => Number(item.high_demand_cost_usd || 0)))
  const maxExposure = Math.max(1, ...options.map((item) => Number(item.exposure_hours_avoided || 0)))
  const x = (value) => 62 + (Number(value) / maxCost) * 540
  const y = (value) => 342 - (Number(value) / maxExposure) * 278
  const limitX = x(insights.budget_limit_usd || 0)
  marks.appendChild(svgNode("line", { x1: limitX, y1: 52, x2: limitX, y2: 342, class: "insight-limit" }))
  addChartText(labels, limitX - 4, 47, "Budget limit", "insight-axis", "end")
  options.forEach((item) => {
    const className = item.selected ? "insight-included" : item.feasible ? "insight-selected" : "insight-neutral"
    const radius = item.selected ? 10 : Math.max(5, Math.min(9, 4 + Number(item.protected_drivers || 0) / 80))
    marks.appendChild(svgNode("circle", { cx: x(item.high_demand_cost_usd), cy: y(item.exposure_hours_avoided), r: radius, class: `${className} insight-point` }))
    if (item.selected) addChartText(labels, x(item.high_demand_cost_usd), y(item.exposure_hours_avoided) - 15, "Selected plan", "insight-value", "middle")
  })
  addChartText(labels, 52, 34, "Cost vs heat-exposure avoided", "insight-title")
  addChartText(labels, 335, 378, "Estimated high-demand cost", "insight-axis", "middle")
  return true
}
function renderStress(insights, marks, labels) {
  const metrics = insights?.stress_metrics || []
  if (!metrics.length) return false
  addChartText(labels, 52, 34, "Expected and high-demand cases vs each operating limit", "insight-title")
  const limitX = 52 + (100 / 130) * 550
  marks.appendChild(svgNode("line", { x1: limitX, y1: 50, x2: limitX, y2: 350, class: "insight-limit" }))
  addChartText(labels, limitX, 46, "100% limit", "insight-axis", "middle")
  metrics.forEach((item, index) => {
    const y = 78 + index * 72
    const limit = Number(item.limit_value || 1)
    const expected = Math.min(130, (Number(item.expected_value || 0) / limit) * 100)
    const high = Math.min(130, (Number(item.high_demand_value || 0) / limit) * 100)
    marks.appendChild(svgNode("rect", { x: 52, y, width: (expected / 130) * 550, height: 15, rx: 4, class: "insight-selected" }))
    marks.appendChild(svgNode("rect", { x: 52, y: y + 22, width: (high / 130) * 550, height: 15, rx: 4, class: item.passed ? "insight-included" : "insight-neutral" }))
    addChartText(labels, 52, y - 7, item.label, "insight-title")
    addChartText(labels, 608, y + 12, item.expected_label, "insight-axis", "end")
    addChartText(labels, 608, y + 34, `${item.high_demand_label} · ${item.passed ? "Pass" : "Blocked"}`, "insight-axis", "end")
  })
  return true
}
function renderOutcome(state, insights, marks, labels) {
  let points = insights?.outcome?.points || []
  let withValues = points.map((item) => Number(item.with_safepause || 0))
  let withoutValues = points.map((item) => Number(item.without_safepause || 0))
  let xLabels = points.map((item) => String(item.at || "").slice(11, 16))
  if (points.length < 2) {
    const activate = (state.data.pre_decision || []).concat(state.data.branches?.ACTIVATE || [])
    const continued = (state.data.pre_decision || []).concat(state.data.branches?.CONTINUE || [])
    if (activate.length < 2 || continued.length !== activate.length) return false
    withValues = activate.map((frame) => Number(frame.city?.urgent_drivers || 0))
    withoutValues = continued.map((frame) => Number(frame.city?.urgent_drivers || 0))
    xLabels = activate.map((frame) => frame.time_label)
  }
  const max = Math.max(1, ...withValues, ...withoutValues)
  const project = (values) => values.map((value, index) => [
    62 + (index / Math.max(1, values.length - 1)) * 546,
    342 - (value / max) * 278,
  ])
  marks.appendChild(svgNode("path", { d: pathD(project(withoutValues)), class: "insight-high" }))
  marks.appendChild(svgNode("path", { d: pathD(project(withValues)), class: "insight-expected" }))
  addChartText(labels, 52, 34, "With SafePause vs without SafePause", "insight-title")
  addChartText(labels, 62, 374, xLabels[0], "insight-axis")
  addChartText(labels, 608, 374, xLabels.at(-1), "insight-axis", "end")
  return true
}
function renderInsightsAlternative(state, frame, zone) {
  const root = state.root
  const grid = q(root, "[data-insight-grid]")
  const marks = q(root, "[data-insight-marks]")
  const labels = q(root, "[data-insight-labels]")
  grid.replaceChildren(); marks.replaceChildren(); labels.replaceChildren()
  addGrid(grid)
  const insights = state.data.decision_views?.[zone?.id]?.insights
  let rendered = false
  if (state.insightView === "timing") rendered = renderTiming(state, frame, insights, marks, labels)
  else if (state.insightView === "tradeoffs") rendered = renderTradeoffs(insights, marks, labels)
  else if (state.insightView === "stress") rendered = renderStress(insights, marks, labels)
  else rendered = renderOutcome(state, insights, marks, labels)
  q(root, "[data-insight]").hidden = !rendered
  q(root, "[data-insight-empty]").hidden = rendered
  const captions = {
    timing: zone ? `${zone.name} · expected and high-demand forecast.` : "All districts · amber areas are included in the current plan.",
    tradeoffs: "City-wide portfolio · each point is one evaluated plan combination.",
    stress: zone ? `${zone.name} plus city budget · 100% marks each operating limit.` : "City plan · 100% marks each operating limit.",
    outcome: "Same scenario · comparison branches only after the recorded choice.",
  }
  setText(root, "[data-insight-caption]", captions[state.insightView])
}
function render(state) {
  const frame = currentFrame(state)
  if (!frame) return
  const root = state.root
  const frames = displayFrames(state)
  const playbackRoot = q(root, ".playback-root")
  playbackRoot.dataset.mode = state.data.mode || "replay"
  setText(root, "[data-range]", state.data.range_label)
  setText(root, "[data-time]", frame.time_label)
  setText(root, "[data-decision-time]", state.data.decision_time_label)
  q(root, "[data-progress]").style.width = `${(state.index / Math.max(1, frames.length - 1)) * 100}%`
  setText(root, "[data-play-label]", state.running ? "Pause" : "Play")
  q(root, '[data-action="next"]').disabled = state.running || !canAdvance(state)
  q(root, '[data-action="play"]').disabled = !state.running && !canAdvance(state)
  if (isCurrent(state) && state.data.current_kpis?.length === 3) {
    const valueSelectors = ["[data-kpi-urgent]", "[data-kpi-coverage]", "[data-kpi-budget]"]
    state.data.current_kpis.forEach((card, index) => {
      setText(root, `[data-kpi-label-${index}]`, card.label)
      setText(root, valueSelectors[index], card.value)
      setText(root, `[data-kpi-note-${index}]`, card.detail)
    })
  } else {
    setText(root, "[data-kpi-label-0]", "Mandatory breaks now")
    setText(root, "[data-kpi-note-0]", "Across Hanoi")
    setText(root, "[data-kpi-urgent]", `${Number(frame.city.urgent_drivers || 0).toLocaleString()} drivers`)
    const atRisk = preventiveRiskAtTick(state, frame)
    const riskAvailable = atRisk != null
    setText(root, "[data-kpi-label-1]", riskAvailable ? "At risk within 15 min" : "Preventive risk")
    setText(root, "[data-kpi-coverage]", riskAvailable ? `${Number(atRisk).toLocaleString()} drivers` : "Not available")
    setText(root, "[data-kpi-note-1]", riskAvailable ? "Projected from current evidence" : "15-minute projection unavailable")
    const activeDrivers = frame.city.active_drivers ?? (frame.zones || []).reduce(
      (total, zone) => total + Number(zone.active_drivers || 0), 0
    )
    setText(root, "[data-kpi-label-2]", "Active drivers")
    setText(root, "[data-kpi-budget]", Number(activeDrivers).toLocaleString())
    setText(root, "[data-kpi-note-2]", "online now")
  }
  renderMap(state, frame)
  const zone = selectedZone(state, frame)
  renderActions(state, frame, zone)
  renderInsights(state, frame, zone)
}
function mount(root, data, setTriggerValue, setStateValue) {
  const state = { root, data, setTriggerValue, setStateValue, index:0, choice:null, selectedZone:null, speed:"normal", mapMetric:"heat", mapZoom:11, insightView:"timing", running:false, timer:null, lastEmittedReplayState:null }
  q(root, '[data-action="play"]').onclick = () => { if (state.running) { stop(state); render(state) } else start(state) }
  q(root, '[data-action="next"]').onclick = () => step(state)
  q(root, '[data-action="reset"]').onclick = () => { stop(state); state.index=0; state.choice=null; state.selectedZone=null; render(state); emitReplayState(state) }
  q(root, "[data-map-all]").onclick = () => selectAllDistricts(state)
  q(root, "[data-speed]").onchange = (event) => { state.speed=event.target.value; if (state.running) start(state) }
  q(root, "[data-map-metric]").onchange = (event) => { state.mapMetric=event.target.value; render(state) }
  q(root, "[data-map-zoom-in]").onclick = () => { state.mapZoom=Math.min(12, state.mapZoom + 1); render(state) }
  q(root, "[data-map-zoom-out]").onclick = () => { state.mapZoom=Math.max(10, state.mapZoom - 1); render(state) }
  q(root, "[data-insight-view]").onchange = (event) => { state.insightView=event.target.value; render(state) }
  root.querySelectorAll("[data-choice]").forEach((button) => {
    button.onclick = () => {
      stop(state)
      if (isCurrent(state)) state.setTriggerValue?.("decision_action", button.dataset.choice)
      else state.choice=button.dataset.choice
      render(state)
      emitReplayState(state)
    }
  })
  state.resizeObserver = new ResizeObserver(() => render(state))
  state.resizeObserver.observe(q(root, "[data-city-map]"))
  INSTANCES.set(root, state)
  render(state)
  return state
}
export default function(component) {
  const { data, parentElement, setTriggerValue, setStateValue } = component
  let state = INSTANCES.get(parentElement)
  if (!state) state = mount(parentElement, data, setTriggerValue, setStateValue)
  else {
    const modeChanged = state.data.mode !== data.mode
    state.data = data
    state.setTriggerValue = setTriggerValue
    state.setStateValue = setStateValue
    state.index = Math.min(state.index, sequence(state).length - 1)
    if (modeChanged) {
      stop(state); state.index=0; state.choice=null
      state.mapMetric="heat"
      q(parentElement, "[data-map-metric]").value="heat"
      state.selectedZone=null
    }
    render(state)
  }
  return () => { stop(state); state.resizeObserver?.disconnect(); INSTANCES.delete(parentElement) }
}
"""

@lru_cache(maxsize=2)
def load_presentation_timeline(path: str = str(DEFAULT_TIMELINE)) -> dict[str, Any]:
    """Load and minimally validate the bounded presentation artifact."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("schema_version") != "operator-presentation-v1":
        raise ValueError("operator presentation timeline schema is unsupported")
    pre = value.get("pre_decision")
    branches = value.get("branches")
    if not isinstance(pre, list) or len(pre) < 2:
        raise ValueError("operator presentation timeline has no usable pre-decision frames")
    if not isinstance(branches, dict) or not all(
        isinstance(branches.get(name), list) for name in ("ACTIVATE", "CONTINUE")
    ):
        raise ValueError("operator presentation timeline branches are incomplete")
    value["district_boundaries"] = load_hanoi_operator_districts()
    value["mode"] = "replay"
    return value


def _parse_replay_state(
    data: Mapping[str, Any], value: Any
) -> tuple[int | None, str | None, str | None]:
    """Resolve one atomic browser cursor against the immutable replay payload."""
    if data.get("mode") != "replay":
        return None, None, None
    pre_decision = data.get("pre_decision")
    if not isinstance(pre_decision, list) or not pre_decision:
        return None, None, None

    default_frame = pre_decision[0]
    if not isinstance(default_frame, Mapping):
        return None, None, None
    default_zones = default_frame.get("zones")
    if not isinstance(default_zones, list) or not default_zones:
        return None, None, None
    default_zone = next(
        (
            zone
            for zone in default_zones
            if isinstance(zone, Mapping) and zone.get("selected")
        ),
        default_zones[0],
    )
    if not isinstance(default_zone, Mapping):
        return None, None, None
    default_tick = default_frame.get("tick")
    default_zone_id = default_zone.get("id")
    default_branch = default_frame.get("branch", "PRE_DECISION")
    fallback = (
        default_tick if isinstance(default_tick, int) and not isinstance(default_tick, bool) else None,
        default_zone_id if isinstance(default_zone_id, str) and default_zone_id else None,
        default_branch if default_branch in {"PRE_DECISION", "ACTIVATE", "CONTINUE"} else "PRE_DECISION",
    )
    if not isinstance(value, Mapping):
        return fallback

    tick = value.get("tick")
    zone_id = value.get("selected_zone_id")
    branch = value.get("branch")
    if (
        not isinstance(tick, int)
        or isinstance(tick, bool)
        or not isinstance(zone_id, str)
        or not zone_id
        or branch not in {"PRE_DECISION", "ACTIVATE", "CONTINUE"}
    ):
        return fallback

    candidate_frames = list(pre_decision)
    branches = data.get("branches")
    if branch in {"ACTIVATE", "CONTINUE"} and isinstance(branches, Mapping):
        branch_frames = branches.get(branch)
        if isinstance(branch_frames, list):
            candidate_frames.extend(branch_frames)
    frame = next(
        (
            item
            for item in candidate_frames
            if isinstance(item, Mapping) and item.get("tick") == tick
        ),
        None,
    )
    if not isinstance(frame, Mapping):
        return fallback
    zones = frame.get("zones")
    if not isinstance(zones, list) or not any(
        isinstance(zone, Mapping) and zone.get("id") == zone_id for zone in zones
    ):
        return fallback
    return tick, zone_id, str(branch)


def _render_dashboard_component(
    data: dict[str, Any],
    *,
    key: str,
) -> OperatorDashboardResult:
    """Mount the one shared Current/Replay DOM and return live-only intents."""
    # Register in the active Streamlit runtime. This is intentionally done at
    # the render boundary because AppTest can replace the runtime registry
    # between script runs while keeping imported Python modules cached.
    presentation = st.components.v2.component(
        "heatsafe_operator_presentation",
        html=_HTML,
        css=_CSS,
        js=_JS,
    )
    result = presentation(
        data=data,
        key=key,
        width="stretch",
        height="content",
        on_selected_zone_id_change=lambda: None,
        on_decision_action_change=lambda: None,
        on_replay_state_change=lambda: None,
    )
    selected_zone_id = getattr(result, "selected_zone_id", None)
    decision_action = getattr(result, "decision_action", None)
    replay_tick, replay_zone, replay_branch = _parse_replay_state(
        data, getattr(result, "replay_state", None)
    )
    return OperatorDashboardResult(
        selected_zone_id=(
            replay_zone
            if data.get("mode") == "replay"
            else (
                selected_zone_id
                if isinstance(selected_zone_id, str) and selected_zone_id
                else None
            )
        ),
        decision_action=(
            str(decision_action)
            if decision_action in {"ACTIVATE", "CONTINUE"}
            else None
        ),
        replay_tick_index=replay_tick,
        replay_branch=replay_branch,
    )


def render_operator_dashboard(
    view: OperatorConsoleView,
    *,
    decision_available: bool = True,
    recording: bool = False,
    recorded_action: str | None = None,
    key: str = "operator-dashboard",
) -> OperatorDashboardResult:
    """Render live conditions through the exact same component used by replay."""
    return _render_dashboard_component(
        build_current_dashboard_payload(
            view,
            decision_available=decision_available,
            recording=recording,
            recorded_action=recorded_action,
        ),
        key=key,
    )


def render_presentation_playback(
    timeline: dict[str, Any] | None = None,
    *,
    key: str = "operator-dashboard",
) -> OperatorDashboardResult:
    """Render immutable replay and return its bounded browser context."""
    return _render_dashboard_component(
        timeline or load_presentation_timeline(),
        key=key,
    )


__all__ = [
    "DEFAULT_TIMELINE",
    "OperatorDashboardResult",
    "build_current_dashboard_payload",
    "load_presentation_timeline",
    "render_operator_dashboard",
    "render_presentation_playback",
]
