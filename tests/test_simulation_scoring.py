from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from heatsafe.config import Settings
from heatsafe.simulation.repository import InMemorySimulationRepository
from heatsafe.simulation.scoring import (
    BigQuerySnapshotScorer,
    DeterministicSnapshotScorer,
)
from infra.ml_pipeline import score_snapshot


class _Done:
    def __init__(self, rows=()):
        self.rows = rows

    def result(self):
        return self.rows


class _RecordingClient:
    def __init__(self, *, fail_first=False):
        self.sql = []
        self.configs = []
        self.fail_first = fail_first

    def query(self, sql, job_config=None):
        self.sql.append(sql)
        self.configs.append(job_config)
        if self.fail_first and len(self.sql) == 1:
            raise RuntimeError("injected scoring failure")
        return _Done()


class SimulationScoringTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(dataset_id="phase4_scoring_unit")
        self.simulation_time = datetime(2026, 5, 26, tzinfo=UTC)

    def test_simulation_sql_uses_persisted_state_exact_lineage_and_replay_time(self):
        client = _RecordingClient()
        run_id = score_snapshot(
            self.settings,
            client,
            scenario="heatwave",
            model_version="model-v1",
            feature_source="simulation",
            simulation_run_id="run-1",
            tick_id="tick-0",
            snapshot_id="snapshot-0",
            simulation_time=self.simulation_time,
        )
        sql = client.sql[0]
        self.assertTrue(run_id.startswith("sim-"))
        self.assertIn("driver_simulation_state", sql)
        self.assertNotIn("GENERATE_ARRAY(1, active_drivers)", sql)
        self.assertIn("simulation_run_id = @simulation_run_id", sql)
        self.assertIn("interval_start BETWEEN TIMESTAMP_SUB(@simulation_time", sql)
        self.assertIn("context_window => 2048", sql)
        self.assertIn("MIN(forecast_at) > @simulation_time", sql)
        self.assertIn("LEAST(50.55, GREATEST(33.05, zone.heat_index_c))", sql)
        self.assertIn("LEAST(5, GREATEST(1, driver.trips_60m))", sql)
        self.assertIn("error_code = IF(", sql)
        self.assertIn("'MODEL_INPUT_OOD'", sql)
        self.assertNotIn("AND NOT features.feature_ood", sql)
        self.assertIn("MERGE `cohort2track2.phase4_scoring_unit.driver_risk_predictions`", sql)
        self.assertIn(
            "WHEN NOT MATCHED THEN INSERT (\n      scenario_id, zone_id, interval_start",
            sql,
        )
        self.assertNotIn("WHEN NOT MATCHED THEN INSERT ROW", sql)
        self.assertIn("SET status = 'SUCCEEDED'", sql)
        self.assertIn("BEGIN TRANSACTION;", sql)
        self.assertIn("pending_score_tick_id = NULL", sql)
        self.assertIn("COMMIT TRANSACTION;", sql)
        self.assertEqual(client.configs[0].maximum_bytes_billed, 400_000_000)

    def test_legacy_sql_preserves_wall_clock_driver_generation_branch(self):
        client = _RecordingClient()
        score_snapshot(
            self.settings,
            client,
            scenario="live",
            model_version="model-v1",
            feature_source="legacy",
        )
        sql = client.sql[0]
        self.assertIn("GENERATE_ARRAY(1, active_drivers)", sql)
        self.assertIn("TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 21 DAY)", sql)
        self.assertNotIn("SET status = 'SUCCEEDED'", sql)

    def test_simulation_lineage_is_mandatory_and_legacy_rejects_it(self):
        with self.assertRaisesRegex(ValueError, "requires heatwave"):
            score_snapshot(
                self.settings,
                _RecordingClient(),
                scenario="heatwave",
                model_version="model-v1",
                feature_source="simulation",
            )
        with self.assertRaisesRegex(ValueError, "does not accept"):
            score_snapshot(
                self.settings,
                _RecordingClient(),
                scenario="live",
                model_version="model-v1",
                feature_source="legacy",
                simulation_run_id="run",
            )

    def test_disposable_profile_model_dataset_is_validated_and_read_only(self):
        client = _RecordingClient()
        score_snapshot(
            self.settings,
            client,
            scenario="heatwave",
            model_version="model-v1",
            feature_source="simulation",
            simulation_run_id="run-1",
            tick_id="tick-0",
            snapshot_id="snapshot-0",
            simulation_time=self.simulation_time,
            model_dataset="cohort2track2.heatsafe_data",
        )
        sql = client.sql[0]
        self.assertIn(
            "MODEL `cohort2track2.heatsafe_data.heat_risk_escalation_model`",
            sql,
        )
        self.assertNotIn(
            "MERGE `cohort2track2.heatsafe_data", sql
        )
        with self.assertRaisesRegex(ValueError, "fully qualified"):
            score_snapshot(
                self.settings,
                _RecordingClient(),
                scenario="live",
                model_version="model-v1",
                model_dataset="unsafe`; DROP TABLE x; --",
            )

    def test_model_version_lookup_uses_separate_read_only_model_dataset(self):
        client = _RecordingClient()
        client.query = lambda sql, job_config=None: (
            client.sql.append(sql)
            or client.configs.append(job_config)
            or _Done(
                [
                    SimpleNamespace(
                        model_version="model-v1",
                    )
                ]
            )
        )
        score_snapshot(
            self.settings,
            client,
            scenario="heatwave",
            feature_source="simulation",
            simulation_run_id="run-1",
            tick_id="tick-0",
            snapshot_id="snapshot-0",
            simulation_time=self.simulation_time,
            model_dataset="cohort2track2.heatsafe_data",
        )
        self.assertIn(
            "`cohort2track2.heatsafe_data.model_evaluations`",
            client.sql[0],
        )
        self.assertNotIn("simulation_runs", client.sql[0])
        self.assertIn(
            "MODEL `cohort2track2.heatsafe_data.heat_risk_escalation_model`",
            client.sql[1],
        )

    def test_run_frozen_model_version_skips_lookup_and_is_used(self):
        client = _RecordingClient()
        score_snapshot(
            self.settings,
            client,
            scenario="heatwave",
            model_version="frozen-model-v1",
            feature_source="simulation",
            simulation_run_id="run-1",
            tick_id="tick-1",
            snapshot_id="snapshot-1",
            simulation_time=self.simulation_time,
            model_dataset="cohort2track2.heatsafe_data",
        )
        self.assertEqual(len(client.sql), 1)
        self.assertNotIn("model_evaluations", client.sql[0])
        model_parameter = next(
            parameter
            for parameter in client.configs[0].query_parameters
            if parameter.name == "model_version"
        )
        self.assertEqual(model_parameter.value, "frozen-model-v1")

    def test_cloud_scorer_forwards_separate_read_only_model_dataset(self):
        repository = InMemorySimulationRepository()
        run = repository.start(
            scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=42
        )
        tick = min(
            (item for item in repository.ticks.values() if item.run_id == run.run_id),
            key=lambda item: item.tick_index,
        )
        lease = repository.acquire_tick_lease(run.run_id, tick.tick_id, "test")
        publication = repository.publish_tick(
            run.run_id, tick.tick_id, lease.fencing_token
        )
        settings = Settings(
            dataset_id="phase4_scoring_unit",
            simulation_model_dataset="cohort2track2.heatsafe_data",
        )
        with patch(
            "infra.ml_pipeline.score_snapshot", return_value="prediction-1"
        ) as score:
            outcome = BigQuerySnapshotScorer(object(), settings=settings).score(
                run, publication
            )
        self.assertEqual(
            score.call_args.kwargs["model_dataset"],
            "cohort2track2.heatsafe_data",
        )
        self.assertTrue(outcome.durably_finalized)
        self.assertIsNone(score.call_args.kwargs["model_version"])
        self.assertTrue(score.call_args.kwargs["seed_forecast_context"])

    def test_score_failure_records_fail_closed_tick_state(self):
        client = _RecordingClient(fail_first=True)
        with self.assertRaisesRegex(RuntimeError, "injected"):
            score_snapshot(
                self.settings,
                client,
                scenario="heatwave",
                model_version="model-v1",
                feature_source="simulation",
                simulation_run_id="run-1",
                tick_id="tick-0",
                snapshot_id="snapshot-0",
                simulation_time=self.simulation_time,
            )
        self.assertEqual(len(client.sql), 2)
        self.assertIn("SCORE_FAILED", client.sql[1])

    def test_monitor_tick_projects_features_and_reuses_forecast_without_ml(self):
        client = _RecordingClient()
        score_snapshot(
            self.settings,
            client,
            scenario="heatwave",
            model_version="model-v1",
            feature_source="simulation",
            simulation_run_id="run-1",
            tick_id="tick-4",
            snapshot_id="snapshot-4",
            simulation_time=self.simulation_time,
            run_ml_inference=False,
            generate_forecast=False,
            forecast_source_tick_id="tick-1",
            forecast_source_snapshot_id="snapshot-1",
            forecast_source_prediction_run_id="prediction-1",
        )
        sql = client.sql[0]
        self.assertIn("driver_current_features", sql)
        self.assertIn("forecast_reused", sql)
        self.assertIn("SKIPPED_LOW_RISK", sql)
        self.assertIn("BEGIN TRANSACTION;", sql)
        self.assertIn("pending_score_tick_id = NULL", sql)
        self.assertIn("COMMIT TRANSACTION;", sql)
        self.assertNotIn("ML.PREDICT", sql)
        self.assertNotIn("ML.EXPLAIN_PREDICT", sql)
        self.assertNotIn("AI.FORECAST", sql)

    def test_reuse_requires_complete_source_lineage(self):
        with self.assertRaisesRegex(ValueError, "complete source lineage"):
            score_snapshot(
                self.settings,
                _RecordingClient(),
                scenario="heatwave",
                model_version="model-v1",
                feature_source="simulation",
                simulation_run_id="run-1",
                tick_id="tick-4",
                snapshot_id="snapshot-4",
                simulation_time=self.simulation_time,
                generate_forecast=False,
            )

    def test_local_scorer_keeps_exact_snapshot_and_action_reduces_risk(self):
        repository = InMemorySimulationRepository()
        run = repository.start(
            scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=42
        )
        tick = min(
            (item for item in repository.ticks.values() if item.run_id == run.run_id),
            key=lambda item: item.tick_index,
        )
        lease = repository.acquire_tick_lease(run.run_id, tick.tick_id, "test")
        publication = repository.publish_tick(
            run.run_id, tick.tick_id, lease.fencing_token
        )
        outcome = DeterministicSnapshotScorer().score(run, publication)
        self.assertEqual(outcome.snapshot_id, tick.snapshot_id)
        self.assertTrue(outcome.predictions)
        self.assertTrue(
            all(item.action_risk <= item.baseline_risk for item in outcome.predictions)
        )


if __name__ == "__main__":
    unittest.main()
