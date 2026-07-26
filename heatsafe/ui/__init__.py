"""Composable Streamlit UI for HeatSafe AI Ops."""

from .copilot_panel import create_copilot, render_copilot_panel
from .city_planner import (
    build_city_planner_view,
    build_unavailable_city_planner_view,
    city_table_rows,
    render_city_plan_actions,
    render_city_plan_copilot,
    render_city_planner,
)
from .decision_workspace import (
    render_business_impact,
    render_decision_workspace,
    render_execution,
    render_forecast,
    render_recommendation,
    render_zone_header,
)
from .evidence_tabs import (
    render_city_intelligence,
    render_driver_evidence,
    render_evidence_tabs,
    render_model_performance,
)
from .state import advance_refresh_token, build_constraints, initialize_state
from .replay import replay_run_label, replay_tick_time
from .production_mode import render_production_mode
from .styles import render_styles

__all__ = [
    "advance_refresh_token",
    "build_constraints",
    "build_city_planner_view",
    "build_unavailable_city_planner_view",
    "city_table_rows",
    "create_copilot",
    "initialize_state",
    "replay_run_label",
    "replay_tick_time",
    "render_business_impact",
    "render_city_intelligence",
    "render_city_plan_actions",
    "render_city_plan_copilot",
    "render_city_planner",
    "render_copilot_panel",
    "render_decision_workspace",
    "render_driver_evidence",
    "render_evidence_tabs",
    "render_execution",
    "render_forecast",
    "render_model_performance",
    "render_production_mode",
    "render_recommendation",
    "render_styles",
    "render_zone_header",
]
