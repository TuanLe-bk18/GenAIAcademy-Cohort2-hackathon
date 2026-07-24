from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace

from heatsafe.simulation.cli import main
from heatsafe.simulation.repository import InMemorySimulationRepository


class SimulationCliTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemorySimulationRepository()

    def factory(self, _settings, *, memory):
        self.assertTrue(memory)
        return self.repository

    def call(self, *argv):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main([*argv, "--memory"], repository_factory=self.factory)
        return result, output.getvalue()

    def call_with_scorer(self, scorer_factory, *argv):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                [*argv, "--memory"],
                repository_factory=self.factory,
                scorer_factory=scorer_factory,
            )
        return result, output.getvalue()

    def test_validate_scenario_is_local_and_deterministic(self):
        code, output = self.call("validate-scenario")
        self.assertEqual(code, 0)
        self.assertIn('"weather_points": 96', output)

    def test_start_tick_status_pause_and_resume_route_through_repository(self):
        self.assertEqual(self.call("start", "--seed", "42")[0], 0)
        self.assertEqual(self.call("status")[0], 0)
        self.assertEqual(self.call("pause")[0], 0)
        self.assertEqual(self.call("resume")[0], 0)
        code, output = self.call("tick")
        self.assertEqual(code, 0)
        self.assertIn("SUCCEEDED", output)
        self.assertIn("prediction_run_id", output)

    def test_tick_without_start_fails_closed(self):
        code, output = self.call("tick")
        self.assertEqual(code, 2)
        self.assertIn("start a simulation", output)

    def test_tick_selects_the_next_index_after_a_score_finalization(self):
        self.call("start")
        self.call("tick")
        code, output = self.call("tick")
        self.assertEqual(code, 0)
        self.assertNotIn('"tick_id": null', output)
        self.assertEqual(self.repository.status("heatwave").last_published_tick_index, 1)

    def test_score_failure_stays_pending_and_exact_retry_succeeds(self):
        class FailingScorer:
            def score(self, _run, _publication):
                raise RuntimeError("model unavailable")

        self.call("start")
        code, output = self.call_with_scorer(
            lambda _settings, *, memory: FailingScorer(), "tick"
        )
        self.assertEqual(code, 2)
        self.assertIn("SCORE_FAILED", output)
        failed_run = self.repository.status("heatwave")
        failed_tick_id = failed_run.pending_score_tick_id
        self.assertIsNotNone(failed_tick_id)
        self.assertEqual(self.repository.ticks[failed_tick_id].status, "SCORE_FAILED")
        code, output = self.call("tick")
        self.assertEqual(code, 0)
        self.assertIn("SUCCEEDED", output)
        self.assertEqual(
            self.repository.status("heatwave").last_completed_tick_index, 0
        )

    def test_completed_run_is_a_terminal_successful_noop(self):
        self.call("start")
        run = self.repository.status("heatwave")
        self.repository.runs[run.run_id] = replace(run, status="COMPLETED")
        code, output = self.call("tick")
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(payload["outcome"], "NO_OP_TERMINAL")
        self.assertTrue(payload["terminal_signal"])
        self.assertEqual(payload["event"], "simulation_tick_terminal")

    def test_fresh_lease_overlap_is_a_bounded_successful_noop(self):
        self.call("start")
        run = self.repository.status("heatwave")
        tick = next(
            tick
            for tick in self.repository.ticks.values()
            if tick.run_id == run.run_id and tick.tick_index == 0
        )
        self.repository.acquire_tick_lease(run.run_id, tick.tick_id, "other")
        code, output = self.call("tick")
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(payload["outcome"], "NO_OP_LEASE_HELD")
        self.assertEqual(payload["event"], "simulation_tick_overlap")

    def test_success_log_contains_scheduler_lineage_and_duration(self):
        self.call("start")
        code, output = self.call("tick")
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(payload["event"], "simulation_tick_completed")
        self.assertIn("simulation_run_id", payload)
        self.assertIn("tick_id", payload)
        self.assertIn("snapshot_id", payload)
        self.assertIn("checksum", payload)
        self.assertIn("duration_ms", payload)


if __name__ == "__main__":
    unittest.main()
