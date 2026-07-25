from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from heatsafe.simulation.cli import main
from heatsafe.simulation.repository import InMemorySimulationRepository
from heatsafe.simulation.telemetry import COMPONENTS, TickTelemetry, component_span


class _Job:
    job_id = "job-123"
    slot_millis = 456
    total_bytes_processed = 789
    total_bytes_billed = 700


class SimulationTelemetryTests(unittest.TestCase):
    def test_span_emits_frozen_schema_and_job_statistics(self):
        lines: list[str] = []
        telemetry = TickTelemetry(sink=lines.append)
        telemetry.bind(
            simulation_run_id="run-1",
            tick_id="tick-1",
            tick_index=24,
            snapshot_id="snapshot-1",
        )
        with telemetry.activate():
            with component_span("publication_commit", row_count=10) as span:
                span.attach_job(_Job())
        telemetry.finish(outcome="SUCCEEDED")

        payloads = [json.loads(line) for line in lines]
        self.assertEqual(
            [item["component"] for item in payloads],
            ["publication_commit", "tick_total"],
        )
        self.assertEqual(payloads[0]["schema_version"], "phase5r-component-v1")
        self.assertEqual(payloads[0]["bigquery_job_id"], "job-123")
        self.assertEqual(payloads[0]["slot_millis"], 456)
        self.assertEqual(payloads[0]["total_bytes_processed"], 789)
        self.assertEqual(payloads[0]["total_bytes_billed"], 700)
        self.assertEqual(payloads[0]["row_count"], 10)
        self.assertEqual(payloads[0]["simulation_run_id"], "run-1")
        self.assertGreaterEqual(payloads[0]["elapsed_ms"], 0)

    def test_failed_span_is_fail_closed_and_excludes_exception_message(self):
        lines: list[str] = []
        telemetry = TickTelemetry(sink=lines.append)
        with self.assertRaisesRegex(RuntimeError, "sensitive-row-value"):
            with telemetry.activate():
                with component_span("controls_load"):
                    raise RuntimeError("sensitive-row-value")
        payload = json.loads(lines[0])
        self.assertEqual(payload["outcome"], "FAILED")
        self.assertEqual(payload["error_code"], "RuntimeError")
        self.assertNotIn("sensitive-row-value", lines[0])

    def test_cli_emits_exactly_one_tick_total_when_explicitly_enabled(self):
        repository = InMemorySimulationRepository()

        def factory(_settings, *, memory):
            self.assertTrue(memory)
            return repository

        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(["start", "--memory"], repository_factory=factory), 0
            )
        output = io.StringIO()
        with patch.dict(
            "os.environ",
            {
                "HEATSAFE_SIMULATION_COMPONENT_TELEMETRY": "1",
                "HEATSAFE_SIMULATION_STATE_MODE": "checkpoint",
            },
            clear=False,
        ), redirect_stdout(output):
            code = main(["tick", "--memory"], repository_factory=factory)
        self.assertEqual(code, 0)
        payloads = [json.loads(line) for line in output.getvalue().splitlines()]
        components = [
            item["component"]
            for item in payloads
            if item.get("event") == "simulation_tick_component"
        ]
        self.assertEqual(components.count("tick_total"), 1)
        self.assertTrue(
            {
                "run_load",
                "lease_acquire",
                "controls_load",
                "checkpoint_replay_delta",
                "advance_tick",
                "publication_projection",
                "tick_total",
            }.issubset(components)
        )
        self.assertTrue(set(components).issubset(COMPONENTS))
        self.assertTrue(
            all(
                item["state_mode"] == "checkpoint"
                for item in payloads
                if item.get("event") == "simulation_tick_component"
            )
        )

    def test_terminal_tick_total_is_a_noop_not_a_successful_work_tick(self):
        repository = InMemorySimulationRepository()

        def factory(_settings, *, memory):
            self.assertTrue(memory)
            return repository

        run = repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )
        repository.runs[run.run_id] = replace(run, status="COMPLETED")
        output = io.StringIO()
        with patch.dict(
            "os.environ",
            {"HEATSAFE_SIMULATION_COMPONENT_TELEMETRY": "1"},
            clear=False,
        ), redirect_stdout(output):
            code = main(["tick", "--memory"], repository_factory=factory)
        self.assertEqual(code, 0)
        totals = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if '"component": "tick_total"' in line
        ]
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]["outcome"], "NO_OP")


if __name__ == "__main__":
    unittest.main()
