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

        self.assertEqual([item.label for item in app.metric], self.KPI_LABELS)
        self.assertEqual(len(app.metric), 3)
        self.assertEqual(len(app.dataframe), 0)
        self.assertEqual(len(app.get("plotly_chart")), 1)
        self.assertEqual(
            self.widget(app.segmented_control, "Console view").value,
            "Operations",
        )
        self.assertIn("Hanoi heat map", [item.value for item in app.subheader])
        priority_buttons = [
            item for item in app.button if item.label[:2] in {"1.", "2.", "3."}
        ]
        self.assertLessEqual(len(priority_buttons), 3)
        self.assertIn("Activate SafePause", [item.label for item in app.button])
        self.assertIn("Continue monitoring", [item.label for item in app.button])
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
        selector = self.widget(app.segmented_control, "Plan explanation")
        self.assertEqual(selector.value, "Timing")
        self.assertEqual(len(app.get("plotly_chart")), 1)

        selector.set_value("Trade-offs")
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(
            self.widget(app.segmented_control, "Plan explanation").value,
            "Trade-offs",
        )
        self.assertEqual(len(app.get("plotly_chart")), 1)

        self.widget(app.segmented_control, "Plan explanation").set_value(
            "Stress test"
        )
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("plotly_chart")), 1)
        self.assert_operator_vocabulary(app)

    def test_tight_budget_fails_closed_with_plain_guidance(self):
        app = self.run_app()
        self.widget(app.number_input, "Budget limit ($)").set_value(0.0)
        self.widget(app.button, "Apply limits").click()
        app.run()

        self.assertFalse(app.exception)
        self.assertTrue(
            any("No safe plan fits the current limits" in item.value for item in app.error)
        )
        coverage = self.widget(app.metric, "Safety coverage")
        self.assertTrue(str(coverage.value).startswith("0 /"))
        self.assert_operator_vocabulary(app)

    def test_simulation_playback_is_precomputed_and_browser_local(self):
        app = self.run_app()
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
        timeline = json.loads(component.json)
        self.assertEqual(timeline["schema_version"], "operator-presentation-v1")
        self.assertEqual(timeline["range_label"], "09:15–13:15")
        self.assertEqual(timeline["decision_time_label"], "11:15")
        self.assertEqual(len(timeline["pre_decision"]), 9)
        self.assertEqual(len(timeline["branches"]["ACTIVATE"]), 8)
        self.assertEqual(len(timeline["branches"]["CONTINUE"]), 8)
        self.assertEqual(timeline["plan_status"], "READY")
        self.assertIn("Next 15 min", component.html_content)
        self.assertIn('data-action="play"', component.html_content)
        self.assertIn("setInterval", component.js_content)
        self.assertNotIn("setStateValue", component.js_content)
        self.assertNotIn("setTriggerValue", component.js_content)

    def test_current_decision_is_recorded_once_and_replaces_actions(self):
        app = self.run_app()
        self.widget(app.button, "Continue monitoring").click()
        app.run()
        self.assertFalse(app.exception)
        self.widget(app.button, "Confirm continue monitoring").click()
        app.run()

        self.assertFalse(app.exception)
        self.assertTrue(
            any(
                "Continue monitoring recorded" in item.value
                for item in app.success
            )
        )
        action_labels = [item.label for item in app.button]
        self.assertNotIn("Activate SafePause", action_labels)
        self.assertNotIn("Continue monitoring", action_labels)
        self.assert_operator_vocabulary(app)


if __name__ == "__main__":
    unittest.main()
