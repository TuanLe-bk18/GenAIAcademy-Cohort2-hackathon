from __future__ import annotations

import unittest

from heatsafe.simulation.execution_policy import (
    TickExecutionInputs,
    plan_tick_execution,
)


class ExecutionPolicyTests(unittest.TestCase):
    def test_every_full_trigger_has_precedence_and_sorted_reasons(self):
        plan = plan_tick_execution(
            TickExecutionInputs(
                current_tier="DANGER",
                lookahead_tier="EXTREME_DANGER",
                exposed_2h=10,
                exposed_4h=2,
                pending_controls=1,
                active_interventions=1,
                previous_mode="MONITOR",
                demand_anomaly=True,
                danger_prewarm=True,
                forecast_available=True,
            )
        )
        self.assertEqual(plan.mode, "FULL")
        self.assertEqual(plan.reason_codes, tuple(sorted(plan.reason_codes)))
        self.assertIn("CURRENT_HEAT_TIER", plan.reason_codes)
        self.assertIn("CONTROL_PENDING", plan.reason_codes)
        self.assertTrue(plan.generate_forecast)
        self.assertTrue(plan.run_ml_inference)

    def test_two_low_risk_full_ticks_then_two_recovery_ticks_then_monitor(self):
        first = plan_tick_execution(
            TickExecutionInputs(
                "CAUTION", "NORMAL", 0, 0,
                previous_mode="FULL", low_risk_streak=0,
            )
        )
        second = plan_tick_execution(
            TickExecutionInputs(
                "CAUTION", "NORMAL", 0, 0,
                previous_mode="FULL",
                low_risk_streak=first.next_low_risk_streak,
            )
        )
        recovery_one = plan_tick_execution(
            TickExecutionInputs(
                "NORMAL", "CAUTION", 0, 0,
                previous_mode="FULL",
                low_risk_streak=second.next_low_risk_streak,
                forecast_available=True,
            )
        )
        recovery_two = plan_tick_execution(
            TickExecutionInputs(
                "NORMAL", "NORMAL", 0, 0,
                previous_mode="RECOVERY",
                low_risk_streak=recovery_one.next_low_risk_streak,
                recovery_streak=recovery_one.next_recovery_streak,
                forecast_available=True,
            )
        )
        monitor = plan_tick_execution(
            TickExecutionInputs(
                "NORMAL", "NORMAL", 0, 0,
                previous_mode="RECOVERY",
                low_risk_streak=recovery_two.next_low_risk_streak,
                recovery_streak=recovery_two.next_recovery_streak,
                forecast_available=True,
            )
        )
        self.assertEqual(
            [first.mode, second.mode, recovery_one.mode, recovery_two.mode, monitor.mode],
            ["FULL", "FULL", "RECOVERY", "RECOVERY", "MONITOR"],
        )
        self.assertTrue(recovery_one.project_features)
        self.assertFalse(recovery_one.run_ml_inference)
        self.assertTrue(monitor.reuse_forecast)

    def test_scoring_failure_and_control_reset_low_risk_streak(self):
        for inputs, reason in (
            (
                TickExecutionInputs(
                    "NORMAL", "NORMAL", 0, 0,
                    previous_mode="FULL",
                    low_risk_streak=10,
                    persisted_scoring_failure=True,
                ),
                "PERSISTED_FULL_RETRY",
            ),
            (
                TickExecutionInputs(
                    "NORMAL", "NORMAL", 0, 0,
                    pending_controls=1,
                    previous_mode="MONITOR",
                    low_risk_streak=10,
                ),
                "CONTROL_PENDING",
            ),
        ):
            with self.subTest(reason=reason):
                plan = plan_tick_execution(inputs)
                self.assertEqual(plan.mode, "FULL")
                self.assertEqual(plan.next_low_risk_streak, 0)
                self.assertIn(reason, plan.reason_codes)

    def test_forecast_generation_is_forced_or_every_fourth_full_tick(self):
        missing = plan_tick_execution(
            TickExecutionInputs("DANGER", "DANGER", 1, 1)
        )
        cadence = plan_tick_execution(
            TickExecutionInputs(
                "DANGER", "DANGER", 1, 1,
                forecast_available=True,
                full_ticks_since_generation=4,
            )
        )
        reuse = plan_tick_execution(
            TickExecutionInputs(
                "DANGER", "DANGER", 1, 1,
                forecast_available=True,
                full_ticks_since_generation=2,
            )
        )
        self.assertTrue(missing.generate_forecast)
        self.assertTrue(cadence.generate_forecast)
        self.assertTrue(reuse.reuse_forecast)


if __name__ == "__main__":
    unittest.main()
