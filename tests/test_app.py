from __future__ import annotations

import json
import os
import unittest
from typing import ClassVar
from unittest import mock

from streamlit.testing.v1 import AppTest

from heatsafe.ui.operator_console.vocabulary import operator_copy_violations


ENVIRONMENT = {
    "HEATSAFE_MODE": "snapshot",
    "HEATSAFE_ENABLE_AI": "0",
    "HEATSAFE_SCENARIO": "heatwave",
}


class HeatSafeOperatorAppTests(unittest.TestCase):
    KPI_LABELS: ClassVar[list[str]] = [
        "Drivers needing a break now",
        "Safety coverage",
        "Budget remaining after this plan",
    ]
    AREA_COLUMNS: ClassVar[set[str]] = {
        "Area",
        "Heat",
        "Need a break now",
        "Recommended start",
        "Plan status",
    }

    def run_app(self, timeout: int = 60) -> AppTest:
        with mock.patch.dict(os.environ, ENVIRONMENT, clear=False):
            app = AppTest.from_file("app.py", default_timeout=timeout)
            app.run()
        self.assertFalse(app.exception)
        return app

    @staticmethod
    def widget(items, label: str):
        return next(item for item in items if item.label == label)

    @staticmethod
    def rendered_copy(app: AppTest) -> tuple[str, ...]:
        values: list[str] = []
        for collection in (
            app.markdown,
            app.caption,
            app.info,
            app.warning,
            app.error,
            app.success,
        ):
            values.extend(str(item.value) for item in collection)
        values.extend(item.label for item in app.metric)
        values.extend(str(item.value) for item in app.metric)
        values.extend(item.label for item in app.button)
        return tuple(values)

    def assert_operator_vocabulary(self, app: AppTest) -> None:
        self.assertEqual(operator_copy_violations(self.rendered_copy(app)), ())

    def test_operations_surface_is_map_first_and_obeys_density_contract(self):
        app = self.run_app()

        self.assertEqual(len(app.metric), 0)
        self.assertEqual(len(app.dataframe), 0)
        self.assertEqual(len(app.get("plotly_chart")), 0)
        self.assertEqual(
            self.widget(app.segmented_control, "Console view").value,
            "Operations",
        )
        components = app.get("bidi_component")
        self.assertEqual(len(components), 1)
        component = components[0].proto
        payload = json.loads(component.json)
        self.assertEqual(payload["mode"], "current")
        self.assertEqual(payload["schema_version"], "operator-dashboard-v1")
        self.assertEqual(
            [item["label"] for item in payload["current_kpis"]],
            self.KPI_LABELS,
        )
        self.assertEqual(len(payload["pre_decision"][0]["zones"]), 10)
        self.assertIn("Hanoi operating areas", component.html_content)
        self.assertIn("Why this plan", component.html_content)
        self.assertIn("data-map-basemap", component.html_content)
        self.assertIn("© OpenStreetMap · © CARTO", component.html_content)
        self.assertIn("basemaps.cartocdn.com", component.js_content)
        self.assertIn("renderBasemap(state, projection)", component.js_content)
        self.assertIn('data-insight-scope', component.html_content)
        self.assertIn('data-choice="ACTIVATE"', component.html_content)
        self.assertEqual(len(app.sidebar.chat_input), 1)
        self.assertEqual(
            app.sidebar.chat_input[0].placeholder,
            "Ask Gemini Copilot...",
        )
        self.assertIsNotNone(
            self.widget(app.sidebar.get("button_group"), "Suggested prompts")
        )
        self.assertTrue(
            any(
                "Gemini Copilot" in str(item.value)
                for item in app.sidebar.markdown
            )
        )
        self.assert_operator_vocabulary(app)

        initial_token = app.session_state["refresh_token"]
        self.widget(app.button, "Refresh conditions").click()
        app.run()
        self.assertFalse(app.exception)
        self.assertNotEqual(app.session_state["refresh_token"], initial_token)

    def test_evidence_surface_renders_only_bounded_area_table(self):
        app = self.run_app()
        self.widget(app.segmented_control, "Console view").set_value(
            "Evidence & history"
        )
        app.run()

        self.assertFalse(app.exception)
        self.assertEqual(len(app.metric), 0)
        self.assertEqual(len(app.get("plotly_chart")), 0)
        self.assertEqual(len(app.dataframe), 1)
        frame = app.dataframe[0].value
        self.assertEqual(len(frame), 10)
        self.assertEqual(len(frame.columns), 6)
        self.assertTrue(self.AREA_COLUMNS <= set(frame.columns))
        self.assertEqual(
            self.widget(app.segmented_control, "Evidence view").value,
            "Areas",
        )
        self.assert_operator_vocabulary(app)

    def test_only_selected_optimization_story_is_rendered(self):
        app = self.run_app()
        component = app.get("bidi_component")[0].proto
        payload = json.loads(component.json)
        selected = next(
            zone for zone in payload["pre_decision"][0]["zones"] if zone["selected"]
        )
        insights = payload["decision_views"][selected["id"]]["insights"]
        self.assertGreater(len(insights["timing_options"]), 0)
        self.assertGreater(len(insights["portfolio_options"]), 0)
        self.assertGreater(len(insights["stress_metrics"]), 0)
        self.assertIn("Selected district", component.html_content)
        self.assertIn("All districts", component.html_content)
        self.assertIn("renderAllDistrictsInsight", component.js_content)
        self.assert_operator_vocabulary(app)

    def test_tight_budget_fails_closed_with_plain_guidance(self):
        app = self.run_app()
        self.widget(app.number_input, "Budget limit ($)").set_value(0.0)
        self.widget(app.button, "Apply limits").click()
        app.run()

        self.assertFalse(app.exception)
        payload = json.loads(app.get("bidi_component")[0].proto.json)
        selected = next(
            zone for zone in payload["pre_decision"][0]["zones"] if zone["selected"]
        )
        recommendation = payload["decision_views"][selected["id"]]["recommendation"]
        self.assertFalse(recommendation["can_activate"])
        self.assertNotEqual(recommendation["state"], "ready")
        self.assertTrue(payload["current_kpis"][1]["value"].startswith("0 /"))
        self.assert_operator_vocabulary(app)

    def test_simulation_playback_is_precomputed_and_browser_local(self):
        app = self.run_app()
        current_component = app.get("bidi_component")[0].proto
        current_html = current_component.html_content
        current_js = current_component.js_content
        self.widget(app.segmented_control, "Mode").set_value(
            "accelerated-production"
        )
        app.run()

        self.assertFalse(app.exception)
        self.assertNotIn("production_window_session", app.session_state)
        components = app.get("bidi_component")
        self.assertEqual(len(components), 1)
        component = components[0].proto
        self.assertEqual(
            component.component_name,
            "heatsafe_operator_presentation",
        )
        self.assertEqual(component.html_content, current_html)
        self.assertEqual(component.js_content, current_js)
        timeline = json.loads(component.json)
        self.assertEqual(timeline["schema_version"], "operator-presentation-v1")
        self.assertEqual(timeline["mode"], "replay")
        self.assertEqual(timeline["range_label"], "09:15–13:15")
        self.assertEqual(timeline["decision_time_label"], "10:00")
        self.assertEqual(len(timeline["pre_decision"]), 4)
        self.assertEqual(len(timeline["branches"]["ACTIVATE"]), 13)
        self.assertEqual(len(timeline["branches"]["CONTINUE"]), 13)
        self.assertEqual(timeline["plan_status"], "READY")
        self.assertIn("Next 15 min", component.html_content)
        self.assertIn("Why this plan", component.html_content)
        self.assertIn("data-kpi-preventive-card", component.html_content)
        self.assertIn("frame.city.coverage?.mandatory", component.js_content)
        self.assertIn('data-action="play"', component.html_content)
        self.assertIn("setInterval", component.js_content)
        self.assertIn('setStateValue?.("replay_state"', component.js_content)
        self.assertIn("lastEmittedReplayState", component.js_content)
        self.assertIn("selected_zone_id: state.selectedZone", component.js_content)
        self.assertIn("branch: state.choice || frame.branch", component.js_content)
        self.assertIn("setTriggerValue", component.js_content)
        self.assertEqual(len(app.sidebar.chat_input), 1)
        self.assertTrue(
            any(
                "Replaying a historical heatwave scenario, fully pre-processed by "
                "BigQuery ML, TimeFM, and the Safety Optimizer."
                in str(item.value)
                for item in app.sidebar.caption
            )
        )

        app.sidebar.chat_input[0].set_value("Compare SafePause options")
        with mock.patch.dict(os.environ, ENVIRONMENT, clear=False):
            app.run()
        self.assertFalse(app.exception)
        replay_messages = app.session_state["gemini_copilot_messages"]
        self.assertEqual(
            replay_messages[-1]["tool"],
            "safepause_decision_pending",
        )
        self.assertIn(
            "The Safety Optimizer has evaluated conditions",
            replay_messages[-1]["content"],
        )
        self.assertIn(
            "No SafePause scenario is needed",
            replay_messages[-1]["content"],
        )
        self.assertNotIn("10:00", replay_messages[-1]["content"])
        self.assertNotIn(
            "| Area | SafePause plan | Guardrails (cost/ETA) |",
            replay_messages[-1]["content"],
        )

    def test_current_dashboard_emits_only_bounded_live_intents(self):
        app = self.run_app()
        component = app.get("bidi_component")[0].proto
        payload = json.loads(component.json)
        self.assertTrue(payload["current_actions"]["available"])
        self.assertIsNone(payload["current_actions"]["recorded_action"])
        self.assertIn('setTriggerValue?.("selected_zone_id"', component.js_content)
        self.assertIn('setTriggerValue?.("decision_action"', component.js_content)
        self.assertIn("if (isCurrent(state)) return", component.js_content)
        self.assertNotIn("emitReplayState(state)\n  const preventiveCoverage", component.js_content)
        self.assert_operator_vocabulary(app)


if __name__ == "__main__":
    unittest.main()
