from __future__ import annotations

from typing import Any

import streamlit as st

PLOTLY_LAYOUT: dict[str, Any] = {
    "template": "plotly_dark",
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"family": "Fira Sans, sans-serif", "size": 13, "color": "#cbd5e1"},
}

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600&family=Fira+Sans:wght@400;500;600;700&display=swap');
:root {
  --ops-bg:#020617; --ops-surface:#0f172a; --ops-surface-2:#1e293b;
  --ops-border:#334155; --ops-border-soft:#273449;
  --ops-text:#f8fafc; --ops-muted:#cbd5e1;
  --ops-heat:#fb923c; --ops-cool:#38bdf8; --ops-ok:#22c55e;
  --ops-warn:#fbbf24; --ops-crit:#f87171;
  --ops-radius-panel:12px; --ops-radius-card:9px;
}
html, body, [class*="css"] {
  font-family:"Fira Sans",-apple-system,system-ui,sans-serif;
  font-size:16px; font-variant-numeric:tabular-nums;
}
.stApp { background:var(--ops-bg); color:var(--ops-text); }
.block-container { max-width:1600px; padding:1rem 1.5rem 3rem; }
[data-testid="stSidebar"] { display:none; }
[data-testid="stHeader"] { background:transparent; }
::-webkit-scrollbar { width:6px; height:6px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:rgba(255,255,255,.12); border-radius:3px; }
h1,h2,h3,h4,p { letter-spacing:-.01em; }
h4 { color:var(--ops-text); font-size:1.28rem; line-height:1.35; font-weight:750; }
.ops-brand { display:flex; align-items:center; gap:.7rem; min-height:42px; }
.ops-mark {
  width:30px; height:30px; display:grid; place-items:center; border-radius:7px;
  color:#1b1712; font-weight:800; background:linear-gradient(145deg,#f0a34c,#dc6338);
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.18);
}
.ops-title { color:var(--ops-text); font-size:1.08rem; font-weight:700; }
.ops-subtitle { color:var(--ops-muted); font-size:.82rem; margin-top:2px; }
.ops-status-row { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin:.2rem 0 .65rem; }
.ops-playback-strip {
  display:flex; align-items:center; flex-wrap:wrap; gap:.55rem 1rem;
  margin:.25rem 0 .65rem; padding:.55rem .7rem;
  border:1px solid var(--ops-border); border-left:3px solid var(--ops-cool);
  border-radius:var(--ops-radius-card); background:var(--ops-surface);
  color:var(--ops-muted); font-size:.82rem; font-variant-numeric:tabular-nums;
}
.ops-playback-strip b { color:var(--ops-text); }
.ops-pill {
  display:inline-flex; align-items:center; gap:6px; padding:.25rem .55rem;
  border:1px solid var(--ops-border); border-radius:999px; color:var(--ops-muted);
  background:var(--ops-surface); font-size:.80rem; font-weight:500;
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
.ops-zone-head { padding:1.1rem 1.2rem; }
.ops-eyebrow { color:var(--ops-heat); font-size:.80rem; font-weight:700; text-transform:uppercase; letter-spacing:.075em; }
.ops-zone-name { color:var(--ops-text); font-size:1.7rem; font-weight:700; margin:.25rem 0 .8rem; }
.ops-zone-stats { display:grid; grid-template-columns:repeat(5,1fr); gap:1rem; }
.ops-stat-label, .ops-metric-label {
  color:var(--ops-muted); font-size:.80rem; font-weight:600;
  text-transform:uppercase; letter-spacing:.045em;
}
.ops-stat-value { color:var(--ops-text); font-size:1rem; font-weight:650; margin-top:.2rem; }
.tier-badge {
  display:inline-block; padding:.16rem .48rem; border-radius:6px;
  font-size:.76rem; font-weight:650; margin-left:.35rem; vertical-align:middle;
}
.tier-safe { background:rgba(52,211,153,.1); color:#6ee7b7; border:1px solid rgba(52,211,153,.2); }
.tier-caution { background:rgba(245,158,11,.1); color:#fbbf24; border:1px solid rgba(245,158,11,.25); }
.tier-danger { background:rgba(239,68,68,.1); color:#f87171; border:1px solid rgba(239,68,68,.25); }
.tier-extreme { background:rgba(220,38,38,.14); color:#fca5a5; border:1px solid rgba(220,38,38,.3); }
.ops-rec {
  border:1px solid rgba(233,134,58,.48); border-radius:var(--ops-radius-panel);
  background:linear-gradient(145deg,rgba(233,134,58,.075),rgba(33,31,28,.95)); padding:1.1rem;
}
.ops-rec-title { color:var(--ops-heat); font-size:.88rem; font-weight:750; text-transform:uppercase; letter-spacing:.065em; }
.ops-rec-name { color:var(--ops-text); font-size:1.35rem; font-weight:750; margin:.28rem 0; }
.ops-copy { color:var(--ops-muted); font-size:.88rem; line-height:1.55; }
.ops-metric-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:.9rem 0; }
.ops-metric { border:1px solid var(--ops-border-soft); border-radius:var(--ops-radius-card); background:rgba(24,22,19,.42); padding:.75rem .8rem; }
.ops-metric-value { color:var(--ops-text); font-size:1.12rem; font-weight:700; margin-top:.22rem; }
.ops-metric-value.cool { color:var(--ops-cool); }
.ops-metric-value.ok { color:var(--ops-ok); }
.ops-wave { display:flex; gap:6px; margin-top:.5rem; }
.ops-wave-item { flex:1; border:1px solid var(--ops-border-soft); border-top:3px solid var(--ops-heat); border-radius:var(--ops-radius-card); padding:.7rem; background:var(--ops-surface-2); }
.ops-wave-title { color:var(--ops-text); font-size:.84rem; font-weight:650; }
.ops-wave-meta { color:var(--ops-muted); font-size:.80rem; margin-top:.2rem; }
.ops-guard { display:flex; align-items:center; gap:6px; color:var(--ops-ok); font-size:.80rem; line-height:1.4; margin-top:.75rem; }
.ops-impact-row { padding:.8rem .9rem; border-bottom:1px solid var(--ops-border-soft); }
.ops-impact-top { display:flex; justify-content:space-between; gap:1rem; color:var(--ops-muted); font-size:.84rem; }
.ops-impact-top b { color:var(--ops-text); }
.ops-track { height:5px; border-radius:999px; background:var(--ops-surface-2); margin:.4rem 0 .2rem; overflow:hidden; }
.ops-fill { height:100%; border-radius:999px; background:var(--ops-ok); }
.ops-caption { color:var(--ops-muted); font-size:.80rem; line-height:1.4; }
.ops-execution-plan { border:1px solid var(--ops-border); border-radius:var(--ops-radius-panel); background:var(--ops-surface); overflow:hidden; margin:.65rem 0 .8rem; }
.ops-execution-row { display:grid; grid-template-columns:28px 1fr auto; align-items:center; gap:.7rem; padding:.72rem .8rem; border-bottom:1px solid var(--ops-border-soft); }
.ops-execution-row:last-child { border-bottom:0; }
.ops-execution-icon { width:28px; height:28px; display:grid; place-items:center; border-radius:8px; color:var(--ops-cool); background:rgba(114,203,208,.09); border:1px solid rgba(114,203,208,.22); font-size:.82rem; font-weight:700; }
.ops-execution-title { color:var(--ops-text); font-size:.96rem; font-weight:700; }
.ops-execution-meta { color:var(--ops-muted); font-size:.80rem; line-height:1.4; margin-top:.16rem; }
.ops-execution-state { color:var(--ops-muted); font-size:.76rem; font-weight:650; text-transform:uppercase; letter-spacing:.05em; padding:.2rem .42rem; border:1px solid var(--ops-border); border-radius:999px; }
.ops-execution-state.ready { color:var(--ops-ok); border-color:rgba(103,207,155,.3); }
.ops-execution-result { border:1px solid rgba(103,207,155,.35); border-radius:var(--ops-radius-panel); background:rgba(103,207,155,.07); padding:.8rem .9rem; margin:.65rem 0; }
.ops-execution-result strong { color:var(--ops-ok); font-size:1rem; }
.ops-execution-result div { color:var(--ops-muted); font-size:.82rem; line-height:1.5; margin-top:.22rem; }
div[data-testid="stButton"] button { border-color:var(--ops-border); background:var(--ops-surface); color:var(--ops-text); border-radius:var(--ops-radius-card); min-height:2.75rem; font-size:.84rem; text-align:left; }
div[data-testid="stButton"] button:hover { border-color:rgba(233,134,58,.65); color:var(--ops-text); }
div[data-testid="stButton"] button:focus-visible,
button:focus-visible, input:focus-visible, [role="slider"]:focus-visible {
  outline:2px solid var(--ops-cool); outline-offset:2px;
}
div[data-testid="stMetric"] { background:var(--ops-surface); border:1px solid var(--ops-border); padding:.8rem 1rem; border-radius:var(--ops-radius-panel); }
div[data-testid="stDataFrame"], div[data-testid="stChatMessage"] { border:1px solid var(--ops-border); border-radius:var(--ops-radius-panel); overflow:hidden; }
[data-testid="stExpander"] { border:1px solid var(--ops-border); border-radius:var(--ops-radius-panel); background:var(--ops-surface); }
[data-testid="stTabs"] button { color:var(--ops-muted); font-size:.95rem; font-weight:650; }
[data-testid="stTabs"] button[aria-selected="true"] { color:var(--ops-text); font-weight:750; }
[data-testid="stMarkdownContainer"] h4 { color:var(--ops-text); font-size:1.28rem; font-weight:750; padding-bottom:.45rem; border-bottom:1px solid var(--ops-border); }
[data-testid="stWidgetLabel"] p, [data-testid="stCheckbox"] label p,
[data-testid="stCaptionContainer"], [data-testid="stMarkdownContainer"] li { color:var(--ops-muted); font-size:.84rem; line-height:1.5; }
[data-baseweb="input"] input { font-size:1rem; }
div[data-testid="stNumberInput"] label p { color:var(--ops-muted); font-size:.80rem; font-weight:650; text-transform:uppercase; letter-spacing:.04em; }
div[data-testid="stNumberInput"] [data-baseweb="input"] { border-color:var(--ops-border); border-radius:var(--ops-radius-card); background:var(--ops-surface-2); }
hr { border-color:var(--ops-border)!important; }
@media (prefers-reduced-motion:reduce) {
  *, *::before, *::after {
    scroll-behavior:auto!important; animation-duration:.01ms!important;
    animation-iteration-count:1!important; transition-duration:.01ms!important;
  }
}
@media (max-width:900px) {
  .block-container { padding-left:1rem; padding-right:1rem; }
  .ops-zone-stats { grid-template-columns:repeat(2,1fr); }
  .ops-metric-grid { grid-template-columns:repeat(2,1fr); }
  .ops-wave { flex-direction:column; }
}
@media (max-width:600px) {
  .ops-zone-name { font-size:1.4rem; }
  .ops-zone-stats, .ops-metric-grid { grid-template-columns:1fr; }
  .ops-execution-row { grid-template-columns:28px 1fr; }
  .ops-execution-state { grid-column:2; justify-self:start; }
}
</style>
"""


def render_styles() -> None:
    """Render the HeatSafe warm operations-console design system once per page."""
    st.markdown(_CSS, unsafe_allow_html=True)


__all__ = ["PLOTLY_LAYOUT", "render_styles"]
