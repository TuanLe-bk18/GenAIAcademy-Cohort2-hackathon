"""Visual tokens for the operator console."""

from __future__ import annotations

from typing import Any

import streamlit as st

# Shared semantic chart colors are deliberately mid-tone so traces remain legible
# on both configured Streamlit themes.
CYAN = "#2786a6"
GREEN = "#43b66e"
AMBER = "#c69700"
RED = "#d94a3a"
GRAY = "#728079"
ORANGE = "#e67e32"

PLOTLY_LAYOUT: dict[str, Any] = {
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"family": "Inter, system-ui, sans-serif", "size": 13},
    "hoverlabel": {"font": {"family": "Inter, system-ui, sans-serif"}},
}

_CSS = """
<style>
:root {
  --operator-bg: var(--st-background-color);
  --operator-surface: var(--st-secondary-background-color);
  --operator-surface-raised: var(--st-gray-background-color);
  --operator-border: var(--st-border-color);
  --operator-text: var(--st-text-color);
  --operator-muted: var(--st-gray-text-color);
  --operator-primary: var(--st-primary-color);
  --operator-primary-bg: var(--st-green-background-color);
  --operator-safe: var(--st-green-text-color);
  --operator-warning: var(--st-yellow-text-color);
  --operator-critical: var(--st-red-text-color);
  --operator-unavailable: var(--st-gray-text-color);
  --operator-context: var(--st-blue-text-color);
}
.block-container { max-width: 1600px; padding-top: 1rem; }
[data-testid="stSidebar"] { border-right: 1px solid var(--operator-border); }
.operator-header {
  display: flex; align-items: center; justify-content: space-between; gap: 1rem;
  padding: .65rem .8rem; margin-bottom: .65rem; border: 1px solid var(--operator-border);
  border-radius: 12px; background: var(--operator-surface);
}
.operator-brand { color: var(--operator-text); font-size: 1.1rem; font-weight: 750; }
.operator-meta { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .4rem .8rem; color: var(--operator-muted); font-size: .82rem; }
.operator-status { color: var(--operator-safe); font-weight: 700; }
.operator-disclosure { color: var(--operator-muted); font-size: .78rem; margin: -.3rem 0 .65rem .15rem; }
.operator-area-title { color: var(--operator-text); font-size: 1.35rem; font-weight: 750; }
.operator-area-context { color: var(--operator-muted); font-size: .88rem; margin: .15rem 0 .8rem; }
.operator-recommendation {
  border-left: 3px solid var(--operator-primary); padding: .7rem .8rem;
  background: var(--operator-primary-bg); border-radius: 0 9px 9px 0; margin: .4rem 0 .75rem;
}
.operator-recommendation strong { color: var(--operator-text); font-size: 1.08rem; }
.operator-recommendation p { color: var(--operator-muted); font-size: .84rem; margin: .25rem 0 0; }
.operator-guard {
  display: flex; justify-content: space-between; gap: .8rem; padding: .52rem 0;
  border-bottom: 1px solid var(--operator-border); font-size: .84rem;
}
.operator-guard:last-child { border-bottom: 0; }
.operator-guard-label { color: var(--operator-muted); }
.operator-pass { color: var(--operator-safe); font-weight: 700; }
.operator-fail { color: var(--operator-critical); font-weight: 700; }
.operator-priority {
  display: flex; justify-content: space-between; gap: .8rem; color: var(--operator-muted);
  font-size: .82rem; padding: .25rem 0;
}
[data-testid="stMetric"] { border-color: var(--operator-border); background: var(--operator-surface); }
[data-testid="stMetric"] label { color: var(--operator-muted); }
[data-testid="stDataFrame"] { border: 1px solid var(--operator-border); border-radius: 10px; overflow: hidden; }
button:focus-visible, input:focus-visible, [role="button"]:focus-visible {
  outline: 2px solid var(--operator-primary) !important; outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .01ms !important; animation-iteration-count: 1 !important;
    transition-duration: .01ms !important; scroll-behavior: auto !important;
  }
}
@media (max-width: 1100px) {
  .operator-header { align-items: flex-start; flex-direction: column; }
  .operator-meta { justify-content: flex-start; }
}
</style>
"""


def render_styles() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


__all__ = [
    "AMBER",
    "CYAN",
    "GRAY",
    "GREEN",
    "ORANGE",
    "PLOTLY_LAYOUT",
    "RED",
    "render_styles",
]
