from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from streamlit.testing.v1 import AppTest

from heatsafe.ui.copilot_panel import COPILOT_STATE_VERSION
from heatsafe.ui.operator_console.vocabulary import operator_copy_violations


ENVIRONMENT = {
    "HEATSAFE_MODE": "snapshot",
    "HEATSAFE_ENABLE_AI": "0",
    "HEATSAFE_SCENARIO": "heatwave",
}


class HeatSafeOperatorAppTests(unittest.TestCase):
    KPI_LABELS: ClassVar[list[str]] = [
        "Mandatory breaks now",
        "At risk within 15 min",
        "Active drivers",
    ]
    AREA_COLUMNS: ClassVar[set[str]] = {
        "Area",
        "Heat",
        "Need now",
        "Demand (30 min)",
        "Plan status",
    }

    def run_app(
        self,
        timeout: int = 60,
        *,
        environment: dict[str, str] | None = None,
    ) -> AppTest:
        with mock.patch.dict(
            os.environ,
            environment or ENVIRONMENT,
            clear=False,
        ):
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
        self.assertGreater(payload["city_budget_usd"], 0)
        self.assertTrue(
            all(
                zone["priority_order"] is not None
                and zone["forecast_requests_30m"] >= 0
                for zone in payload["pre_decision"][0]["zones"]
            )
        )
        self.assertIn("Hanoi operating areas", component.html_content)
        self.assertIn("Why this plan", component.html_content)
        self.assertIn("data-district-detail", component.html_content)
        self.assertIn("renderDistrictDetail", component.js_content)
        self.assertIn("districtDecisionCopy", component.js_content)
        self.assertIn("data-map-basemap", component.html_content)
        self.assertIn("© OpenStreetMap · © CARTO", component.html_content)
        self.assertIn("basemaps.cartocdn.com", component.js_content)
        self.assertIn("renderBasemap(state, projection)", component.js_content)
        self.assertNotIn('data-insight-scope', component.html_content)
        self.assertIn('data-map-all', component.html_content)
        self.assertIn('map-selection-summary', component.html_content)
        self.assertNotIn('data-map-summary-active', component.html_content)
        self.assertIn(
            "drivers are projected to require a mandatory break within 15 minutes.",
            component.js_content,
        )
        self.assertIn('data-choice="ACTIVATE"', component.html_content)
        self.assertIn(">Activate SafePause</button>", component.html_content)
        self.assertIn(">Continue Monitoring</button>", component.html_content)
        self.assertIn(
            ".action-strip button{width:100%;color:var(--warning)",
            component.css_content,
        )
        self.assertNotIn("status-strip", component.html_content)
        self.assertNotIn("decision-panel", component.html_content)
        self.assertNotIn("Synthetic Hanoi operations", component.html_content)
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
        forbidden_sidebar_controls = {
            "Selected area",
            "Budget limit ($)",
            "Support per driver ($)",
            "Apply limits",
            "Refresh conditions",
            "Reset view",
        }
        sidebar_labels = {
            item.label
            for collection in (
                app.sidebar.selectbox,
                app.sidebar.number_input,
                app.sidebar.button,
            )
            for item in collection
        }
        self.assertTrue(forbidden_sidebar_controls.isdisjoint(sidebar_labels))
        self.assert_operator_vocabulary(app)

    def test_evidence_surface_renders_only_bounded_area_table(self):
        app = self.run_app()
        self.widget(app.segmented_control, "Console view").set_value(
            "Evidence & History"
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
            "Area evidence",
        )
        self.widget(app.segmented_control, "Evidence view").set_value(
            "SafePause plan"
        )
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe), 1)
        plan_frame = app.dataframe[0].value
        self.assertEqual(
            list(plan_frame.columns),
            [
                "Area",
                "Drivers protected",
                "Start",
                "Fulfillment impact",
                "ETA impact",
                "Reserved cost",
            ],
        )
        self.assertGreater(len(plan_frame), 0)
        self.widget(app.segmented_control, "Evidence view").set_value(
            "Decision history"
        )
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(
            self.widget(app.segmented_control, "Evidence view").value,
            "Decision history",
        )
        self.assertEqual(len(app.dataframe), 0)
        self.assertTrue(
            any("No records are available" in str(item.value) for item in app.caption)
        )
        self.assert_operator_vocabulary(app)

    def test_replay_evidence_surface_uses_timeline_without_mounting_dashboard(self):
        app = self.run_app()
        self.widget(app.segmented_control, "Mode").set_value(
            "accelerated-production"
        )
        app.run()
        self.widget(app.segmented_control, "Console view").set_value(
            "Evidence & History"
        )
        app.run()

        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("bidi_component")), 0)
        self.assertEqual(len(app.dataframe), 1)
        self.assertEqual(len(app.dataframe[0].value.columns), 6)
        self.assertEqual(
            self.widget(app.segmented_control, "Evidence view").value,
            "Area evidence",
        )
        self.assertTrue(
            any(
                "Current heat, safety need, demand" in str(item.value)
                for item in app.caption
            )
        )
        self.widget(app.segmented_control, "Evidence view").set_value(
            "SafePause plan"
        )
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe), 0)
        self.assertTrue(
            any("No records are available" in str(item.value) for item in app.caption)
        )

        app.session_state["operator-dashboard:persisted-replay-state"] = {
            "tick": 40,
            "branch": "ACTIVATE",
            "scope": "citywide",
            "selected_zone_id": None,
        }
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe), 1)
        self.assertEqual(len(app.dataframe[0].value.columns), 6)

        self.widget(app.segmented_control, "Evidence view").set_value(
            "Decision history"
        )
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(
            self.widget(app.segmented_control, "Evidence view").value,
            "Decision history",
        )
        self.assertEqual(len(app.dataframe), 1)
        history = app.dataframe[0].value
        self.assertEqual(len(history), 1)
        self.assertEqual(history.iloc[0]["Action"], "Activate SafePause")
        self.assertEqual(history.iloc[0]["Time"], "10:00")
        self.assert_operator_vocabulary(app)

    def test_citywide_default_and_district_detail_share_one_evidence_surface(self):
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
        self.assertIsNotNone(selected["fulfillment_drop_percent"])
        self.assertIsNotNone(selected["eta_impact_minutes"])
        self.assertIsNotNone(selected["safepause_driver_count"])
        self.assertIsNotNone(selected["expected_risk_prevented"])
        self.assertIsNotNone(selected["high_demand_reserved_cost_usd"])
        self.assertIsNotNone(selected["expected_crossers_120m"])
        self.assertNotIn("Selected district", component.html_content)
        self.assertIn(">All Districts</button>", component.html_content)
        self.assertIn("renderAllDistrictsInsight", component.js_content)
        self.assertIn("Expected safety-limit crossings by district", component.js_content)
        self.assertIn("Expected crossers / projected", component.js_content)
        self.assertNotIn('<option value="stress">Stress test</option>', component.html_content)
        self.assertIn("Drivers at the mandatory-break limit", component.js_content)
        self.assertNotIn("ACTIVATE and CONTINUE are compared only through tick", component.js_content)
        self.assertIn("selectAllDistricts(state)", component.js_content)
        self.assertIn('<option value="status" selected>SafePause status</option>', component.html_content)
        self.assertIn('mapMetric:"status"', component.js_content)
        self.assertIn("City-wide intervention tradeoffs", component.js_content)
        self.assert_operator_vocabulary(app)

    def test_fixed_server_policy_reaches_optimizer_and_fails_closed(self):
        environment = {
            **ENVIRONMENT,
            "HEATSAFE_OPERATOR_BUDGET_CAP_VND": "0",
            "HEATSAFE_OPERATOR_SPONSOR_PER_DRIVER_VND": "8000",
        }
        app = self.run_app(environment=environment)

        self.assertFalse(app.exception)
        payload = json.loads(app.get("bidi_component")[0].proto.json)
        selected = next(
            zone for zone in payload["pre_decision"][0]["zones"] if zone["selected"]
        )
        recommendation = payload["decision_views"][selected["id"]]["recommendation"]
        self.assertFalse(recommendation["can_activate"])
        self.assertNotEqual(recommendation["state"], "ready")
        self.assertRegex(
            payload["current_kpis"][0]["value"],
            r"^[\d,]+ drivers$",
        )
        self.assert_operator_vocabulary(app)

    def test_simulation_playback_is_precomputed_and_browser_local(self):
        app = self.run_app()
        current_component = app.get("bidi_component")[0].proto
        current_html = current_component.html_content
        current_js = current_component.js_content
        current_sidebar_shape = {
            name: len(getattr(app.sidebar, name))
            for name in (
                "button",
                "caption",
                "chat_input",
                "markdown",
                "number_input",
                "segmented_control",
                "selectbox",
            )
        }
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
        self.assertNotIn("data-kpi-preventive-card", component.html_content)
        self.assertIn("event?.new_preventive_count", component.js_content)
        self.assertIn('"At risk within 15 min"', component.js_content)
        self.assertIn("Activate SafePause now", component.js_content)
        self.assertIn("Start mandatory breaks now", component.js_content)
        self.assertIn("SafePause activated", component.js_content)
        self.assertIn("Update SafePause coverage", component.js_content)
        self.assertIn("Continue monitoring", component.js_content)
        self.assertIn(">Activate SafePause</button>", component.html_content)
        self.assertIn(">Continue Monitoring</button>", component.html_content)
        self.assertNotIn("status-strip", component.html_content)
        self.assertNotIn("decision-panel", component.html_content)
        self.assertIn(
            "function citywideRecommendation(state, frame)",
            component.js_content,
        )
        self.assertNotIn(
            "Select a district on the map for its detailed recommendation.",
            component.js_content,
        )
        self.assertIn('data-action="play"', component.html_content)
        self.assertIn("setInterval", component.js_content)
        self.assertIn('setStateValue?.("replay_state"', component.js_content)
        self.assertIn("lastEmittedReplayState", component.js_content)
        select_all_source = component.js_content[
            component.js_content.index("function selectAllDistricts(state)"):
            component.js_content.index("function tweenNumber")
        ]
        self.assertIn("state.selectedZone = null", select_all_source)
        self.assertIn("emitReplayState(state)", select_all_source)
        emit_source = component.js_content[
            component.js_content.index("function emitReplayState(state)"):
            component.js_content.index("function stop(state)")
        ]
        self.assertNotIn("!state.selectedZone", emit_source)
        self.assertIn(
            'scope: state.selectedZone ? "district" : "citywide"',
            emit_source,
        )
        self.assertIn("selected_zone_id: state.selectedZone", emit_source)
        self.assertIn("branch: state.choice || frame.branch", emit_source)
        self.assertIn("setTriggerValue", component.js_content)
        self.assertEqual(len(app.sidebar.chat_input), 1)
        replay_sidebar_shape = {
            name: len(getattr(app.sidebar, name))
            for name in current_sidebar_shape
        }
        expected_replay_sidebar_shape = dict(current_sidebar_shape)
        expected_replay_sidebar_shape["caption"] += 1
        self.assertEqual(
            replay_sidebar_shape,
            expected_replay_sidebar_shape,
        )
        replay_sidebar_copy = " ".join(
            str(item.value)
            for collection in (app.sidebar.caption, app.sidebar.markdown)
            for item in collection
        )
        self.assertNotIn(
            "Ask about the displayed replay frame",
            replay_sidebar_copy,
        )
        self.assertNotIn(
            "I can explain the verified replay frame",
            replay_sidebar_copy,
        )
        self.assertIn(
            "09:15 · All Districts · PRE-DECISION",
            replay_sidebar_copy,
        )
        self.assertTrue(
            any(
                "Replaying a reviewed, precomputed Hanoi heatwave simulation "
                "with the deterministic Safety Optimizer."
                in str(item.value)
                for item in app.sidebar.caption
            )
        )

        app.sidebar.chat_input[0].set_value("Compare SafePause options")
        with mock.patch.dict(os.environ, ENVIRONMENT, clear=False):
            app.run()
        self.assertFalse(app.exception)
        replay_messages = app.session_state["replay_copilot_messages"]
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

    def test_mode_chat_namespaces_preserve_and_clear_independently(self):
        app = self.run_app()
        app.sidebar.chat_input[0].set_value("Where should we intervene?")
        with mock.patch.dict(os.environ, ENVIRONMENT, clear=False):
            app.run()
        self.assertFalse(app.exception)
        production_messages = list(
            app.session_state["production_copilot_messages"]
        )
        self.assertEqual(len(production_messages), 2)

        self.widget(app.segmented_control, "Mode").set_value(
            "accelerated-production"
        )
        with mock.patch.dict(os.environ, ENVIRONMENT, clear=False):
            app.run()
        app.sidebar.chat_input[0].set_value("Compare SafePause options")
        with mock.patch.dict(os.environ, ENVIRONMENT, clear=False):
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual(
            app.session_state["production_copilot_messages"],
            production_messages,
        )
        self.assertEqual(len(app.session_state["replay_copilot_messages"]), 2)

        replay_clear = next(
            item
            for item in app.sidebar.button
            if item.key == "replay_copilot-clear"
        )
        replay_clear.click()
        with mock.patch.dict(os.environ, ENVIRONMENT, clear=False):
            app.run()
        self.assertEqual(app.session_state["replay_copilot_messages"], [])
        self.assertEqual(
            app.session_state["production_copilot_messages"],
            production_messages,
        )

        self.widget(app.segmented_control, "Mode").set_value("current")
        with mock.patch.dict(os.environ, ENVIRONMENT, clear=False):
            app.run()
        self.assertEqual(
            app.session_state["production_copilot_messages"],
            production_messages,
        )
        self.assertNotIn("production_copilot_pending_prompt", app.session_state)
        self.assertNotIn("replay_copilot_pending_prompt", app.session_state)
        self.assertEqual(
            app.session_state["production_copilot_state_version"],
            COPILOT_STATE_VERSION,
        )
        self.assertEqual(
            app.session_state["replay_copilot_state_version"],
            COPILOT_STATE_VERSION,
        )

    def test_stale_copilot_state_version_clears_only_its_namespace(self):
        app = self.run_app()
        app.session_state["production_copilot_messages"] = [
            {"role": "assistant", "content": "stale production"}
        ]
        app.session_state["production_copilot_state_version"] = (
            COPILOT_STATE_VERSION - 1
        )
        app.session_state["replay_copilot_messages"] = [
            {"role": "assistant", "content": "keep replay"}
        ]
        app.session_state["replay_copilot_state_version"] = COPILOT_STATE_VERSION

        with mock.patch.dict(os.environ, ENVIRONMENT, clear=False):
            app.run()

        self.assertEqual(app.session_state["production_copilot_messages"], [])
        self.assertEqual(
            app.session_state["replay_copilot_messages"],
            [{"role": "assistant", "content": "keep replay"}],
        )
        self.assertEqual(
            app.session_state["production_copilot_state_version"],
            COPILOT_STATE_VERSION,
        )

    def test_sidebar_source_has_no_removed_controls_or_handlers(self):
        source = Path(
            "heatsafe/ui/operator_console/sidebar.py"
        ).read_text(encoding="utf-8")
        for removed in (
            "Selected area",
            "Budget limit ($)",
            "Support per driver ($)",
            "Apply limits",
            "Refresh conditions",
            "Reset view",
            "playback_action",
            "refresh_requested",
            "reset_requested",
            "limits_applied",
            "OperatorPlaybackView",
            "playback=",
        ):
            self.assertNotIn(removed, source)

    def test_copilot_layout_keys_match_fixed_composer_overflow_contract(self):
        panel_source = Path(
            "heatsafe/ui/copilot_panel.py"
        ).read_text(encoding="utf-8")
        style_source = Path(
            "heatsafe/ui/operator_console/styles.py"
        ).read_text(encoding="utf-8")

        for key in (
            "gemini-copilot-shell",
            "gemini-copilot-header",
            "gemini-copilot-history",
            "gemini-copilot-composer",
        ):
            self.assertIn(f'key="{key}"', panel_source)
            self.assertIn(f".st-key-{key}", style_source)

        self.assertIn(
            "grid-template-rows: auto minmax(0, 1fr) auto",
            style_source,
        )
        self.assertIn("overflow: hidden !important", style_source)
        self.assertIn("overflow-y: auto !important", style_source)

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
