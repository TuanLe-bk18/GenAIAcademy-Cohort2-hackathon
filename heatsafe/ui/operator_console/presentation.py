"""Browser-local, display-only operator playback.

Streamlit fragments still clear and redraw their child elements. This CCv2 surface
keeps one stable DOM and advances a precomputed timeline entirely in the browser so
Play and Next do not run Python, the simulator, the optimizer, or a Streamlit rerun.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TIMELINE = (
    ROOT
    / "data"
    / "scenarios"
    / "hanoi_heatwave_v1"
    / "operator_presentation_timeline.json"
)

_HTML = """
<main class="playback-root" aria-label="HeatSafe simulation playback">
  <section class="playback-toolbar" aria-label="Playback controls">
    <div class="playback-clock">
      <span class="live-dot" aria-hidden="true"></span>
      <div>
        <div class="toolbar-kicker">Simulation playback · <span data-range></span></div>
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

  <section class="status-strip">
    <span class="status-pill" data-status></span>
    <span>Synthetic Hanoi operations · display-only replay · fixed demo limits <span data-limit-copy></span> · no real dispatch</span>
  </section>

  <section class="kpi-grid" aria-label="City status">
    <article class="kpi-card critical">
      <span>Drivers needing a break now</span><strong data-kpi-urgent>0</strong><small>Across Hanoi</small>
    </article>
    <article class="kpi-card coverage">
      <span>Safety coverage</span><strong data-kpi-coverage>Monitoring</strong><small data-kpi-coverage-note>Available at decision time</small>
    </article>
    <article class="kpi-card budget">
      <span>Budget remaining after this plan</span><strong data-kpi-budget>—</strong><small>High-demand reserve included</small>
    </article>
  </section>

  <section class="workspace-grid">
    <article class="panel map-panel">
      <header><div><span class="eyebrow">Live conditions</span><h2>Hanoi heat map</h2></div><span data-map-summary></span></header>
      <div class="map-stage">
        <svg data-map viewBox="0 0 720 360" role="img" aria-label="Bubble map of ten Hanoi operating areas">
          <defs>
            <pattern id="city-grid" width="42" height="42" patternUnits="userSpaceOnUse">
              <path d="M 42 0 L 0 0 0 42" fill="none" stroke="var(--map-grid)" stroke-width="1"/>
            </pattern>
            <filter id="map-glow"><feGaussianBlur stdDeviation="5" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
          </defs>
          <rect width="720" height="360" rx="12" fill="var(--map-canvas)"/>
          <rect width="720" height="360" rx="12" fill="url(#city-grid)" opacity=".65"/>
          <path d="M80 282 C180 220 218 258 310 190 S470 138 644 72" fill="none" stroke="var(--map-river-bed)" stroke-width="11" opacity=".9"/>
          <path d="M80 282 C180 220 218 258 310 190 S470 138 644 72" fill="none" stroke="var(--map-river)" stroke-width="2" opacity=".8"/>
          <g data-map-zones></g>
        </svg>
        <div class="map-legend"><span><i class="legend-hot"></i>Heat severity</span><span><i class="legend-selected"></i>Selected / included</span></div>
      </div>
      <div class="priority-list" data-priority aria-label="Priority areas"></div>
    </article>

    <article class="panel decision-panel">
      <header><div><span class="eyebrow">Selected area</span><h2 data-area-name>—</h2></div><span class="heat-badge" data-area-heat>—</span></header>
      <div class="area-stats">
        <div><span>Need a break now</span><strong data-area-urgent>0</strong></div>
        <div><span>Active drivers</span><strong data-area-active>0</strong></div>
        <div><span>Requests / 15 min</span><strong data-area-requests>0</strong></div>
      </div>
      <div class="recommendation-card">
        <span class="eyebrow" data-recommendation-state>Live monitoring</span>
        <h3 data-recommendation-headline>Monitoring conditions</h3>
        <p data-recommendation-copy>Conditions update every 15 operational minutes.</p>
      </div>
      <div class="guardrails" data-guardrails></div>
      <div class="decision-actions" data-decision-actions hidden>
        <button type="button" class="primary" data-choice="ACTIVATE">Activate SafePause</button>
        <button type="button" data-choice="CONTINUE">Continue monitoring</button>
      </div>
      <div class="decision-receipt" data-decision-receipt hidden></div>
      <p class="decision-note" data-decision-note>Action becomes available at the recommendation time.</p>
    </article>
  </section>

  <section class="panel trend-panel">
    <header>
      <div><span class="eyebrow">Playback trend</span><h2>City conditions over the operating window</h2></div>
      <div class="trend-legend"><span><i class="legend-urgent"></i>Drivers at safety limit</span><span><i class="legend-demand"></i>Requests / 15 min</span></div>
    </header>
    <svg data-trend viewBox="0 0 1180 250" role="img" aria-label="City exposure and request trend">
      <defs>
        <linearGradient id="urgent-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--heat)" stop-opacity=".28"/><stop offset="1" stop-color="var(--heat)" stop-opacity="0"/></linearGradient>
      </defs>
      <g class="chart-grid">
        <line x1="62" y1="34" x2="1150" y2="34"/><line x1="62" y1="94" x2="1150" y2="94"/>
        <line x1="62" y1="154" x2="1150" y2="154"/><line x1="62" y1="214" x2="1150" y2="214"/>
      </g>
      <g data-chart-reveal>
        <path data-urgent-area fill="url(#urgent-fill)"></path>
        <path data-urgent-line class="urgent-line"></path>
        <path data-demand-line class="demand-line"></path>
      </g>
      <line data-cursor class="chart-cursor" y1="25" y2="219"></line>
      <circle data-urgent-dot class="urgent-dot" r="5"></circle>
      <circle data-demand-dot class="demand-dot" r="4"></circle>
      <text x="62" y="240" data-start-label></text>
      <text x="1112" y="240" data-end-label></text>
      <text x="70" y="52" class="chart-value" data-chart-urgent></text>
      <text x="70" y="72" class="chart-value demand-value" data-chart-demand></text>
    </svg>
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
.playback-toolbar { position:relative; display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:72px; padding:12px 16px 16px; border:1px solid var(--border); border-radius:13px; background:var(--surface); overflow:hidden; }
.playback-clock { display:flex; align-items:center; gap:12px; }
.live-dot { width:10px; height:10px; border-radius:50%; background:var(--primary); box-shadow:0 0 0 5px var(--primary-bg); }
.toolbar-kicker { color:var(--text); font-weight:750; font-size:14px; }
.toolbar-time { color:var(--muted); font-size:13px; margin-top:3px; }
.toolbar-time strong { color:var(--safe); font-size:17px; font-variant-numeric:tabular-nums; }
.playback-actions { display:flex; align-items:center; flex-wrap:wrap; justify-content:flex-end; gap:8px; }
.playback-actions button, .decision-actions button, .priority-list button { border:1px solid var(--border); border-radius:8px; background:var(--surface); color:var(--text); cursor:pointer; min-height:36px; padding:7px 12px; transition:background-color .18s ease, border-color .18s ease, opacity .18s ease; }
.playback-actions button:hover, .decision-actions button:hover, .priority-list button:hover { background:var(--raised); border-color:var(--primary); }
.playback-actions button:focus-visible, .decision-actions button:focus-visible, .priority-list button:focus-visible, select:focus-visible { outline:2px solid var(--primary); outline-offset:2px; }
.playback-actions button:disabled, .decision-actions button:disabled { opacity:.42; cursor:not-allowed; }
.playback-actions label { display:flex; align-items:center; gap:6px; color:var(--muted); font-size:12px; }
.playback-actions select { border:1px solid var(--border); border-radius:8px; background:var(--surface); color:var(--text); padding:7px 8px; cursor:pointer; }
.progress-track { position:absolute; height:4px; background:var(--raised); left:0; right:0; bottom:0; }
.progress-track span { display:block; height:100%; width:0; background:var(--primary); transition:width .42s cubic-bezier(.2,.8,.2,1); }
.status-strip { display:flex; align-items:center; flex-wrap:wrap; gap:9px; color:var(--muted); font-size:12px; padding:0 4px; }
.status-pill { border:1px solid var(--primary); border-radius:999px; color:var(--safe); background:var(--primary-bg); padding:3px 9px; font-weight:700; }
.kpi-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
.kpi-card { min-height:104px; padding:13px 15px; border:1px solid var(--border); border-radius:12px; background:var(--surface); display:flex; flex-direction:column; justify-content:center; overflow:hidden; position:relative; }
.kpi-card::before { content:""; position:absolute; inset:0 auto 0 0; width:4px; background:var(--primary); }
.kpi-card.critical::before { background:var(--critical); }.kpi-card.budget::before { background:var(--heat); }
.kpi-card span,.kpi-card small { color:var(--muted); font-size:12px; }.kpi-card strong { margin:5px 0 2px; font-size:25px; letter-spacing:-.03em; font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.workspace-grid { display:grid; grid-template-columns:minmax(0,1.75fr) minmax(330px,1fr); gap:12px; align-items:stretch; }
.panel { border:1px solid var(--border); border-radius:13px; background:var(--surface); overflow:hidden; }
.panel > header { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; padding:14px 16px 10px; }
.panel h2 { font-size:17px; line-height:1.25; margin:2px 0 0; }.eyebrow { color:var(--muted); font-size:10px; letter-spacing:.11em; text-transform:uppercase; font-weight:750; }
.map-panel header > span { color:var(--muted); font-size:12px; }
.map-stage { padding:0 12px; position:relative; }.map-stage svg { display:block; width:100%; height:315px; }
.map-zone { cursor:pointer; }.map-zone circle { transition:r .42s cubic-bezier(.2,.8,.2,1), fill .32s ease, stroke .24s ease, opacity .24s ease; }.map-zone text { fill:var(--text); font-size:10px; font-weight:650; pointer-events:none; }
.map-legend,.trend-legend { display:flex; flex-wrap:wrap; gap:12px; color:var(--muted); font-size:10px; }.map-legend { position:absolute; left:24px; bottom:10px; padding:5px 8px; border:1px solid var(--border); border-radius:7px; background:var(--surface); }
.map-legend i,.trend-legend i { display:inline-block; width:9px; height:9px; margin-right:5px; border-radius:50%; vertical-align:-1px; }.legend-hot{background:var(--heat)}.legend-selected{border:2px solid var(--primary)}.legend-urgent{background:var(--heat)}.legend-demand{background:var(--context)}
.priority-list { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; padding:10px 12px 12px; }.priority-list button { text-align:left; min-width:0; padding:8px 10px; }.priority-list button.selected { border-color:var(--primary); background:var(--primary-bg); }.priority-list b,.priority-list span { display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }.priority-list span { color:var(--muted); font-size:10px; margin-top:2px; }
.heat-badge { color:var(--heat); border:1px solid var(--heat); border-radius:999px; background:var(--heat-bg); padding:4px 8px; font-size:11px; font-weight:700; white-space:nowrap; }
.area-stats { display:grid; grid-template-columns:repeat(3,1fr); gap:1px; margin:0 14px; border:1px solid var(--border); border-radius:9px; overflow:hidden; background:var(--border); }.area-stats div { background:var(--surface); padding:10px; }.area-stats span { display:block; color:var(--muted); font-size:10px; }.area-stats strong { display:block; margin-top:3px; font-size:17px; font-variant-numeric:tabular-nums; }
.recommendation-card { margin:12px 14px; padding:12px; min-height:102px; border-left:3px solid var(--primary); border-radius:0 9px 9px 0; background:var(--primary-bg); }.recommendation-card h3 { font-size:16px; line-height:1.3; margin:4px 0 5px; }.recommendation-card p { color:var(--muted); font-size:12px; line-height:1.45; margin:0; }
.guardrails { margin:0 14px; min-height:96px; }.guardrail { display:flex; justify-content:space-between; gap:10px; padding:6px 0; border-bottom:1px solid var(--border); font-size:11px; }.guardrail span:first-child{color:var(--muted)}.guardrail .pass{color:var(--safe);font-weight:700}.guardrail .fail{color:var(--critical-text);font-weight:700}
.decision-actions { display:flex; gap:8px; padding:10px 14px 0; }.decision-actions button { flex:1; }.decision-actions .primary { color:#102218; background:var(--primary); border-color:var(--primary); font-weight:750; }.decision-actions .primary:hover{background:var(--safe)}
.decision-receipt { margin:10px 14px 0; border:1px solid var(--primary); border-radius:8px; color:var(--safe); background:var(--safe-bg); padding:9px 10px; font-size:12px; }
.decision-note { color:var(--muted); font-size:10px; margin:8px 14px 14px; }
.trend-panel > header { align-items:center; }.trend-panel svg { display:block; width:100%; height:205px; padding:0 8px 8px; }.chart-grid line { stroke:var(--border); stroke-width:1; }.urgent-line,.demand-line { fill:none; stroke-linejoin:round; stroke-linecap:round; }.urgent-line{stroke:var(--heat);stroke-width:3}.demand-line{stroke:var(--context);stroke-width:2;stroke-dasharray:5 4}.chart-cursor{stroke:var(--muted);stroke-width:1;stroke-dasharray:3 4;opacity:.72;transition:x1 .42s cubic-bezier(.2,.8,.2,1),x2 .42s cubic-bezier(.2,.8,.2,1)}.urgent-dot{fill:var(--heat);transition:cx .42s cubic-bezier(.2,.8,.2,1),cy .42s cubic-bezier(.2,.8,.2,1)}.demand-dot{fill:var(--context);transition:cx .42s cubic-bezier(.2,.8,.2,1),cy .42s cubic-bezier(.2,.8,.2,1)}.chart-value{fill:var(--heat);font-size:11px;font-weight:700}.demand-value{fill:var(--context)}[data-chart-reveal]{transition:clip-path .42s cubic-bezier(.2,.8,.2,1)}[data-trend] > text{fill:var(--muted);font-size:10px}
[hidden]{display:none!important}
@media(max-width:1000px){.playback-toolbar{align-items:flex-start;flex-direction:column}.playback-actions{justify-content:flex-start}.workspace-grid{grid-template-columns:1fr}.map-stage svg{height:300px}.kpi-card strong{font-size:21px}}
@media(max-width:680px){.kpi-grid,.priority-list{grid-template-columns:1fr}.area-stats{grid-template-columns:1fr}.playback-actions{display:grid;grid-template-columns:1fr 1fr;width:100%}.playback-actions label{grid-column:span 2}.trend-legend{display:none}}
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
  let zone = zones.find((item) => item.id === state.selectedZone)
  if (!zone) {
    zone = zones[0]
    state.selectedZone = zone?.id || null
  }
  return zone
}
function mapPoint(zone, zones) {
  const lats = zones.map((item) => Number(item.latitude))
  const lons = zones.map((item) => Number(item.longitude))
  const minLat = Math.min(...lats), maxLat = Math.max(...lats)
  const minLon = Math.min(...lons), maxLon = Math.max(...lons)
  const x = 82 + ((Number(zone.longitude) - minLon) / Math.max(.001, maxLon - minLon)) * 545
  const y = 300 - ((Number(zone.latitude) - minLat) / Math.max(.001, maxLat - minLat)) * 238
  return [x, y]
}
function renderMap(state, frame) {
  const root = state.root
  const layer = q(root, "[data-map-zones]")
  const zones = frame.zones || []
  const ids = new Set(zones.map((item) => item.id))
  layer.querySelectorAll(".map-zone").forEach((node) => { if (!ids.has(node.dataset.id)) node.remove() })
  zones.forEach((zone) => {
    let group = layer.querySelector(`[data-id="${zone.id}"]`)
    if (!group) {
      group = document.createElementNS(NS, "g")
      group.setAttribute("class", "map-zone")
      group.dataset.id = zone.id
      const circle = document.createElementNS(NS, "circle")
      const label = document.createElementNS(NS, "text")
      label.setAttribute("text-anchor", "middle")
      label.setAttribute("dy", "-15")
      group.append(circle, label)
      group.setAttribute("tabindex", "0")
      group.setAttribute("role", "button")
      group.onclick = () => { state.selectedZone = zone.id; render(state) }
      group.onkeydown = (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); state.selectedZone = zone.id; render(state) }
      }
      layer.appendChild(group)
    }
    const [x, y] = mapPoint(zone, zones)
    const circle = group.querySelector("circle")
    const label = group.querySelector("text")
    const selected = state.selectedZone === zone.id
    group.setAttribute("transform", `translate(${x} ${y})`)
    group.setAttribute("aria-label", `${zone.name}, ${zone.heat_state} heat, ${zone.urgent_drivers} drivers need a break`)
    circle.setAttribute("r", String(10 + Math.min(19, Math.sqrt(Math.max(0, zone.urgent_drivers)) * 1.2)))
    circle.setAttribute("fill", heatColor(root, Number(zone.heat_index_c)))
    circle.setAttribute("fill-opacity", selected ? ".98" : ".82")
    circle.setAttribute("stroke", selected || zone.included ? token(root, "--primary", "#43b66e") : token(root, "--muted", "#728079"))
    circle.setAttribute("stroke-width", selected ? "4" : zone.included ? "2.5" : "1")
    if (selected) circle.setAttribute("filter", "url(#map-glow)"); else circle.removeAttribute("filter")
    label.textContent = zone.name
  })
  const priority = q(root, "[data-priority]")
  priority.replaceChildren()
  zones.slice(0, 3).forEach((zone, index) => {
    const button = document.createElement("button")
    button.type = "button"
    if (state.selectedZone === zone.id) button.classList.add("selected")
    const title = document.createElement("b")
    title.textContent = `${index + 1}. ${zone.name}`
    const detail = document.createElement("span")
    detail.textContent = `${zone.heat_state} · ${zone.urgent_drivers} need a break`
    button.append(title, detail)
    button.onclick = () => { state.selectedZone = zone.id; render(state) }
    priority.appendChild(button)
  })
  setText(root, "[data-map-summary]", `${zones.length} operating areas · 15-minute conditions`)
}
function renderDecision(state, frame, zone) {
  const root = state.root
  setText(root, "[data-area-name]", zone?.name)
  setText(root, "[data-area-heat]", zone ? `${zone.heat_state} · ${Number(zone.heat_index_c).toFixed(1)}°C` : "—")
  tweenNumber(q(root, "[data-area-urgent]"), Number(zone?.urgent_drivers || 0))
  tweenNumber(q(root, "[data-area-active]"), Number(zone?.active_drivers || 0))
  tweenNumber(q(root, "[data-area-requests]"), Number(zone?.requests_15m || 0))
  const decisionIndex = (state.data.pre_decision || []).length - 1
  const atDecision = state.index >= decisionIndex
  const decision = state.data.decision_views?.[zone?.id]?.recommendation
  const actions = q(root, "[data-decision-actions]")
  const receipt = q(root, "[data-decision-receipt]")
  const activate = q(root, '[data-choice="ACTIVATE"]')
  const canActivate = Boolean(decision?.can_activate)
  activate.disabled = !canActivate
  if (!atDecision) {
    setText(root, "[data-recommendation-state]", "Live monitoring")
    setText(root, "[data-recommendation-headline]", "Monitoring conditions")
    setText(root, "[data-recommendation-copy]", `Next recommendation at ${state.data.decision_time_label}. The next interval is ready for instant playback.`)
  } else {
    setText(root, "[data-recommendation-state]", decision?.state === "ready" ? "Recommended action" : "Operator review")
    setText(root, "[data-recommendation-headline]", decision?.headline || "Recommendation temporarily unavailable")
    setText(root, "[data-recommendation-copy]", decision ? `${decision.group_summary} · ${decision.break_length_label}. ${decision.explanation}` : "Monitoring remains available.")
  }
  const guards = q(root, "[data-guardrails]")
  guards.replaceChildren()
  ;(atDecision ? (decision?.guardrails || []) : []).slice(0, 4).forEach((guard) => {
    const row = document.createElement("div")
    row.className = "guardrail"
    const label = document.createElement("span")
    label.textContent = guard.label
    const value = document.createElement("span")
    value.className = guard.passed ? "pass" : "fail"
    value.textContent = `${guard.passed ? "✓" : "!"} ${guard.value} · ${guard.status_label}`
    row.append(label, value)
    guards.appendChild(row)
  })
  actions.hidden = !atDecision || Boolean(state.choice)
  receipt.hidden = !state.choice
  if (state.choice) receipt.textContent = state.choice === "ACTIVATE" ? "SafePause selected for this display replay. The comparison continues locally." : "Continue monitoring selected. The no-action path continues locally."
  setText(root, "[data-decision-note]", !atDecision ? "Action becomes available at the recommendation time." : !state.choice ? (canActivate ? "Choose a path to continue the display replay." : "SafePause is disabled because this plan does not fit all current limits.") : "Use Play or Next 15 min to view the outcome.")
}
function points(frames, key, top, bottom) {
  const values = frames.map((frame) => Number(frame.city?.[key] || 0))
  const max = Math.max(1, ...values)
  return frames.map((frame, index) => {
    const x = 62 + (index / Math.max(1, frames.length - 1)) * 1088
    const y = bottom - (values[index] / max) * (bottom - top)
    return [x, y]
  })
}
function path(pointsValue) { return pointsValue.map(([x,y], index) => `${index ? "L" : "M"} ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ") }
function renderTrend(state, frame) {
  const root = state.root
  const frames = displayFrames(state)
  const urgent = points(frames, "urgent_drivers", 34, 214)
  const demand = points(frames, "requests_15m", 45, 205)
  const urgentPath = path(urgent)
  q(root, "[data-urgent-line]").setAttribute("d", urgentPath)
  q(root, "[data-demand-line]").setAttribute("d", path(demand))
  const area = `${urgentPath} L ${urgent.at(-1)[0].toFixed(1)} 214 L 62 214 Z`
  q(root, "[data-urgent-area]").setAttribute("d", area)
  const progress = state.index / Math.max(1, frames.length - 1)
  q(root, "[data-chart-reveal]").style.clipPath = `inset(0 ${(1 - progress) * 100}% 0 0)`
  const u = urgent[Math.min(state.index, urgent.length - 1)]
  const d = demand[Math.min(state.index, demand.length - 1)]
  const cursor = q(root, "[data-cursor]")
  cursor.setAttribute("x1", u[0]); cursor.setAttribute("x2", u[0])
  const urgentDot = q(root, "[data-urgent-dot]"), demandDot = q(root, "[data-demand-dot]")
  urgentDot.setAttribute("cx", u[0]); urgentDot.setAttribute("cy", u[1])
  demandDot.setAttribute("cx", d[0]); demandDot.setAttribute("cy", d[1])
  setText(root, "[data-start-label]", frames[0]?.time_label)
  setText(root, "[data-end-label]", frames.at(-1)?.time_label)
  setText(root, "[data-chart-urgent]", `${Number(frame.city.urgent_drivers).toLocaleString()} drivers at safety limit`)
  setText(root, "[data-chart-demand]", `${Number(frame.city.requests_15m).toLocaleString()} requests / 15 min`)
}
function render(state) {
  const frame = currentFrame(state)
  if (!frame) return
  const root = state.root
  const frames = displayFrames(state)
  if (!state.selectedZone) state.selectedZone = frame.zones?.[0]?.id
  setText(root, "[data-range]", state.data.range_label)
  setText(root, "[data-time]", frame.time_label)
  setText(root, "[data-decision-time]", state.data.decision_time_label)
  setText(root, "[data-status]", state.running ? "Playing" : frame.status)
  setText(root, "[data-limit-copy]", `$${Number(state.data.presentation_limits?.budget_usd || 0).toLocaleString()} budget / $${Number(state.data.presentation_limits?.support_per_driver_usd || 0).toFixed(2)} support per driver`)
  q(root, "[data-progress]").style.width = `${(state.index / Math.max(1, frames.length - 1)) * 100}%`
  setText(root, "[data-play-label]", state.running ? "Pause" : "Play")
  q(root, '[data-action="next"]').disabled = state.running || !canAdvance(state)
  q(root, '[data-action="play"]').disabled = !state.running && !canAdvance(state)
  tweenNumber(q(root, "[data-kpi-urgent]"), Number(frame.city.urgent_drivers || 0))
  const atDecision = state.index >= (state.data.pre_decision || []).length - 1
  const covered = Number(frame.city.covered_drivers || 0), required = Number(frame.city.required_drivers || 0)
  setText(root, "[data-kpi-coverage]", atDecision ? `${covered.toLocaleString()} / ${required.toLocaleString()}` : "Monitoring")
  setText(root, "[data-kpi-coverage-note]", atDecision ? (covered >= required ? "All covered" : `${Math.max(0, required-covered).toLocaleString()} still uncovered`) : "Available at decision time")
  const budget = frame.city.budget_remaining_usd
  setText(root, "[data-kpi-budget]", atDecision && budget != null ? `${budget < 0 ? "−" : ""}$${Math.abs(Number(budget)).toLocaleString()}` : "—")
  renderMap(state, frame)
  const zone = selectedZone(state, frame)
  renderDecision(state, frame, zone)
  renderTrend(state, frame)
}
function mount(root, data) {
  const state = { root, data, index:0, choice:null, selectedZone:null, speed:"normal", running:false, timer:null }
  q(root, '[data-action="play"]').onclick = () => { if (state.running) { stop(state); render(state) } else start(state) }
  q(root, '[data-action="next"]').onclick = () => step(state)
  q(root, '[data-action="reset"]').onclick = () => { stop(state); state.index=0; state.choice=null; state.selectedZone=null; render(state) }
  q(root, "[data-speed]").onchange = (event) => { state.speed=event.target.value; if (state.running) start(state) }
  root.querySelectorAll("[data-choice]").forEach((button) => {
    button.onclick = () => { stop(state); state.choice=button.dataset.choice; render(state) }
  })
  INSTANCES.set(root, state)
  render(state)
  return state
}
export default function(component) {
  const { data, parentElement } = component
  let state = INSTANCES.get(parentElement)
  if (!state) state = mount(parentElement, data)
  else { state.data = data; state.index = Math.min(state.index, sequence(state).length - 1); render(state) }
  return () => { stop(state); INSTANCES.delete(parentElement) }
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
    return value


def render_presentation_playback(
    timeline: dict[str, Any] | None = None,
    *,
    key: str = "operator-presentation-playback",
) -> None:
    """Mount one stable client-side playback surface."""
    # Register in the active Streamlit runtime. This is intentionally done at
    # the render boundary because AppTest can replace the runtime registry
    # between script runs while keeping imported Python modules cached.
    presentation = st.components.v2.component(
        "heatsafe_operator_presentation",
        html=_HTML,
        css=_CSS,
        js=_JS,
    )
    presentation(
        data=timeline or load_presentation_timeline(),
        key=key,
        width="stretch",
        height="content",
    )


__all__ = [
    "DEFAULT_TIMELINE",
    "load_presentation_timeline",
    "render_presentation_playback",
]
