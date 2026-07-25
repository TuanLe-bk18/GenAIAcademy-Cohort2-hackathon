from __future__ import annotations

from datetime import UTC, datetime
import unittest
from typing import Any, cast

from heatsafe.repository import (
    BigQueryRepository,
    ForecastUnavailable,
    SnapshotRepository,
)


RUN_ID = "12345678123456781234567812345678"
TICK_ID = "f" * 64
SNAPSHOT_ID = "e" * 64
NOW = datetime(2026, 5, 26, tzinfo=UTC)


class QueryResult:
    def __init__(self, rows):
        self.rows = rows

    def result(self):
        return self.rows


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries = []
        self.configs = []

    def query(self, query, job_config):
        self.queries.append(query)
        self.configs.append(job_config)
        return QueryResult(self.responses.pop(0))


def run_row(**overrides):
    row = {
        "simulation_run_id": RUN_ID,
        "scenario_id": "heatwave",
        "scenario_version": "hanoi_heatwave_v1",
        "status": "RUNNING",
        "simulation_start_at": NOW,
        "last_published_tick_index": 2,
        "last_completed_tick_index": 2,
        "pending_score_tick_id": None,
        "created_at": NOW,
    }
    row.update(overrides)
    return row


def tick_rows():
    zones = SnapshotRepository().load().zones
    return [
        {
            **zone.to_dict(),
            "snapshot_id": SNAPSHOT_ID,
            "simulation_run_id": RUN_ID,
            "tick_id": TICK_ID,
            "generator_version": "stateful-replay-v1",
        }
        for zone in zones
    ]


class ReplayHistoryRepositoryTests(unittest.TestCase):
    def repository(self, responses):
        repository = BigQueryRepository(scenario="heatwave")
        repository._client_instance = cast(Any, Client(responses))
        return repository

    def test_lists_runs_and_loads_bounded_progress(self):
        progress_row = run_row(
            succeeded_ticks=3,
            failed_ticks=0,
            latest_succeeded_tick_index=2,
        )
        repository = self.repository([[run_row()], [progress_row]])

        runs = repository.list_replay_runs(999)
        progress = repository.load_replay_progress(RUN_ID)

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].simulation_run_id, RUN_ID)
        self.assertEqual(progress.succeeded_ticks, 3)
        self.assertEqual(progress.latest_succeeded_tick_index, 2)
        self.assertEqual(progress.total_ticks, 96)
        client = cast(Any, repository._client_instance)
        self.assertIn("LIMIT @limit", client.queries[0])
        self.assertIn("COUNTIF(tick.status = 'SUCCEEDED')", client.queries[1])
        limit_parameter = next(
            parameter
            for parameter in client.configs[0].query_parameters
            if parameter.name == "limit"
        )
        self.assertEqual(limit_parameter.value, 100)

    def test_exact_tick_uses_existing_history_and_not_current_projection(self):
        repository = self.repository([tick_rows()])
        result = repository.load_replay_tick(RUN_ID, 24)

        self.assertEqual(len(result.zones), 10)
        self.assertEqual({zone.snapshot_id for zone in result.zones}, {SNAPSHOT_ID})
        self.assertEqual(
            {zone.simulation_run_id for zone in result.zones}, {RUN_ID}
        )
        query = cast(Any, repository._client_instance).queries[0]
        self.assertIn("simulation_ticks", query)
        self.assertIn("zone_operations", query)
        self.assertIn("weather_observations", query)
        self.assertIn("coolstop_partners", query)
        self.assertIn("status = 'SUCCEEDED'", query)
        self.assertNotIn("zone_snapshots_current", query)

    def test_exact_tick_fails_closed_on_incomplete_or_mixed_lineage(self):
        incomplete = self.repository([tick_rows()[:9]])
        with self.assertRaisesRegex(RuntimeError, "expected 10 zones"):
            incomplete.load_replay_tick(RUN_ID, 0)

        mixed = tick_rows()
        mixed[-1] = {**mixed[-1], "snapshot_id": "d" * 64}
        repository = self.repository([mixed])
        with self.assertRaisesRegex(RuntimeError, "mixed or duplicate lineage"):
            repository.load_replay_tick(RUN_ID, 0)

    def test_external_identifiers_fail_before_query(self):
        repository = self.repository([])
        with self.assertRaises(ValueError):
            repository.load_replay_tick("not-a-run-id", 0)
        with self.assertRaises(ValueError):
            repository.load_replay_tick(RUN_ID, 96)
        self.assertEqual(cast(Any, repository._client_instance).queries, [])

    def test_historical_forecast_is_scoped_to_exact_tick_lineage(self):
        repository = self.repository([tick_rows(), []])
        repository.load_replay_tick(RUN_ID, 24)

        with self.assertRaises(ForecastUnavailable):
            repository.forecast_demand("ba-dinh")

        client = cast(Any, repository._client_instance)
        query = client.queries[1]
        self.assertIn("forecast.simulation_run_id = @simulation_run_id", query)
        self.assertIn("forecast.tick_id = @tick_id", query)
        self.assertIn("forecast.snapshot_id = @snapshot_id", query)
        self.assertNotIn("zone_snapshots_current", query)
        parameters = {
            parameter.name: parameter.value
            for parameter in client.configs[1].query_parameters
        }
        self.assertEqual(parameters["simulation_run_id"], RUN_ID)
        self.assertEqual(parameters["tick_id"], TICK_ID)
        self.assertEqual(parameters["snapshot_id"], SNAPSHOT_ID)


if __name__ == "__main__":
    unittest.main()
