from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

from heatsafe.simulation.cli import main
from heatsafe.simulation.repository import InMemorySimulationRepository
from heatsafe.simulation.scoring import DeterministicSnapshotScorer
from heatsafe.simulation import load_zone_priors


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

    def test_audit_realism_is_local_and_reports_all_requested_seeds(self):
        bounded = replace(
            load_zone_priors()[0],
            active_anchor=48,
            exposed_2h_anchor=0,
            exposed_4h_anchor=0,
            forecast_requests_30m=12,
        )
        with patch("heatsafe.simulation.engine.load_zone_priors", return_value=(bounded,)):
            code, output = self.call("audit-realism", "--seeds", "42")
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertTrue(payload["certified"])
        self.assertEqual(payload["seeds"], [42])
        self.assertEqual(len(payload["audits"][0]["hourly"]), 24)

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

    def test_fast_replay_runs_back_to_back_through_target_tick(self):
        self.call("start")
        run_id = self.repository.status("heatwave").run_id
        scorer_creations = 0

        def scorer_factory(_settings, *, memory):
            nonlocal scorer_creations
            self.assertTrue(memory)
            scorer_creations += 1
            return DeterministicSnapshotScorer()

        code, output = self.call_with_scorer(
            scorer_factory,
            "fast-replay",
            "--run-id",
            run_id,
            "--until",
            "2",
            "--batch-size",
            "1",
        )

        payloads = [json.loads(line) for line in output.splitlines()]
        tick_events = [
            payload
            for payload in payloads
            if payload.get("event") == "simulation_tick_completed"
        ]
        completion = payloads[-1]
        self.assertEqual(code, 0)
        self.assertEqual(scorer_creations, 1)
        self.assertEqual(len(tick_events), 3)
        self.assertEqual(
            self.repository.status("heatwave").last_completed_tick_index, 2
        )
        self.assertEqual(
            completion["event"], "simulation_fast_replay_completed"
        )
        self.assertEqual(completion["outcome"], "SUCCEEDED")
        self.assertEqual(completion["executed_ticks"], 3)
        self.assertEqual(completion["target_tick_index"], 2)

    def test_fast_replay_rejects_unsafe_batching_before_mutation(self):
        self.call("start")
        run_id = self.repository.status("heatwave").run_id
        code, output = self.call(
            "fast-replay", "--run-id", run_id, "--until", "2",
            "--batch-size", "8"
        )

        self.assertEqual(code, 2)
        self.assertIn("only --batch-size=1 is implemented", output)
        self.assertIsNone(
            self.repository.status("heatwave").last_completed_tick_index
        )

    def test_fast_replay_rejects_manual_tick_id_before_mutation(self):
        self.call("start")
        run_id = self.repository.status("heatwave").run_id
        code, output = self.call(
            "fast-replay", "--run-id", run_id, "--until", "2",
            "--tick-id", "fixed"
        )

        self.assertEqual(code, 2)
        self.assertIn("--tick-id cannot be used", output)
        self.assertIsNone(
            self.repository.status("heatwave").last_completed_tick_index
        )

    def test_fast_replay_stops_on_score_failure(self):
        class FailingScorer:
            def score(self, _run, _publication):
                raise RuntimeError("model unavailable")

        self.call("start")
        run_id = self.repository.status("heatwave").run_id
        code, output = self.call_with_scorer(
            lambda _settings, *, memory: FailingScorer(),
            "fast-replay",
            "--run-id",
            run_id,
            "--until",
            "2",
        )

        run = self.repository.status("heatwave")
        self.assertEqual(code, 2)
        self.assertIn("SCORE_FAILED", output)
        self.assertIsNone(run.last_completed_tick_index)
        self.assertIsNotNone(run.pending_score_tick_id)

    def test_fast_replay_requires_exact_run_before_mutation(self):
        self.call("start")

        code, output = self.call("fast-replay", "--until", "2")
        self.assertEqual(code, 2)
        self.assertIn("requires --run-id", output)

        code, output = self.call(
            "fast-replay", "--run-id", "wrong-run", "--until", "2"
        )
        self.assertEqual(code, 2)
        self.assertIn("does not match", output)
        self.assertIsNone(
            self.repository.status("heatwave").last_completed_tick_index
        )

    def test_fast_replay_rejects_legacy_clock_before_lease_mutation(self):
        self.call("start")
        run = self.repository.status("heatwave")
        self.repository.runs[run.run_id] = replace(
            run, start_time=datetime(2026, 7, 24, tzinfo=UTC)
        )
        tick = next(
            tick
            for tick in self.repository.ticks.values()
            if tick.run_id == run.run_id and tick.tick_index == 0
        )

        code, output = self.call(
            "fast-replay", "--run-id", run.run_id, "--until", "0"
        )

        self.assertEqual(code, 2)
        self.assertIn("replay clock drift", output)
        self.assertEqual(self.repository.ticks[tick.tick_id].status, "PENDING")
        self.assertIsNone(self.repository.ticks[tick.tick_id].lease_owner)


if __name__ == "__main__":
    unittest.main()
