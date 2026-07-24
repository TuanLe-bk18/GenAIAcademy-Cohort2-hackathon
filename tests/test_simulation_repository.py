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


class SimulationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.clock = datetime(2026, 5, 26, tzinfo=UTC)
        self.repository = InMemorySimulationRepository(
            now=lambda: self.clock,
            lease_seconds=360,
        )
        self.run = self.repository.start(
            scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=42
        )
        self.first_tick = next(
            tick for tick in self.repository.ticks.values()
            if tick.run_id == self.run.run_id and tick.tick_index == 0
        )

    def _publish_first_tick(self):
        lease = self.repository.acquire_tick_lease(
            self.run.run_id, self.first_tick.tick_id, "client-a"
        )
        return self.repository.publish_tick(
            self.run.run_id, self.first_tick.tick_id, lease.fencing_token
        )

    def test_start_precreates_one_run_and_ninety_six_immutable_tick_ids(self):
        ticks = [tick for tick in self.repository.ticks.values() if tick.run_id == self.run.run_id]
        self.assertEqual(len(ticks), 96)
        self.assertEqual(len({tick.tick_id for tick in ticks}), 96)
        with self.assertRaises(RunConflict):
            self.repository.start(
                scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=43
            )

    def test_only_the_fencing_token_can_publish(self):
        lease = self.repository.acquire_tick_lease(
            self.run.run_id, self.first_tick.tick_id, "client-a"
        )
        with self.assertRaises(LeaseConflict):
            self.repository.acquire_tick_lease(
                self.run.run_id, self.first_tick.tick_id, "client-b"
            )
        with self.assertRaises(LeaseConflict):
            self.repository.publish_tick(self.run.run_id, self.first_tick.tick_id, "client-a")
        publication = self.repository.publish_tick(
            self.run.run_id, self.first_tick.tick_id, lease.fencing_token
        )
        self.assertEqual(publication.tick.status, "SNAPSHOT_READY")

    def test_expired_lease_cannot_publish(self):
        lease = self.repository.acquire_tick_lease(
            self.run.run_id, self.first_tick.tick_id, "client-a"
        )
        self.clock += timedelta(seconds=361)
        with self.assertRaises(LeaseConflict):
            self.repository.publish_tick(
                self.run.run_id, self.first_tick.tick_id, lease.fencing_token
            )

    def test_publication_has_coherent_lineage_and_exactly_ten_zone_rows(self):
        publication = self._publish_first_tick()
        self.assertEqual(len(publication.driver_rows), 6_230)
        self.assertEqual(len(publication.zone_rows), 10)
        self.assertTrue(publication.order_rows)
        self.assertTrue(all(row["simulation_run_id"] == self.run.run_id for row in publication.driver_rows))
        self.assertTrue(all(row["tick_id"] == self.first_tick.tick_id for row in publication.zone_rows))
        current = self.repository.status("heatwave")
        self.assertEqual(current.last_published_tick_index, 0)
        self.assertEqual(current.pending_score_tick_id, self.first_tick.tick_id)
        self.assertIsNone(current.last_completed_tick_index)

    def test_snapshot_ready_retry_is_a_no_republish(self):
        first = self._publish_first_tick()
        second = self.repository.publish_tick(self.run.run_id, self.first_tick.tick_id, "ignored")
        self.assertIs(first, second)
        self.assertEqual(len(self.repository.published), 1)

    def test_score_finalization_advances_completed_cursor_once(self):
        self._publish_first_tick()
        completed = self.repository.finalize_score(
            self.run.run_id, self.first_tick.tick_id, succeeded=True
        )
        self.assertEqual(completed.last_completed_tick_index, 0)
        self.assertIsNone(completed.pending_score_tick_id)
        retry = self.repository.finalize_score(
            self.run.run_id, self.first_tick.tick_id, succeeded=True
        )
        self.assertEqual(retry.last_completed_tick_index, 0)

    def test_score_failure_does_not_advance_completed_cursor(self):
        self._publish_first_tick()
        result = self.repository.finalize_score(
            self.run.run_id, self.first_tick.tick_id, succeeded=False
        )
        self.assertIsNone(result.last_completed_tick_index)
        self.assertEqual(result.pending_score_tick_id, self.first_tick.tick_id)
        with self.assertRaises(SimulationRepositoryError):
            self.repository.finalize_score(
                self.run.run_id, self.first_tick.tick_id, succeeded=True
            )

    def test_pending_score_blocks_a_later_tick(self):
        self._publish_first_tick()
        second = next(
            tick for tick in self.repository.ticks.values()
            if tick.run_id == self.run.run_id and tick.tick_index == 1
        )
        with self.assertRaisesRegex(SimulationRepositoryError, "pending score"):
            self.repository.acquire_tick_lease(self.run.run_id, second.tick_id, "client-b")


class BigQueryPublisherShapeTests(unittest.TestCase):
    class Done:
        def result(self):
            return None

    class Client:
        def __init__(self):
            self.sql = ""
            self.config = None

        def query(self, sql, job_config):
            self.sql = sql
            self.config = job_config
            return BigQueryPublisherShapeTests.Done()

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
        self.assertLess(client.sql.rfind("SNAPSHOT_READY"), client.sql.rfind("COMMIT TRANSACTION"))
        self.assertEqual(client.config.maximum_bytes_billed, 250_000_000)


if __name__ == "__main__":
    unittest.main()
