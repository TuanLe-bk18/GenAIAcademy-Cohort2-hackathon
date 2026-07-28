"""Build the bounded, display-only timeline used by Simulation playback.

This script intentionally performs the expensive deterministic simulation and
planning work ahead of time. The Streamlit presentation surface only reads the
resulting JSON and never advances the engine or reruns the optimizer.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from heatsafe.currency import usd_to_vnd
from heatsafe.models import DecisionConstraints, PredictiveCityPlan
from heatsafe.production_mode import ProductionSession, build_production_evidence
from heatsafe.services.preventive_planning import (
    build_accelerated_forecast_input,
    build_predictive_city_plan,
    project_city_forecast,
)
from heatsafe.ui.operator_console.view_models import build_operator_console_view
from heatsafe.ui.operator_console.vocabulary import format_heat_state

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT
    / "data"
    / "scenarios"
    / "hanoi_heatwave_v1"
    / "operator_presentation_timeline.json"
)
CONSTRAINTS = DecisionConstraints(
    horizon_minutes=120,
    # Presentation-only limits keep all ten areas feasible so judges can inspect
    # both display branches. Current plan retains its independently editable,
    # authoritative limits.
    budget_cap_vnd=usd_to_vnd(500),
    sponsor_per_driver_vnd=usd_to_vnd(0.32),
)


def _json_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _decision_views(
    plan: PredictiveCityPlan,
    zones: tuple[Any, ...],
) -> dict[str, dict[str, Any]]:
    views: dict[str, dict[str, Any]] = {}
    for zone in zones:
        view = build_operator_console_view(
            plan,
            zones,
            CONSTRAINTS,
            selected_zone_id=zone.zone_id,
            optimization_evidence=plan.optimization_evidence,
            now=plan.evidence_lineage.observed_at,
        )
        views[zone.zone_id] = {
            "recommendation": _json_value(view.recommendation),
            "insights": _json_value(view.decision_insights),
        }
    return views


def _frame(
    result: Any,
    session: ProductionSession,
    *,
    branch: str,
    plan: PredictiveCityPlan | None,
    comparison_result: Any | None = None,
) -> dict[str, Any]:
    evidence = build_production_evidence(
        result,
        fixture=session.fixture,
        zones=session.zones,
        constraints=CONSTRAINTS,
    )
    projection_by_id = {item.zone_id: item for item in result.zones}
    selected_ids = set(plan.selected_zone_ids) if plan is not None else set()
    zones: list[dict[str, Any]] = []
    for snapshot in evidence.zones:
        projection = projection_by_id[snapshot.zone_id]
        zones.append(
            {
                "id": snapshot.zone_id,
                "name": snapshot.name,
                "latitude": snapshot.latitude,
                "longitude": snapshot.longitude,
                "heat_index_c": round(snapshot.heat_index_c, 1),
                "heat_state": format_heat_state(snapshot.heat_index_c),
                "active_drivers": snapshot.active_drivers,
                "urgent_drivers": snapshot.exposed_4h,
                "requests_15m": projection.requests_15m,
                "included": snapshot.zone_id in selected_ids,
            }
        )
    zones.sort(
        key=lambda item: (
            -int(item["urgent_drivers"]),
            -float(item["heat_index_c"]),
            str(item["name"]),
        )
    )
    urgent = sum(int(item["urgent_drivers"]) for item in zones)
    active = sum(int(item["active_drivers"]) for item in zones)
    requests = sum(int(item["requests_15m"]) for item in zones)
    comparison_urgent = None
    if comparison_result is not None:
        comparison_urgent = sum(
            item.exposed_4h for item in comparison_result.zones
        )
    covered = int(plan.mandatory_now_covered) if plan is not None else 0
    required = (
        covered + int(plan.mandatory_now_uncovered) if plan is not None else urgent
    )
    budget_remaining = (
        round((plan.budget_cap_vnd - plan.p95_reserved_cost_vnd) / 25_000)
        if plan is not None
        else None
    )
    return {
        "tick": result.tick_index,
        "time": result.simulation_time.isoformat(),
        "time_label": result.simulation_time.strftime("%H:%M"),
        "branch": branch,
        "status": (
            "Decision needed"
            if result.tick_index == session.window.decision_tick
            else "Playback complete"
            if result.tick_index == session.window.end_tick
            else "Monitoring"
        ),
        "city": {
            "urgent_drivers": urgent,
            "active_drivers": active,
            "requests_15m": requests,
            "average_heat_index_c": round(
                sum(float(item["heat_index_c"]) for item in zones) / len(zones),
                1,
            ),
            "covered_drivers": covered,
            "required_drivers": required,
            "budget_remaining_usd": budget_remaining,
            "comparison_urgent_drivers": comparison_urgent,
        },
        "zones": zones,
    }


def build_timeline() -> dict[str, Any]:
    session = ProductionSession.create()
    pre_decision: list[dict[str, Any]] = []
    plan: PredictiveCityPlan | None = None
    views: dict[str, dict[str, Any]] | None = None

    while True:
        if session.current_tick == session.window.decision_tick:
            forecast_input = build_accelerated_forecast_input(
                session.actual_result,
                fixture=session.fixture,
                zones=session.zones,
            )
            plan = build_predictive_city_plan(
                project_city_forecast(forecast_input),
                CONSTRAINTS,
            )
            assert session.decision_evidence is not None
            views = _decision_views(plan, session.decision_evidence.zones)
        pre_decision.append(
            _frame(
                session.actual_result,
                session,
                branch="PRE_DECISION",
                plan=plan,
            )
        )
        if session.current_tick >= session.window.decision_tick:
            break
        session.advance()

    assert plan is not None
    proposals = tuple(
        row.best_window.proposal
        for row in plan.rows
        if row.zone_id in plan.selected_zone_ids and row.best_window is not None
    )
    if proposals:
        session.choose("ACTIVATE", proposals=proposals)
    else:
        session.choose("CONTINUE")

    with_safepause: list[dict[str, Any]] = []
    without_safepause: list[dict[str, Any]] = []
    while session.current_tick < session.window.end_tick:
        session.advance()
        with_safepause.append(
            _frame(
                session.actual_result,
                session,
                branch="ACTIVATE",
                plan=plan,
                comparison_result=session.shadow_result,
            )
        )
        without_safepause.append(
            _frame(
                session.shadow_result,
                session,
                branch="CONTINUE",
                plan=plan,
                comparison_result=session.actual_result,
            )
        )

    first = pre_decision[0]
    last = with_safepause[-1] if with_safepause else pre_decision[-1]
    return {
        "schema_version": "operator-presentation-v1",
        "generated_from": {
            "scenario_version": session.window.scenario_version,
            "generator_version": session.window.generator_version,
            "seed": session.window.seed,
            "source_state_checksum": session.window.source_state_checksum,
        },
        "range_label": (
            f"{datetime.fromisoformat(first['time']).strftime('%H:%M')}"
            f"–{datetime.fromisoformat(last['time']).strftime('%H:%M')}"
        ),
        "decision_time_label": pre_decision[-1]["time_label"],
        "start_tick": session.window.start_tick,
        "decision_tick": session.window.decision_tick,
        "end_tick": session.window.end_tick,
        "plan_status": plan.status,
        "presentation_limits": {
            "budget_usd": 500,
            "support_per_driver_usd": 0.32,
        },
        "decision_views": views or {},
        "pre_decision": pre_decision,
        "branches": {
            "ACTIVATE": with_safepause,
            "CONTINUE": without_safepause,
        },
    }


def main() -> None:
    timeline = build_timeline()
    OUTPUT.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {OUTPUT.relative_to(ROOT)} with "
        f"{len(timeline['pre_decision'])} pre-decision frames and "
        f"{len(timeline['branches']['ACTIVATE'])} post-decision frames."
    )


if __name__ == "__main__":
    main()
