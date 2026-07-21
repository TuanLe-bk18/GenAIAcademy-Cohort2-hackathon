from __future__ import annotations

import os
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest


class HeatSafeAppTests(unittest.TestCase):
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
                ["Operating scenario", "Decision zone"],
            )
            self.assertEqual(
                [item.label for item in app.number_input],
                ["Cost cap ($)", "Partner / driver ($)"],
            )
            self.assertEqual(
                [item.label for item in app.tabs],
                [
                    "CITY INTELLIGENCE",
                    "DRIVER EVIDENCE",
                    "COPILOT & AUDIT",
                    "MODEL PERFORMANCE",
                ],
            )
            initial_refresh_token = app.session_state["refresh_token"]
            self.assertIsInstance(initial_refresh_token, str)
            self.assertTrue(initial_refresh_token)
            self.assertEqual(app.selectbox[1].key, "zone_selector_id")

            app.selectbox[1].select("dong-da")
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["selected_zone_id"], "dong-da")
            self.assertEqual(app.selectbox[1].value, "dong-da")

            app.session_state["selected_zone_id"] = "hoan-kiem"
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox[1].value, "hoan-kiem")

            refresh = next(
                button for button in app.button if button.label == "Refresh data"
            )
            refresh.click()
            app.run()
            self.assertFalse(app.exception)
            self.assertNotEqual(
                app.session_state["refresh_token"], initial_refresh_token
            )


if __name__ == "__main__":
    unittest.main()
