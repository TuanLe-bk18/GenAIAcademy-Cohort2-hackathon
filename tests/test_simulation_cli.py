from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

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
        self.assertIn("SNAPSHOT_READY", output)

    def test_tick_without_start_fails_closed(self):
        code, output = self.call("tick")
        self.assertEqual(code, 2)
        self.assertIn("start a simulation", output)

    def test_tick_selects_the_next_index_after_a_score_finalization(self):
        self.call("start")
        self.call("tick")
        run = self.repository.status("heatwave")
        self.repository.finalize_score(
            run.run_id, run.pending_score_tick_id, succeeded=True
        )
        code, output = self.call("tick")
        self.assertEqual(code, 0)
        self.assertNotIn('"tick_id": null', output)
        self.assertEqual(self.repository.status("heatwave").last_published_tick_index, 1)


if __name__ == "__main__":
    unittest.main()
