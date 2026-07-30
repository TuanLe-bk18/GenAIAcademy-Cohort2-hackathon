"""Bounded, one-view-at-a-time evidence rendering."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd
import streamlit as st

from .view_models import OperatorEvidenceSummary, OperatorTable

EVIDENCE_VIEWS = ("Area evidence", "SafePause plan", "Decision history")
_VIEW_CAPTIONS = {
    "Area evidence": "Current heat, safety need, demand, and plan status by operating area.",
    "SafePause plan": "Only areas included in the reviewed plan, with the service and cost impact needed for approval.",
    "Decision history": "Operator choices and subsequent safety outcomes, newest first.",
}


def _render_table(table: OperatorTable, *, key: str) -> None:
    if not table.rows:
        st.caption("No records are available for this view.")
        return
    frame = pd.DataFrame(table.as_records(), columns=table.columns)
    first_column = str(table.columns[0])
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=min(440, 38 * len(frame) + 42),
        column_config={
            first_column: st.column_config.TextColumn(first_column, pinned=True),
        },
        key=key,
    )


def render_evidence(
    evidence: OperatorEvidenceSummary,
    *,
    selected_view: str | None = None,
    key_prefix: str = "operator-evidence",
) -> str:
    """Render only one bounded evidence table and return its selected name."""
    st.subheader("Evidence & History")
    selector_key = f"{key_prefix}:selector"
    if selector_key not in st.session_state:
        st.session_state[selector_key] = (
            selected_view if selected_view in EVIDENCE_VIEWS else "Area evidence"
        )
    chosen = st.segmented_control(
        "Evidence view",
        EVIDENCE_VIEWS,
        key=selector_key,
    )
    active = chosen if chosen in EVIDENCE_VIEWS else "Area evidence"
    st.caption(_VIEW_CAPTIONS[active])
    if active == "Area evidence":
        _render_table(evidence.areas, key=f"{key_prefix}:areas")
    elif active == "SafePause plan":
        _render_table(evidence.drivers, key=f"{key_prefix}:plan")
    else:
        _render_table(evidence.history, key=f"{key_prefix}:history")
    return active


def _replay_frame(timeline: Mapping[str, Any], cursor: object | None) -> Mapping[str, Any]:
    pre_decision = timeline.get("pre_decision")
    if not isinstance(pre_decision, list) or not pre_decision:
        return {}
    tick = getattr(cursor, "tick", pre_decision[0].get("tick"))
    branch = getattr(cursor, "branch", "PRE_DECISION")
    frames = list(pre_decision)
    branches = timeline.get("branches")
    if branch in {"ACTIVATE", "CONTINUE"} and isinstance(branches, Mapping):
        branch_frames = branches.get(branch)
        if isinstance(branch_frames, list):
            frames.extend(branch_frames)
    return next(
        (
            item
            for item in frames
            if isinstance(item, Mapping) and item.get("tick") == tick
        ),
        pre_decision[0],
    )


def _replay_area_table(
    frame: Mapping[str, Any],
    selected_zone_id: str | None,
    *,
    decision_ready: bool,
) -> OperatorTable:
    zones = frame.get("zones")
    if not isinstance(zones, list):
        zones = []
    ordered = sorted(
        (zone for zone in zones if isinstance(zone, Mapping)),
        key=lambda zone: (zone.get("id") != selected_zone_id, zone.get("name", "")),
    )
    columns = (
        "Area",
        "Heat",
        "Need now",
        "Need by 120 min",
        "Demand (30 min)",
        "Plan status",
    )
    rows = tuple(
        (
            str(zone.get("name", "—")),
            f"{str(zone.get('heat_state', 'Updating')).title()} · {float(zone.get('heat_index_c', 0)):.1f}°C",
            max(0, int(zone.get("urgent_drivers", 0) or 0)),
            max(0, int(zone.get("needs_protection_120m", 0) or 0)),
            max(0, int(zone.get("forecast_requests_30m", 0) or 0)),
            (
                "Monitoring"
                if not decision_ready
                else "Included"
                if zone.get("included")
                else "Candidate"
                if zone.get("portfolio_status") == "SELECTED"
                else "Monitoring"
            ),
        )
        for zone in ordered[:10]
    )
    return OperatorTable(columns=columns, rows=rows)


def _metric_label(view: Mapping[str, Any], label: str) -> str:
    insights = view.get("insights")
    metrics = insights.get("stress_metrics") if isinstance(insights, Mapping) else None
    if not isinstance(metrics, list):
        return "Updating"
    metric = next(
        (
            item
            for item in metrics
            if isinstance(item, Mapping) and item.get("label") == label
        ),
        None,
    )
    return str(metric.get("high_demand_label", "Updating")) if metric else "Updating"


def _replay_plan_table(
    timeline: Mapping[str, Any], frame: Mapping[str, Any]
) -> OperatorTable:
    zones = frame.get("zones")
    decision_views = timeline.get("decision_views")
    if not isinstance(zones, list) or not isinstance(decision_views, Mapping):
        zones = []
        decision_views = {}
    columns = (
        "Area",
        "Drivers protected",
        "Start",
        "Fulfillment impact",
        "ETA impact",
        "Reserved cost",
    )
    decision_tick = timeline.get("decision_tick")
    frame_tick = frame.get("tick")
    if (
        isinstance(decision_tick, int)
        and isinstance(frame_tick, int)
        and frame_tick < decision_tick
    ):
        return OperatorTable(columns=columns, rows=())
    rows: list[tuple[object, ...]] = []
    for zone in zones:
        if not isinstance(zone, Mapping) or not (
            zone.get("included") or zone.get("portfolio_status") == "SELECTED"
        ):
            continue
        view = decision_views.get(zone.get("id"))
        if not isinstance(view, Mapping):
            continue
        recommendation = view.get("recommendation")
        if not isinstance(recommendation, Mapping):
            continue
        reserve = zone.get("high_demand_reserved_cost_usd")
        rows.append(
            (
                str(zone.get("name", "—")),
                max(0, int(recommendation.get("driver_count", 0) or 0)),
                str(recommendation.get("start_time_label", "—")),
                _metric_label(view, "Orders completed"),
                _metric_label(view, "Expected pickup delay"),
                f"${float(reserve):,.2f}" if reserve is not None else "Updating",
            )
        )
    return OperatorTable(columns=columns, rows=tuple(rows[:10]))


def _replay_history_table(
    timeline: Mapping[str, Any], cursor: object | None
) -> OperatorTable:
    columns = ("Time", "Action", "Drivers", "Safety outcome", "Result")
    branch = getattr(cursor, "branch", "PRE_DECISION")
    current_tick = getattr(cursor, "tick", -1)
    if branch == "CONTINUE":
        return OperatorTable(
            columns=columns,
            rows=((str(timeline.get("decision_time_label", "—")), "Continue monitoring", 0, "Monitoring", "Recorded"),),
        )
    if branch != "ACTIVATE":
        return OperatorTable(columns=columns, rows=())
    events = timeline.get("rolling_events")
    if not isinstance(events, list):
        events = []
    rows = []
    for event in events:
        if not isinstance(event, Mapping) or int(event.get("tick", -1)) > current_tick:
            continue
        outcome = str(event.get("outcome", "Recorded")).replace("_", " ").title()
        mandatory_covered = max(
            0, int(event.get("mandatory_covered", 0) or 0)
        )
        mandatory_uncovered = max(
            0, int(event.get("mandatory_uncovered", 0) or 0)
        )
        preventive = max(0, int(event.get("new_preventive_count", 0) or 0))
        safety_outcome = (
            f"No mandatory gap · {preventive:,} preventive"
            if mandatory_covered == 0 and mandatory_uncovered == 0
            else f"{mandatory_covered:,} covered · {mandatory_uncovered:,} uncovered"
        )
        rows.append(
            (
                str(event.get("time_label", "—")),
                "Activate SafePause" if event.get("outcome") == "ACTIVATED" else "Update SafePause",
                max(0, int(event.get("new_driver_count", 0) or 0)),
                safety_outcome,
                outcome,
            )
        )
    return OperatorTable(columns=columns, rows=tuple(rows[-10:][::-1]))


def build_replay_evidence_summary(
    timeline: Mapping[str, Any], cursor: object | None
) -> OperatorEvidenceSummary:
    """Build bounded evidence tables for the persisted browser replay position."""
    frame = _replay_frame(timeline, cursor)
    decision_tick = timeline.get("decision_tick")
    frame_tick = frame.get("tick")
    decision_ready = bool(
        isinstance(decision_tick, int)
        and isinstance(frame_tick, int)
        and frame_tick >= decision_tick
    )
    return OperatorEvidenceSummary(
        areas=_replay_area_table(
            frame,
            getattr(cursor, "selected_zone_id", None),
            decision_ready=decision_ready,
        ),
        drivers=_replay_plan_table(timeline, frame),
        history=_replay_history_table(timeline, cursor),
    )


__all__ = [
    "EVIDENCE_VIEWS",
    "build_replay_evidence_summary",
    "render_evidence",
]
