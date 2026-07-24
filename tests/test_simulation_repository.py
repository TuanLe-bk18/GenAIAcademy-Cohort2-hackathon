from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from heatsafe.simulation.repository import (
    BigQuerySimulationRepository,
    InMemorySimulationRepository,
    LeaseConflict,
    RunConflict,
    SimulationRepositoryError,
)
from infra.provision_gcp import table_schemas


class SimulationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.clock = datetime(2026, 5, 26, tzinfo=UTC)
        self.repository = InMemorySimulationRepository(
            now=lambda: self.clock,
            lease_seconds=360,
        )
        self.simulation_run = self.repository.start(
            scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=42
        )
        self.first_tick = next(
            tick for tick in self.repository.ticks.values()
            if tick.run_id == self.simulation_run.run_id and tick.tick_index == 0
        )

    def _publish_first_tick(self):
        lease = self.repository.acquire_tick_lease(
            self.simulation_run.run_id, self.first_tick.tick_id, "client-a"
        )
        return self.repository.publish_tick(
            self.simulation_run.run_id, self.first_tick.tick_id, lease.fencing_token
        )

    def test_start_precreates_one_run_and_ninety_six_immutable_tick_ids(self):
        ticks = [tick for tick in self.repository.ticks.values() if tick.run_id == self.simulation_run.run_id]
        self.assertEqual(len(ticks), 96)
        self.assertEqual(len({tick.tick_id for tick in ticks}), 96)
        with self.assertRaises(RunConflict):
            self.repository.start(
                scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=43
            )

    def test_only_the_fencing_token_can_publish(self):
        lease = self.repository.acquire_tick_lease(
            self.simulation_run.run_id, self.first_tick.tick_id, "client-a"
        )
        with self.assertRaises(LeaseConflict):
            self.repository.acquire_tick_lease(
                self.simulation_run.run_id, self.first_tick.tick_id, "client-b"
            )
        with self.assertRaises(LeaseConflict):
            self.repository.publish_tick(self.simulation_run.run_id, self.first_tick.tick_id, "client-a")
        publication = self.repository.publish_tick(
            self.simulation_run.run_id, self.first_tick.tick_id, lease.fencing_token
        )
        self.assertEqual(publication.tick.status, "SNAPSHOT_READY")

    def test_expired_lease_cannot_publish(self):
        lease = self.repository.acquire_tick_lease(
            self.simulation_run.run_id, self.first_tick.tick_id, "client-a"
        )
        self.clock += timedelta(seconds=361)
        with self.assertRaises(LeaseConflict):
            self.repository.publish_tick(
                self.simulation_run.run_id, self.first_tick.tick_id, lease.fencing_token
            )

    def test_publication_has_coherent_lineage_and_exactly_ten_zone_rows(self):
        publication = self._publish_first_tick()
        self.assertEqual(len(publication.driver_rows), 6_230)
        self.assertEqual(len(publication.zone_rows), 10)
        self.assertTrue(publication.order_rows)
        self.assertTrue(all(row["simulation_run_id"] == self.simulation_run.run_id for row in publication.driver_rows))
        self.assertTrue(all(row["tick_id"] == self.first_tick.tick_id for row in publication.zone_rows))
        schemas = table_schemas()
        for table_name, rows in {
            "driver_simulation_state": publication.driver_rows,
            "zone_snapshots_current": publication.zone_rows,
            "order_events": publication.order_rows,
            "weather_observations": publication.weather_rows,
            "zone_operations": publication.operation_rows,
            "demand_history": publication.demand_rows,
            "driver_state_history": publication.driver_history_rows,
        }.items():
            required = {
                field.name for field in schemas[table_name] if field.mode == "REQUIRED"
            }
            self.assertTrue(required <= set(rows[0]), table_name)
        current = self.repository.status("heatwave")
        assert current is not None
        self.assertEqual(current.last_published_tick_index, 0)
        self.assertEqual(current.pending_score_tick_id, self.first_tick.tick_id)
        self.assertIsNone(current.last_completed_tick_index)

    def test_snapshot_ready_retry_is_a_no_republish(self):
        first = self._publish_first_tick()
        second = self.repository.publish_tick(self.simulation_run.run_id, self.first_tick.tick_id, "ignored")
        self.assertIs(first, second)
        self.assertEqual(len(self.repository.published), 1)

    def test_durable_snapshot_retry_rebuilds_only_the_local_projection_cache(self):
        self._publish_first_tick()
        self.repository.published.clear()  # Mimic a restarted worker process.
        retry = self.repository.publish_tick(
            self.simulation_run.run_id, self.first_tick.tick_id, "ignored"
        )
        self.assertEqual(retry.tick.status, "SNAPSHOT_READY")
        self.assertEqual(len(self.repository.published), 1)

    def test_score_finalization_advances_completed_cursor_once(self):
        self._publish_first_tick()
        completed = self.repository.finalize_score(
            self.simulation_run.run_id, self.first_tick.tick_id, succeeded=True
        )
        self.assertEqual(completed.last_completed_tick_index, 0)
        self.assertIsNone(completed.pending_score_tick_id)
        retry = self.repository.finalize_score(
            self.simulation_run.run_id, self.first_tick.tick_id, succeeded=True
        )
        self.assertEqual(retry.last_completed_tick_index, 0)

    def test_score_failure_does_not_advance_completed_cursor(self):
        self._publish_first_tick()
        result = self.repository.finalize_score(
            self.simulation_run.run_id, self.first_tick.tick_id, succeeded=False
        )
        self.assertIsNone(result.last_completed_tick_index)
        self.assertEqual(result.pending_score_tick_id, self.first_tick.tick_id)
        with self.assertRaises(SimulationRepositoryError):
            self.repository.finalize_score(
                self.simulation_run.run_id, self.first_tick.tick_id, succeeded=True
            )

    def test_pending_score_blocks_a_later_tick(self):
        self._publish_first_tick()
        second = next(
            tick for tick in self.repository.ticks.values()
            if tick.run_id == self.simulation_run.run_id and tick.tick_index == 1
        )
        with self.assertRaisesRegex(SimulationRepositoryError, "pending score"):
            self.repository.acquire_tick_lease(self.simulation_run.run_id, second.tick_id, "client-b")


