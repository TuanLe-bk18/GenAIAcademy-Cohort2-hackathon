"""Public API for the HeatSafe operator-first Streamlit console.

Pure contracts are imported eagerly. Streamlit renderers are loaded lazily so tests and
non-UI orchestration can build views without importing the Streamlit runtime.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .outcomes import build_safepause_outcome_view
from .view_models import (
    MAX_AREAS,
    MAX_DRIVER_ROWS,
    MAX_HISTORY_ROWS,
    MAX_PORTFOLIO_OPTIONS,
    MAX_PRIORITY_AREAS,
    MAX_TIMING_OPTIONS,
    OperatorAreaView,
    OperatorCityKpis,
    OperatorConsoleView,
    OperatorDecisionInsightsView,
    OperatorEvidenceSummary,
    OperatorGuardrailView,
    OperatorKpiView,
    OperatorOutcomePoint,
    OperatorOutcomeView,
    OperatorPortfolioOptionView,
    OperatorRecommendationView,
    OperatorStressMetricView,
    OperatorTable,
    OperatorTimingOptionView,
    build_area_evidence_table,
    build_city_kpis,
    build_decision_insights,
    build_driver_evidence_table,
    build_history_evidence_table,
    build_operator_console_view,
    build_plan_evidence_table,
    build_recommendation_view,
)
from .vocabulary import (
    FORBIDDEN_OPERATOR_TERMS,
    HANOI_TZ,
    as_hanoi_time,
    format_currency_vnd,
    format_duration,
    format_freshness,
    format_hanoi_after,
    format_hanoi_range,
    format_hanoi_time,
    format_heat_state,
    format_mode_label,
    format_plan_status_label,
    format_readiness_label,
    format_risk_level,
    operator_copy_violations,
)

if TYPE_CHECKING:
    from .city_map import map_records, render_city_map
    from .decision_card import render_decision_card
    from .decision_insights import render_decision_insights
    from .evidence import build_replay_evidence_summary, render_evidence
    from .operations import (
        OperatorOperationsResult,
        render_city_kpis,
        render_operations,
        render_operator_header,
    )
    from .presentation import (
        OperatorDashboardResult,
        ReplayCursor,
        build_current_dashboard_payload,
        load_presentation_timeline,
        replay_cursor_from_component_state,
        replay_cursor_from_session_state,
        render_operator_dashboard,
        render_presentation_playback,
    )
    from .shell import OperatorConsoleResult, render_operator_console
    from .sidebar import OperatorSidebarResult, render_sidebar
    from .state_panels import (
        render_complete_state,
        render_loading_state,
        render_monitoring_state,
        render_no_safe_plan,
        render_recommendation_unavailable,
    )
    from .styles import render_styles

EVIDENCE_VIEWS = (
    "Area evidence",
    "SafePause plan",
    "Decision history",
)

_LAZY_EXPORTS = {
    "map_records": ("city_map", "map_records"),
    "render_city_map": ("city_map", "render_city_map"),
    "render_decision_card": ("decision_card", "render_decision_card"),
    "render_decision_insights": ("decision_insights", "render_decision_insights"),
    "build_replay_evidence_summary": (
        "evidence",
        "build_replay_evidence_summary",
    ),
    "render_evidence": ("evidence", "render_evidence"),
    "OperatorOperationsResult": ("operations", "OperatorOperationsResult"),
    "render_city_kpis": ("operations", "render_city_kpis"),
    "render_operations": ("operations", "render_operations"),
    "render_operator_header": ("operations", "render_operator_header"),
    "load_presentation_timeline": (
        "presentation",
        "load_presentation_timeline",
    ),
    "OperatorDashboardResult": ("presentation", "OperatorDashboardResult"),
    "ReplayCursor": ("presentation", "ReplayCursor"),
    "build_current_dashboard_payload": (
        "presentation",
        "build_current_dashboard_payload",
    ),
    "render_operator_dashboard": (
        "presentation",
        "render_operator_dashboard",
    ),
    "replay_cursor_from_component_state": (
        "presentation",
        "replay_cursor_from_component_state",
    ),
    "replay_cursor_from_session_state": (
        "presentation",
        "replay_cursor_from_session_state",
    ),
    "render_presentation_playback": (
        "presentation",
        "render_presentation_playback",
    ),
    "OperatorConsoleResult": ("shell", "OperatorConsoleResult"),
    "render_operator_console": ("shell", "render_operator_console"),
    "OperatorSidebarResult": ("sidebar", "OperatorSidebarResult"),
    "render_sidebar": ("sidebar", "render_sidebar"),
    "render_complete_state": ("state_panels", "render_complete_state"),
    "render_loading_state": ("state_panels", "render_loading_state"),
    "render_monitoring_state": ("state_panels", "render_monitoring_state"),
    "render_no_safe_plan": ("state_panels", "render_no_safe_plan"),
    "render_recommendation_unavailable": (
        "state_panels",
        "render_recommendation_unavailable",
    ),
    "render_styles": ("styles", "render_styles"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, symbol_name = target
    value = getattr(import_module(f"{__name__}.{module_name}"), symbol_name)
    globals()[name] = value
    return value


__all__ = [
    "EVIDENCE_VIEWS",
    "FORBIDDEN_OPERATOR_TERMS",
    "HANOI_TZ",
    "MAX_AREAS",
    "MAX_DRIVER_ROWS",
    "MAX_HISTORY_ROWS",
    "MAX_PORTFOLIO_OPTIONS",
    "MAX_PRIORITY_AREAS",
    "MAX_TIMING_OPTIONS",
    "OperatorAreaView",
    "OperatorCityKpis",
    "OperatorConsoleResult",
    "OperatorConsoleView",
    "OperatorDashboardResult",
    "OperatorDecisionInsightsView",
    "OperatorEvidenceSummary",
    "OperatorGuardrailView",
    "OperatorKpiView",
    "OperatorOperationsResult",
    "OperatorOutcomePoint",
    "OperatorOutcomeView",
    "OperatorPortfolioOptionView",
    "OperatorRecommendationView",
    "OperatorSidebarResult",
    "OperatorStressMetricView",
    "OperatorTable",
    "OperatorTimingOptionView",
    "ReplayCursor",
    "as_hanoi_time",
    "build_area_evidence_table",
    "build_city_kpis",
    "build_current_dashboard_payload",
    "build_decision_insights",
    "build_driver_evidence_table",
    "build_history_evidence_table",
    "build_operator_console_view",
    "build_plan_evidence_table",
    "build_recommendation_view",
    "build_replay_evidence_summary",
    "build_safepause_outcome_view",
    "format_currency_vnd",
    "format_duration",
    "format_freshness",
    "format_hanoi_after",
    "format_hanoi_range",
    "format_hanoi_time",
    "format_heat_state",
    "format_mode_label",
    "format_plan_status_label",
    "format_readiness_label",
    "format_risk_level",
    "load_presentation_timeline",
    "map_records",
    "operator_copy_violations",
    "render_city_kpis",
    "render_city_map",
    "render_complete_state",
    "render_decision_card",
    "render_decision_insights",
    "render_evidence",
    "render_loading_state",
    "render_monitoring_state",
    "render_no_safe_plan",
    "render_operations",
    "render_operator_console",
    "render_operator_dashboard",
    "render_operator_header",
    "render_presentation_playback",
    "replay_cursor_from_component_state",
    "replay_cursor_from_session_state",
    "render_recommendation_unavailable",
    "render_sidebar",
    "render_styles",
]
