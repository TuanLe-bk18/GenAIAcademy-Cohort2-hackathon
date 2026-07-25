from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import unittest

from heatsafe.config import Settings
from heatsafe.simulation.checkpoint import (
    InMemoryCheckpointStore,
    encode_checkpoint,
)
from heatsafe.simulation.engine import (
    advance_tick,
    load_scenario,
    load_zone_priors,
)
from heatsafe.simulation.repository import (
    BigQuerySimulationRepository,
    InMemorySimulationRepository,
    SimulationRepositoryError,
)
from heatsafe.simulation.scoring import BigQuerySnapshotScorer
from heatsafe.simulation.telemetry import TickTelemetry
from infra.ml_pipeline import score_snapshot


FIXTURE_START = datetime.fromisoformat("2026-05-26T00:00:00+07:00")


class _Done:
    def result(self):
        return ()


class _RecordingClient:
    def __init__(self):
        self.queries: list[tuple[str, object]] = []

    def query(self, sql, job_config=None):
        self.queries.append((sql, job_config))
        return _Done()


def _query_parameter(config, name: str):
    return next(
        parameter.value
        for parameter in config.query_parameters
        if parameter.name == name
    )


class ReplayClockRepositoryTests(unittest.TestCase):
    def test_fixture_epoch_owns_run_and_tick_zero_four_ninety_five(self):
        wall_clock = datetime(2026, 7, 25, 4, 0, tzinfo=UTC)
        repository = InMemorySimulationRepository(now=lambda: wall_clock)

        run = repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )

        self.assertEqual(run.start_time, FIXTURE_START)
        ticks = {
            tick.tick_index: tick
            for tick in repository.ticks.values()
            if tick.run_id == run.run_id
        }
        for index in (0, 4, 95):
            with self.subTest(index=index):
                self.assertEqual(
                    ticks[index].simulation_time,
                    FIXTURE_START + timedelta(minutes=15 * index),
                )

    def test_scenario_id_must_match_fixture_manifest(self):
        repository = InMemorySimulationRepository()
        with self.assertRaisesRegex(
            SimulationRepositoryError, "scenario.*fixture"
        ):
            repository.start(
                scenario_id="live",
                scenario_version="hanoi_heatwave_v1",
                seed=42,
            )

    def test_fixture_epoch_does_not_replace_wall_clock_for_lease_expiry(self):
        wall_clock = datetime(2026, 7, 25, 4, 0, tzinfo=UTC)
        repository = InMemorySimulationRepository(
            now=lambda: wall_clock,
            lease_seconds=360,
        )
        run = repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )
        tick = min(
            (
                item
                for item in repository.ticks.values()
                if item.run_id == run.run_id
            ),
            key=lambda item: item.tick_index,
        )

        lease = repository.acquire_tick_lease(
            run.run_id, tick.tick_id, "clock-test"
        )

        self.assertEqual(run.start_time, FIXTURE_START)
        self.assertEqual(
            lease.expires_at, wall_clock + timedelta(seconds=360)
        )

    def test_publication_rejects_ledger_engine_clock_drift(self):
        repository = InMemorySimulationRepository()
        run = repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )
        tick = min(
            (
                item
                for item in repository.ticks.values()
                if item.run_id == run.run_id
            ),
            key=lambda item: item.tick_index,
        )
        repository.ticks[tick.tick_id] = replace(
            tick, simulation_time=tick.simulation_time + timedelta(days=60)
        )
        lease = repository.acquire_tick_lease(
            run.run_id, tick.tick_id, "clock-test"
        )

        with self.assertRaisesRegex(
            SimulationRepositoryError, "replay clock"
        ):
            repository.publish_tick(
                run.run_id, tick.tick_id, lease.fencing_token
            )

    def test_bigquery_start_parameter_uses_fixture_epoch(self):
        client = _RecordingClient()
        repository = BigQuerySimulationRepository(
            client,
            dataset="project.dataset",
            now=lambda: datetime(2026, 7, 25, 4, 0, tzinfo=UTC),
        )

        run = repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )

        config = next(
            config
            for _, config in client.queries
            if any(
                parameter.name == "start_time"
                for parameter in config.query_parameters
            )
        )
        self.assertEqual(run.start_time, FIXTURE_START)
        self.assertEqual(_query_parameter(config, "start_time"), FIXTURE_START)

    def test_incompatible_checkpoint_clock_falls_back_before_advancing(self):
        store = InMemoryCheckpointStore()
        repository = InMemorySimulationRepository(
            checkpoint_store=store,
            state_mode="checkpoint",
        )
        run = repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )
        ticks = sorted(
            (
                item
                for item in repository.ticks.values()
                if item.run_id == run.run_id
            ),
            key=lambda item: item.tick_index,
        )
        first_lease = repository.acquire_tick_lease(
            run.run_id, ticks[0].tick_id, "clock-test-0"
        )
        first = repository.publish_tick(
            run.run_id, ticks[0].tick_id, first_lease.fencing_token
        )
        repository.finalize_score(
            run.run_id, ticks[0].tick_id, succeeded=True
        )
        persisted_first = repository.ticks[ticks[0].tick_id]
        bad = encode_checkpoint(
            replace(
                first.result.state,
                start_time=first.result.state.start_time + timedelta(days=1),
            )
        )
        object_name = persisted_first.checkpoint_object_name
        assert object_name is not None
        _, generation = store.objects[object_name]
        store.objects[object_name] = (bad.data, generation)
        repository.ticks[ticks[0].tick_id] = replace(
            persisted_first,
            checkpoint_compressed_size=len(bad.data),
            checkpoint_expanded_size=bad.expanded_size,
            checkpoint_payload_sha256=bad.payload_sha256,
            checkpoint_state_checksum=bad.state_checksum,
        )

        lines: list[str] = []
        telemetry = TickTelemetry(sink=lines.append, state_mode="checkpoint")
        with telemetry.activate():
            second_lease = repository.acquire_tick_lease(
                run.run_id, ticks[1].tick_id, "clock-test-1"
            )
            second = repository.publish_tick(
                run.run_id, ticks[1].tick_id, second_lease.fencing_token
            )

        expected = advance_tick(
            first.result.state,
            fixture=load_scenario("hanoi_heatwave_v1"),
            zones=load_zone_priors(),
        )
        self.assertEqual(second.result.checksum, expected.checksum)
        self.assertTrue(
            any(
                json.loads(line).get("error_code") == "CHECKPOINT_FALLBACK"
                for line in lines
            )
        )