class BigQueryPublisherShapeTests(unittest.TestCase):
    class Done:
        def result(self):
            return None

    class Client:
        def __init__(self):
            self.sql = ""
            self.config = None
            self.queries = []
            self.staging_tables = []
            self.staged_rows = []

        def query(self, sql, job_config):
            self.sql = sql
            self.config = job_config
            self.queries.append(sql)
            return BigQueryPublisherShapeTests.Done()

        def load_table_from_json(self, _rows, table_id, **_kwargs):
            self.staging_tables.append(table_id)
            self.staged_rows.extend(_rows)
            return BigQueryPublisherShapeTests.Done()

        def get_table(self, _table_id):
            return type("Table", (), {"schema": []})()

        def update_table(self, *_args):
            return None

    def test_fenced_transaction_uses_byte_cap_and_snapshot_ready_last(self):
        client = self.Client()
        repository = BigQuerySimulationRepository(
            client, dataset="project.dataset", now=lambda: datetime(2026, 5, 26, tzinfo=UTC)
        )
        run = repository.start(
            scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=42
        )
        tick = next(tick for tick in repository.ticks.values() if tick.run_id == run.run_id and tick.tick_index == 0)
        lease = repository.acquire_tick_lease(run.run_id, tick.tick_id, "client")
        repository.publish_tick(run.run_id, tick.tick_id, lease.fencing_token)
        self.assertIn("BEGIN TRANSACTION", client.sql)
        self.assertIn("lease_owner = @lease_owner", client.sql)
        self.assertIn("SNAPSHOT_READY", client.sql)
        self.assertIn("order_events", client.sql)
        self.assertIn("weather_observations", client.sql)
        self.assertIn("zone_operations", client.sql)
        self.assertIn("demand_history", client.sql)
        self.assertIn("driver_state_history", client.sql)
        self.assertIn("last_published_tick_index = @tick_index", client.sql)
        self.assertLess(client.sql.index("last_published_tick_index"), client.sql.index("SET status = 'SNAPSHOT_READY'"))
        self.assertEqual(len(client.staging_tables), 7)
        self.assertTrue(all("__simulation_stage_" in table for table in client.staging_tables))
        self.assertTrue(all(not isinstance(value, datetime) for row in client.staged_rows for value in row.values()))
        self.assertLess(client.sql.rfind("SNAPSHOT_READY"), client.sql.rfind("COMMIT TRANSACTION"))
        assert client.config is not None
        self.assertEqual(client.config.maximum_bytes_billed, 250_000_000)

    def test_run_lifecycle_uses_precreation_conditional_lease_and_separate_score_cursor(self):
        client = self.Client()
        repository = BigQuerySimulationRepository(
            client, dataset="project.dataset", now=lambda: datetime(2026, 5, 26, tzinfo=UTC)
        )
        run = repository.start(
            scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=42
        )
        tick = next(tick for tick in repository.ticks.values() if tick.run_id == run.run_id and tick.tick_index == 0)
        lease = repository.acquire_tick_lease(run.run_id, tick.tick_id, "client")
        repository.publish_tick(run.run_id, tick.tick_id, lease.fencing_token)
        repository.finalize_score(run.run_id, tick.tick_id, succeeded=True)
        all_sql = "\n".join(client.queries)
        self.assertIn("GENERATE_ARRAY(0, 95)", all_sql)
        self.assertIn("LOWER(TO_HEX(SHA256", all_sql)
        self.assertIn("SUBSTR(LOWER(TO_HEX(SHA256", all_sql)
        self.assertIn("active_simulation_run_id", all_sql)
        self.assertIn("lease_owner = @lease_owner", all_sql)
        self.assertIn("pending_score_tick_id", all_sql)
        self.assertIn("last_completed_tick_index", all_sql)

    def test_reload_maps_bigquery_simulation_run_id_to_repository_run_id(self):
        client = self.Client()
        repository = BigQuerySimulationRepository(
            client, dataset="project.dataset", now=lambda: datetime(2026, 5, 26, tzinfo=UTC)
        )
        run = repository.start(
            scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=42
        )
        repository.ticks.clear()
        repository._query = lambda _sql, _params: [{
            "simulation_run_id": run.run_id,
            "scenario_id": run.scenario_id,
            "tick_id": "tick-0",
            "tick_index": 0,
            "simulation_time": run.start_time,
            "snapshot_id": "snapshot-0",
            "status": "SUCCEEDED",
            "lease_owner": None,
            "lease_expires_at": None,
            "input_checksum": "input",
            "output_checksum": "output",
        }]
        repository._load_ticks(run)
        self.assertEqual(repository.ticks["tick-0"].run_id, run.run_id)


if __name__ == "__main__":
    unittest.main()
