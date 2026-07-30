"""Visual tokens for the operator console."""

from __future__ import annotations

from typing import Any

import streamlit as st

# Shared semantic chart colors are deliberately mid-tone so traces remain legible
# on both configured Streamlit themes.
CYAN = "#4da7b3"
GREEN = "#53c68c"
AMBER = "#f0a35a"
RED = "#ef6a5b"
GRAY = "#728079"
ORANGE = "#f0a35a"

PLOTLY_LAYOUT: dict[str, Any] = {
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"family": "Inter, system-ui, sans-serif", "size": 15},
    "hoverlabel": {"font": {"family": "Inter, system-ui, sans-serif", "size": 14}},
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
[data-testid="stHeader"] {
  height: 2.5rem;
  background: transparent;
}
[data-testid="stHeader"],
[data-testid="stHeader"] * {
  pointer-events: none !important;
}
[data-testid="stHeader"] button,
[data-testid="stHeader"] a,
[data-testid="stHeader"] [role="button"],
[data-testid="stHeader"] [data-testid="stStatusWidget"],
[data-testid="stHeader"] [data-testid="stStatusWidget"] * {
  pointer-events: auto !important;
  cursor: pointer;
}
[data-testid="stMain"] .block-container {
  max-width: 1600px; padding-top: .35rem !important; padding-bottom: 2rem;
}
[data-testid="stSidebarHeader"] {
  position: absolute;
  inset: 0 0 auto;
  height: 2.5rem !important;
  margin-bottom: 0 !important;
  z-index: 1;
}
[data-testid="stSidebarHeader"],
[data-testid="stSidebarHeader"] * {
  pointer-events: none !important;
}
[data-testid="stSidebarHeader"] button,
[data-testid="stSidebarHeader"] a,
[data-testid="stSidebarHeader"] [role="button"] {
  pointer-events: auto !important;
  cursor: pointer;
}
[data-testid="stSidebarUserContent"] {
  height: 100%;
  padding-top: .35rem !important;
  padding-bottom: .5rem !important;
}
[data-testid="stSidebarUserContent"] .block-container {
  padding-bottom: .5rem !important;
}
[data-testid="stSidebar"] { border-right: 1px solid var(--operator-border); }
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"]:has(.st-key-gemini-copilot-shell),
[data-testid="stSidebar"] .st-key-gemini-copilot-shell {
  height: calc(100dvh - 14rem) !important;
  max-height: calc(100dvh - 14rem) !important;
  min-height: 420px;
  overflow: hidden !important;
}
[data-testid="stSidebar"] .st-key-gemini-copilot-shell,
[data-testid="stSidebar"] .st-key-gemini-copilot-shell > [data-testid="stVerticalBlock"] {
  display: grid !important;
  grid-template-rows: auto minmax(0, 1fr) auto;
  min-height: 0 !important;
  overflow: hidden !important;
}
[data-testid="stSidebar"] .st-key-gemini-copilot-shell > [data-testid="stVerticalBlock"] {
  height: 100% !important;
  max-height: 100% !important;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"]:has(.st-key-gemini-copilot-header),
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"]:has(.st-key-gemini-copilot-composer) {
  min-height: 0;
  overflow: visible;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"]:has(.st-key-gemini-copilot-history) {
  height: 100% !important;
  max-height: 100% !important;
  min-height: 0 !important;
  overflow: hidden !important;
  border-top: 1px solid var(--operator-border);
  margin-top: .35rem;
  padding-top: .25rem;
}
[data-testid="stSidebar"] .st-key-gemini-copilot-history {
  height: 100% !important;
  max-height: 100% !important;
  min-height: 0 !important;
  overflow-y: auto !important;
  overflow-x: hidden !important;
  border: 1px solid var(--operator-border); border-radius: 14px;
  background: color-mix(in srgb, var(--operator-surface) 88%, transparent);
  padding: .2rem .45rem;
}
[data-testid="stSidebar"] .st-key-gemini-copilot-history [data-testid="stChatMessage"] {
  border: 0; background: transparent; padding: .65rem .15rem;
}
[data-testid="stSidebar"] .st-key-gemini-copilot-history [data-testid="stChatMessage"] + [data-testid="stChatMessage"] {
  border-top: 1px solid var(--operator-border);
}
[data-testid="stSidebar"] .st-key-gemini-copilot-history [data-testid="stMarkdownContainer"] {
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
}
[data-testid="stSidebar"] .st-key-gemini-copilot-history [data-testid="stMarkdownContainer"] table {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  table-layout: fixed;
  font-size: .78rem;
}
[data-testid="stSidebar"] .st-key-gemini-copilot-history [data-testid="stMarkdownContainer"] th,
[data-testid="stSidebar"] .st-key-gemini-copilot-history [data-testid="stMarkdownContainer"] td {
  min-width: 0;
  padding: .42rem .35rem;
  white-space: normal;
  overflow-wrap: normal;
  word-break: normal;
  hyphens: none;
  line-height: 1.35;
  vertical-align: top;
}
[data-testid="stSidebar"] .st-key-gemini-copilot-history [data-testid="stMarkdownContainer"] th {
  font-size: .72rem;
  font-weight: 700;
}
[data-testid="stSidebar"] .st-key-gemini-copilot-history [data-testid="stMarkdownContainer"] table:has(th:nth-child(3):last-child) th:nth-child(1) { width: 30%; }
[data-testid="stSidebar"] .st-key-gemini-copilot-history [data-testid="stMarkdownContainer"] table:has(th:nth-child(3):last-child) th:nth-child(2) { width: 28%; }
[data-testid="stSidebar"] .st-key-gemini-copilot-history [data-testid="stMarkdownContainer"] table:has(th:nth-child(3):last-child) th:nth-child(3) { width: 42%; }
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"]:has(.st-key-gemini-copilot-composer),
[data-testid="stSidebar"] .st-key-gemini-copilot-composer {
  align-self: stretch;
  min-height: 0;
  z-index: 2;
  padding: .35rem 0 .99rem;
  background: var(--operator-bg);
}
[data-testid="stSidebar"] [data-testid="stChatInput"] {
  border: 1px solid var(--operator-border); border-radius: 14px;
  background: var(--operator-surface);
}
[data-testid="stSidebar"] [data-testid="stChatInput"]:focus-within {
  border-color: var(--operator-primary);
}
[data-testid="stSidebar"] [data-testid="stPills"] [role="option"] {
  border-color: var(--operator-border); background: var(--operator-surface);
}
.operator-ai-thinking {
  display: inline-flex; align-items: baseline; gap: .3rem; min-height: 1.4rem;
  color: var(--operator-muted); font-size: .86rem;
}
.operator-ai-thinking-dots { display: inline-flex; letter-spacing: .08rem; }
.operator-ai-thinking-dots span {
  animation: operator-thinking-dot 1.2s infinite ease-in-out;
  opacity: .25;
}
.operator-ai-thinking-dots span:nth-child(2) { animation-delay: .16s; }
.operator-ai-thinking-dots span:nth-child(3) { animation-delay: .32s; }
@keyframes operator-thinking-dot {
  0%, 70%, 100% { opacity: .25; transform: translateY(0); }
  35% { opacity: 1; transform: translateY(-2px); }
}
.operator-sidebar-copy { padding-top: 0; }
.operator-sidebar-brand {
  color: var(--operator-text); font-size: 1.35rem; font-weight: 800;
  letter-spacing: -.03em; line-height: 1.1; margin-bottom: 4px; white-space: nowrap;
}
.operator-brand-heat { color: #ff8c00; }
.operator-brand-safe { color: #00e5ff; }
.operator-brand-text { color: var(--operator-text); }
.operator-sidebar-tagline {
  color: var(--operator-muted); font-size: .65em; letter-spacing: .5px;
  text-transform: uppercase;
}
.operator-header {
  display: flex; align-items: center; justify-content: space-between; gap: 1rem;
  padding: .65rem .8rem; margin-bottom: .65rem; border: 1px solid var(--operator-border);
  border-radius: 12px; background: var(--operator-surface);
}
.operator-brand { color: var(--operator-text); font-size: 1.25rem; font-weight: 750; letter-spacing: -.01em; }
.operator-meta { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .4rem .8rem; color: var(--operator-muted); font-size: .9rem; }
.operator-status { color: var(--operator-safe); font-weight: 700; }
.operator-disclosure { color: var(--operator-muted); font-size: .9rem; margin: -.3rem 0 .75rem .15rem; }
.operator-area-title { color: var(--operator-text); font-size: 1.5rem; font-weight: 750; overflow-wrap: anywhere; }
.operator-area-context { color: var(--operator-muted); font-size: .95rem; margin: .15rem 0 .8rem; overflow-wrap: anywhere; }
.operator-recommendation {
  border-left: 3px solid var(--operator-primary); padding: .7rem .8rem;
  background: var(--operator-primary-bg); border-radius: 0 9px 9px 0; margin: .4rem 0 .75rem;
}
.operator-recommendation strong { color: var(--operator-text); font-size: 1.2rem; overflow-wrap: anywhere; }
.operator-recommendation p { color: var(--operator-muted); font-size: .96rem; margin: .25rem 0 0; overflow-wrap: anywhere; }
.operator-guard {
  display: flex; justify-content: space-between; gap: .8rem; padding: .52rem 0;
  border-bottom: 1px solid var(--operator-border); font-size: .94rem; align-items: flex-start;
}
.operator-guard:last-child { border-bottom: 0; }
.operator-guard-label { color: var(--operator-muted); }
.operator-pass { color: var(--operator-safe); font-weight: 700; }
.operator-fail { color: var(--operator-critical); font-weight: 700; }
.operator-priority {
  display: flex; justify-content: space-between; gap: .8rem; color: var(--operator-muted);
  font-size: .82rem; padding: .25rem 0;
}
[data-testid="stMetric"] { border-color: var(--operator-border); background: var(--operator-surface); min-height: 118px; }
[data-testid="stMetric"] label { color: var(--operator-muted); }
[data-testid="stMetricValue"] { font-size: clamp(1.75rem, 2vw, 2.2rem); overflow-wrap: anywhere; }
[data-testid="stMetricDelta"] { font-size: .95rem; white-space: normal; overflow-wrap: anywhere; }
[data-testid="stDataFrame"] { border: 1px solid var(--operator-border); border-radius: 10px; overflow: hidden; }
.st-key-operator-console-surface,
.st-key-operator-console-evidence-selector,
.st-key-operator-console-replay-evidence-selector {
  position: relative;
  z-index: 5;
  pointer-events: auto !important;
}
button:not(:disabled),
[role="button"]:not([aria-disabled="true"]),
.st-key-operator-console-surface button,
.st-key-operator-console-surface [role="radio"],
.st-key-operator-console-evidence-selector button,
.st-key-operator-console-evidence-selector [role="radio"],
.st-key-operator-console-replay-evidence-selector button,
.st-key-operator-console-replay-evidence-selector [role="radio"] {
  cursor: pointer !important;
  pointer-events: auto !important;
}
button:disabled,
[role="button"][aria-disabled="true"] { cursor: not-allowed !important; }
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
@media (max-width: 700px) {
  [data-testid="stMain"] .block-container { padding-left: .8rem; padding-right: .8rem; }
  .operator-header { padding: .75rem; }
  .operator-guard { flex-direction: column; gap: .2rem; }
  [data-testid="stMetric"] { min-height: 104px; }
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
