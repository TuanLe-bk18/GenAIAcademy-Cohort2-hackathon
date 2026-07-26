from __future__ import annotations

import os
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest


class HeatSafeAppTests(unittest.TestCase):
    CITY_TAB_LABELS = [
        "City intelligence",
        "Driver evidence",
        "Copilot & audit",
        "Model performance",
    ]
    CITY_PLAN_COLUMNS = {
        "District",
        "Heat index (°C)",
        "Heat source",
        "Mandatory now",
        "Projected +60m",
        "Projected +120m",
        "Watchlist +120m",
        "Expected crossers",
        "Severity rank",
        "Future safety rank",
        "Opportunity rank",
        "Raw risk +120m",
        "Risk prevented",
        "Residual risk +120m",
        "Best window",
        "Expected cost",
        "P95 reserve",
        "Portfolio status",
        "Reason",
    }

    def assert_shared_city_plan(self, app: AppTest) -> None:
        frames = [
            item.value
            for item in app.dataframe
            if self.CITY_PLAN_COLUMNS <= set(item.value.columns)
        ]
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(len(frame), 10)
        self.assertEqual(set(frame["District"]), {
            "Hoàn Kiếm",
            "Hai Bà Trưng",
            "Đống Đa",
            "Ba Đình",
            "Cầu Giấy",
            "Thanh Xuân",
            "Hoàng Mai",
            "Nam Từ Liêm",
            "Hà Đông",
            "Bắc Từ Liêm",
        })
        self.assertTrue(
            set(frame["Portfolio status"]).issubset(
                {"SELECTED", "DEFERRED", "UNAVAILABLE"}
            )
        )

    def test_snapshot_mode_renders_primary_controls_and_refreshes_locally(self):
        environment = {
            "HEATSAFE_MODE": "snapshot",
            "HEATSAFE_ENABLE_AI": "0",
            "HEATSAFE_SCENARIO": "heatwave",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            app = AppTest.from_file("app.py", default_timeout=30)
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(
                [item.label for item in app.selectbox],
                ["Operating scenario", "Experience mode", "Decision zone"],
            )
            self.assertEqual(
                [item.label for item in app.number_input],
                ["Cost cap ($)", "Partner / driver ($)"],
            )
            self.assertEqual(
                [item.label for item in app.tabs],
                self.CITY_TAB_LABELS,
            )
            self.assert_shared_city_plan(app)
            initial_refresh_token = app.session_state["refresh_token"]
            self.assertIsInstance(initial_refresh_token, str)
            self.assertTrue(initial_refresh_token)
            self.assertEqual(app.selectbox[2].key, "zone_selector_id")

            app.selectbox[2].select("dong-da")
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["selected_zone_id"], "dong-da")
            self.assertEqual(app.selectbox[2].value, "dong-da")

            app.session_state["selected_zone_id"] = "hoan-kiem"
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox[2].value, "hoan-kiem")

            refresh = next(
                button for button in app.button if button.label == "Refresh data"
            )
            refresh.click()
            app.run()
            self.assertFalse(app.exception)
            self.assertNotEqual(
                app.session_state["refresh_token"], initial_refresh_token
            )

    def test_accelerated_production_mode_loads_verified_window(self):
        environment = {
            "HEATSAFE_MODE": "snapshot",
            "HEATSAFE_ENABLE_AI": "0",
            "HEATSAFE_SCENARIO": "heatwave",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            app = AppTest.from_file("app.py", default_timeout=45)
            app.run()
            app.selectbox[1].select("accelerated-production")
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(
                any(
                    "Production · Accelerated operational window" in item.value
                    for item in app.markdown
                )
            )
            labels = [button.label for button in app.button]
            self.assertIn("Start", labels)
            self.assertIn("Advance 15 min", labels)
            self.assertIn("Reset run", labels)
            self.assertIn("Decision zone", [item.label for item in app.selectbox])
            self.assertEqual(
                [item.label for item in app.tabs],
                self.CITY_TAB_LABELS,
            )
            self.assert_shared_city_plan(app)
            self.assertEqual(
                [item.label for item in app.number_input],
                ["Cost cap ($)", "Partner / driver ($)"],
            )
            self.assertTrue(
                any(
                    "No SafePause plan is presented before the decision tick"
                    in item.value
                    for item in app.info
                )
            )
            session = app.session_state["production_window_session"]
            self.assertEqual(session.current_tick, 37)
            self.assertEqual(session.window.decision_tick, 45)

    def test_accelerated_start_uses_safe_default_when_speed_is_unset(self):
        environment = {
            "HEATSAFE_MODE": "snapshot",
            "HEATSAFE_ENABLE_AI": "0",
            "HEATSAFE_SCENARIO": "heatwave",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            app = AppTest.from_file("app.py", default_timeout=45)
            app.run()
            app.selectbox[1].select("accelerated-production")
            app.run()
            app.session_state["production_window_speed"] = None

            start = next(button for button in app.button if button.label == "Start")
            start.click()
            app.run()

            self.assertFalse(app.exception)
            session = app.session_state["production_window_session"]
            self.assertEqual(session.status, "RUNNING")


if __name__ == "__main__":
    unittest.main()
