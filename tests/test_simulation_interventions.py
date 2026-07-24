from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
import unittest
from unittest.mock import patch

from heatsafe.simulation.cli import main
from heatsafe.simulation.control import (
    BigQueryControlWriter,
    ControlValidationError,
    canonical_proposal_checksum,
    validate_control_payload,
)
from heatsafe.simulation.models import DriverStatus, PauseControl
from heatsafe.simulation.repository import (
    InMemorySimulationRepository,
    SimulationRepositoryError,
)
from heatsafe.simulation.scoring import DeterministicSnapshotScorer


def _payload(
    *,
    now: datetime,
    run_id: str = "run-1",
    tick_id: str = "tick-0",
    snapshot_id: str = "snapshot-0",
):
    decisions = [
        {
            "driver_id_hash": "driver-a",
            "baseline_risk": 0.72,
            "action_risk": 0.48,
            "pause_start_delay_minutes": 0,
            "pause_duration_minutes": 15,
        },
        {
            "driver_id_hash": "driver-b",
            "baseline_risk": 0.66,
            "action_risk": 0.51,
            "pause_start_delay_minutes": 15,
            "pause_duration_minutes": 15,
        },
    ]
    return {
        "proposal_id": "proposal-1",
        "scenario_id": "heatwave",
        "simulation_run_id": run_id,
        "source_tick_id": tick_id,
        "source_snapshot_id": snapshot_id,
        "within_guardrails": True,
        "selected_drivers": len(decisions),
        "driver_decisions": decisions,
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
    }


class ControlContractTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 24, tzinfo=UTC)

    def validate(self, payload, **overrides):
        values = {
            "scenario_id": "heatwave",
            "run_id": "run-1",
            "source_tick_id": "tick-0",
            "source_snapshot_id": "snapshot-0",
            "source_tick_index": 0,
            "now": self.now,
            "simulation_time": datetime(2026, 5, 26, tzinfo=UTC),
        }
        values.update(overrides)
        return validate_control_payload(payload, **values)

    def test_valid_payload_is_grouped_into_deterministic_waves(self):
        payload = _payload(now=self.now)
        queued = self.validate(payload)
        self.assertEqual(queued.payload_checksum, canonical_proposal_checksum(payload))
        self.assertEqual(queued.selected_driver_count, 2)
        self.assertEqual(len(queued.pause_controls), 2)
        self.assertEqual(
            [item.requested_minute for item in queued.pause_controls], [15, 30]
        )

    def test_lineage_payload_expiry_and_cap_fail_closed(self):
        payload = _payload(now=self.now)
        payload["simulation_run_id"] = "other"
        with self.assertRaisesRegex(ControlValidationError, "lineage"):
            self.validate(payload)
        payload = _payload(now=self.now)
        payload["expires_at"] = (self.now - timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ControlValidationError, "expired"):
            self.validate(payload)
        with self.assertRaisesRegex(ControlValidationError, "cap"):
            self.validate(_payload(now=self.now), max_selected_drivers=1)

    def test_mutated_or_duplicate_driver_payload_is_rejected(self):
        payload = _payload(now=self.now)
        payload["selected_drivers"] = 3
        with self.assertRaisesRegex(ControlValidationError, "count"):
            self.validate(payload)
        payload = _payload(now=self.now)
        payload["driver_decisions"][1]["driver_id_hash"] = "driver-a"
        with self.assertRaisesRegex(ControlValidationError, "unique"):
            self.validate(payload)

    def test_public_local_cli_has_no_queue_control_authority(self):
        with patch.dict(os.environ, {}, clear=True):
            code = main([
                "queue-control", "--memory",
                "--proposal-id", "p", "--run-id", "r",
                "--source-tick-id", "t", "--source-snapshot-id", "s",
            ])
        self.assertEqual(code, 2)

    def test_bigquery_writer_uses_fixed_actor_and_exact_scored_lineage(self):
        payload = _payload(now=self.now)

        class Done:
            def __init__(self, rows):
                self.rows = rows

            def result(self):
                return self.rows

        class Client:
            def __init__(self):
                self.queries = []

            def query(self, sql, job_config):
                self.queries.append((sql, job_config))
                if len(self.queries) == 1:
                    return Done([{
                        "proposal_json": payload,
                        "scenario_id": "heatwave",
                        "tick_index": 0,
                        "simulation_time": datetime(2026, 5, 26, tzinfo=UTC),
                    }])
                return Done([])

        client = Client()
        queued = BigQueryControlWriter(
            client, dataset="project.dataset", now=lambda: self.now
        ).queue(
            proposal_id="proposal-1",
            run_id="run-1",
            source_tick_id="tick-0",
            source_snapshot_id="snapshot-0",
            request_execution_id="job-execution-1",
        )
        self.assertEqual(queued.selected_driver_count, 2)
        self.assertIn("t.status = 'SUCCEEDED'", client.queries[0][0])
        self.assertIn("t.error_code, '') != 'MODEL_INPUT_OOD'", client.queries[0][0])
        self.assertIn("simulation_control_events", client.queries[1][0])
        values = {
            parameter.name: parameter.value
            for parameter in client.queries[1][1].query_parameters
        }
        self.assertEqual(values["actor_type"], "TRUSTED_OPERATOR")
        self.assertEqual(values["requested_by"], "heatsafe-simulation-control")