class ReplayClockScoringTests(unittest.TestCase):
    def test_simulation_clock_assertion_precedes_scoring_mutations_and_forecast(self):
        client = _RecordingClient()
        score_snapshot(
            Settings(dataset_id="clock_contract"),
            client,
            scenario="heatwave",
            model_version="model-v1",
            feature_source="simulation",
            simulation_run_id="run-1",
            tick_id="tick-4",
            snapshot_id="snapshot-4",
            simulation_time=FIXTURE_START + timedelta(hours=1),
        )

        sql, _ = client.queries[0]
        guard = sql.index(
            "simulation replay clock and lineage must match before scoring"
        )
        self.assertLess(
            guard,
            sql.index("DELETE FROM `cohort2track2.clock_contract.driver_current_features`"),
        )
        context_guard = sql.index(
            "forecast context must end at the current simulation time"
        )
        self.assertLess(context_guard, sql.index("FROM AI.FORECAST("))
        self.assertIn("simulation_run.simulation_start_at", sql)
        self.assertIn("tick.simulation_time = @simulation_time", sql)
        self.assertIn(
            "COUNTIF(demand.interval_start != @simulation_time) = 0",
            sql,
        )

    def test_bigquery_scorer_rejects_clock_drift_before_provider_query(self):
        repository = InMemorySimulationRepository()
        run = repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )
        tick = min(
            (
                item
                for item in repository.ticks.values()
                if item.run_id == run.run_id
            ),
            key=lambda item: item.tick_index,
        )
        lease = repository.acquire_tick_lease(
            run.run_id, tick.tick_id, "clock-test"
        )
        publication = repository.publish_tick(
            run.run_id, tick.tick_id, lease.fencing_token
        )
        drifted = replace(
            publication,
            tick=replace(
                publication.tick,
                simulation_time=publication.tick.simulation_time
                + timedelta(days=60),
            ),
        )
        client = _RecordingClient()

        with self.assertRaisesRegex(
            SimulationRepositoryError, "replay clock"
        ):
            BigQuerySnapshotScorer(
                client, settings=Settings(dataset_id="clock_contract")
            ).score(run, drifted)

        self.assertEqual(client.queries, [])


if __name__ == "__main__":
    unittest.main()
