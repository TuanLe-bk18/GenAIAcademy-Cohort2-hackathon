"""Bounded, one-view-at-a-time evidence rendering."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .view_models import OperatorEvidenceSummary, OperatorTable

EVIDENCE_VIEWS = ("Areas", "Drivers", "History")


def _render_table(table: OperatorTable, *, key: str) -> None:
    if not table.rows:
        st.caption("No records are available for this view.")
        return
    st.dataframe(
        pd.DataFrame(table.as_records(), columns=table.columns),
        hide_index=True,
        width="stretch",
        key=key,
    )


def render_evidence(
    evidence: OperatorEvidenceSummary,
    *,
    selected_view: str | None = None,
    key_prefix: str = "operator-evidence",
) -> str:
    """Render only one bounded evidence table and return its selected name."""
    st.subheader("Evidence & history")
    chosen = st.segmented_control(
        "Evidence view",
        EVIDENCE_VIEWS,
        default=selected_view if selected_view in EVIDENCE_VIEWS else "Areas",
        key=f"{key_prefix}:selector",
    )
    active = chosen if chosen in EVIDENCE_VIEWS else "Areas"
    if active == "Areas":
        _render_table(evidence.areas, key=f"{key_prefix}:areas")
    elif active == "Drivers":
        _render_table(evidence.drivers, key=f"{key_prefix}:drivers")
    else:
        _render_table(evidence.history, key=f"{key_prefix}:history")
    return active


__all__ = ["EVIDENCE_VIEWS", "render_evidence"]