class ClosedLoopRepositoryTests(unittest.TestCase):
    def _first_tick(self, repository, run):
        return min(
            (item for item in repository.ticks.values() if item.run_id == run.run_id),
            key=lambda item: item.tick_index,
        )

    def _publish_finalize(self, repository, run, tick):
        lease = repository.acquire_tick_lease(run.run_id, tick.tick_id, "test")
        publication = repository.publish_tick(
            run.run_id, tick.tick_id, lease.fencing_token
        )
        repository.finalize_score(run.run_id, tick.tick_id, succeeded=True)
        return publication

    def test_control_changes_only_selected_driver_and_publishes_receipt(self):
        repository = InMemorySimulationRepository()
        run = repository.start(
            scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=42
        )
        first = self._first_tick(repository, run)
        first_publication = self._publish_finalize(repository, run, first)
        selected = next(
            row for row in first_publication.driver_rows
            if row["status"] == DriverStatus.IDLE.value
        )
        control_driver = next(
            row for row in first_publication.driver_rows
            if row["zone_id"] == selected["zone_id"]
            and row["driver_id_hash"] != selected["driver_id_hash"]
        )
        control = PauseControl(
            control_id="event-1:0:15",
            control_event_id="event-1",
            proposal_id="proposal-1",
            driver_ids=(str(selected["driver_id_hash"]),),
            requested_minute=15,
            pause_start_delay_minutes=0,
            pause_duration_minutes=15,
            baseline_risk_by_driver=((str(selected["driver_id_hash"]), 0.7),),
            action_risk_by_driver=((str(selected["driver_id_hash"]), 0.45),),
        )
        repository.queue_controls((control,))
        repository.queue_controls((control,))  # exact duplicate is idempotent
        second = next(
            item for item in repository.ticks.values()
            if item.run_id == run.run_id and item.tick_index == 1
        )
        publication = self._publish_finalize(repository, run, second)
        selected_after = next(
            row for row in publication.driver_rows
            if row["driver_id_hash"] == selected["driver_id_hash"]
        )
        control_after = next(
            row for row in publication.driver_rows
            if row["driver_id_hash"] == control_driver["driver_id_hash"]
        )
        self.assertIsNotNone(selected_after["current_intervention_id"])
        self.assertIsNone(control_after["current_intervention_id"])
        self.assertTrue(publication.intervention_rows)
        self.assertEqual(len(publication.consumption_rows), 1)
        self.assertEqual(publication.consumption_rows[0]["outcome"], "APPLIED")

    def test_same_control_identity_cannot_change_payload(self):
        repository = InMemorySimulationRepository()
        first = PauseControl("control-1", ("driver-a",), 15, 15)
        changed = PauseControl("control-1", ("driver-b",), 15, 15)
        repository.queue_controls((first,))
        with self.assertRaisesRegex(SimulationRepositoryError, "payload changed"):
            repository.queue_controls((changed,))

    def test_recovered_driver_next_baseline_reflects_pause_state(self):
        repository = InMemorySimulationRepository()
        run = repository.start(
            scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=77
        )
        first = self._first_tick(repository, run)
        first_publication = self._publish_finalize(repository, run, first)
        selected_state = next(
            driver for driver in first_publication.result.state.drivers
            if driver.status == DriverStatus.IDLE and driver.scheduled_at(45)
        )
        scorer = DeterministicSnapshotScorer()
        first_score = scorer.score(run, first_publication)
        initial_risk = next(
            item.baseline_risk for item in first_score.predictions
            if item.driver_id_hash == selected_state.driver_id_hash
        )
        repository.queue_controls((PauseControl(
            control_id="recovery-event:0:15",
            control_event_id="recovery-event",
            proposal_id="recovery-proposal",
            driver_ids=(selected_state.driver_id_hash,),
            requested_minute=15,
            pause_duration_minutes=15,
        ),))
        for tick_index in (1, 2):
            tick = next(
                item for item in repository.ticks.values()
                if item.run_id == run.run_id and item.tick_index == tick_index
            )
            publication = self._publish_finalize(repository, run, tick)
        recovered_score = scorer.score(run, publication)
        recovered = [
            item for item in recovered_score.predictions
            if item.driver_id_hash == selected_state.driver_id_hash
        ]
        self.assertNotEqual(first_score.snapshot_id, recovered_score.snapshot_id)
        self.assertTrue(recovered)
        self.assertLess(recovered[0].baseline_risk, initial_risk)


if __name__ == "__main__":
    unittest.main()
