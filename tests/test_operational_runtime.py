from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest

from heatsafe.models import DecisionConstraints, InterventionEvent
from heatsafe.operational_runtime import (
    BigQueryAcceleratedControlQueue,
    DurableAcceleratedRuntime,
    OperationalRuntimeError,
    RepositoryControlQueue,
    activate_simulated_plan,
    continue_without_intervention,
)
from heatsafe.services.preventive_planning import (
    build_accelerated_forecast_input,
    build_current_forecast_input,
    build_predictive_city_plan,
    project_city_forecast,
)
from heatsafe.simulation.engine import load_zone_priors
from heatsafe.simulation.repository import InMemorySimulationRepository
from heatsafe.simulation.scenario import load_scenario
from heatsafe.simulation.scoring import DeterministicSnapshotScorer
from tests.test_preventive_planning import (
    FakeCurrentEvidenceRepository,
    current_zones_with_one_city_weather,
)


class IdempotentAuditSpy:
    def __init__(self):
        self.events = {}
        self.calls = 0

    def approve(self, proposal):
        self.calls += 1
        event = self.events.get(proposal.proposal_id)
        if event is None:
            event = InterventionEvent(
                intervention_id=f"audit-{proposal.proposal_id}",
                proposal_id=proposal.proposal_id,
                approved_at=datetime.now(UTC),
                approved_by="test",
                actor_type="TEST",
                status="SIMULATED",
                dispatch_status="NOT_APPLICABLE",
                proposal=proposal,
            )
            self.events[proposal.proposal_id] = event
        return event


class BombControlQueue:
    def queue(self, *args, **kwargs):
        raise AssertionError("Current mode must never queue an accelerated control")


def build_current_plan():
    zones = current_zones_with_one_city_weather()
    evidence = build_current_forecast_input(
        FakeCurrentEvidenceRepository(zones), zones
    )
    return build_predictive_city_plan(
        project_city_forecast(evidence),
        DecisionConstraints(budget_cap_vnd=5_000_000),
    )


class DurableAcceleratedHarness:
    def __init__(self):
        self.fixture = load_scenario("hanoi_heatwave_v1")
        self.zones = load_zone_priors()
        self.repository = InMemorySimulationRepository()
        self.run = self.repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )
        self.runtime = DurableAcceleratedRuntime(
            self.repository,
            DeterministicSnapshotScorer(),
        )

    def advance(self, index):
        return self.runtime.advance(
            expected_tick_index=index,
            execution_id=f"execution-{index}",
        )

    def plan_at_completed_tick(self, index):
        tick = next(
            item
            for item in self.repository.ticks.values()
            if item.run_id == self.run.run_id and item.tick_index == index
        )
        publication = self.repository.published[tick.tick_id]
        evidence = build_accelerated_forecast_input(
            publication.result,
            fixture=self.fixture,
            zones=self.zones,
            durable_run_id=self.run.run_id,
            durable_tick_id=tick.tick_id,
            durable_snapshot_id=tick.snapshot_id,
        )
        return build_predictive_city_plan(
            project_city_forecast(evidence),
            DecisionConstraints(budget_cap_vnd=5_000_000),
        )


class CurrentOperationalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = build_current_plan()

    def test_activate_is_projected_idempotent_and_never_queues_controls(self):
        audit = IdempotentAuditSpy()
        first = activate_simulated_plan(
            self.plan,
            audit_store=audit,
            current_snapshot_id=self.plan.evidence_lineage.snapshot_id,
            control_queue=BombControlQueue(),
        )
        second = activate_simulated_plan(
            self.plan,
            audit_store=audit,
            current_snapshot_id=self.plan.evidence_lineage.snapshot_id,
            control_queue=BombControlQueue(),
        )

        self.assertEqual(first.status, "SIMULATED_PROJECTED")
        self.assertEqual(first.dispatch_status, "NOT_APPLICABLE")
        self.assertEqual(first.controls, ())
        self.assertEqual(first.receipt_id, second.receipt_id)
        self.assertEqual(
            first.approved_intervention_ids,
            second.approved_intervention_ids,
        )
        self.assertEqual(len(audit.events), len(self.plan.selected_zone_ids))
        self.assertEqual(len(first.projected_outcomes), 10)
        self.assertTrue(
            any(
                item.residual_risk_120m < item.baseline_risk_120m
                for item in first.projected_outcomes
            )
        )

    def test_new_snapshot_or_expiry_fails_closed_before_audit(self):
        audit = IdempotentAuditSpy()
        wrong_snapshot = activate_simulated_plan(
            self.plan,
            audit_store=audit,
            current_snapshot_id="new-current-snapshot",
        )
        expired_plan = replace(
            self.plan,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        expired = activate_simulated_plan(
            expired_plan,
            audit_store=audit,
            current_snapshot_id=expired_plan.evidence_lineage.snapshot_id,
        )

        self.assertEqual(wrong_snapshot.status, "STALE_PLAN")
        self.assertEqual(expired.status, "STALE_PLAN")
        self.assertEqual(audit.calls, 0)

    def test_continue_has_no_intervention_effect_or_audit(self):
        receipt = continue_without_intervention(
            self.plan,
            current_snapshot_id=self.plan.evidence_lineage.snapshot_id,
        )

        self.assertEqual(receipt.status, "CONTINUED")
        self.assertEqual(receipt.controls, ())
        self.assertTrue(
            all(
                item.residual_risk_120m == item.baseline_risk_120m
                for item in receipt.projected_outcomes
            )
        )

    def test_audit_failure_is_explicit_and_never_reaches_control_boundary(self):
        class FailingAudit:
            def approve(self, _proposal):
                raise RuntimeError("audit unavailable")

        receipt = activate_simulated_plan(
            self.plan,
            audit_store=FailingAudit(),
            current_snapshot_id=self.plan.evidence_lineage.snapshot_id,
            control_queue=BombControlQueue(),
        )

        self.assertEqual(receipt.status, "FAILED")
        self.assertEqual(receipt.error_code, "RuntimeError")
        self.assertEqual(receipt.dispatch_status, "NOT_APPLICABLE")
        self.assertEqual(receipt.controls, ())


class AcceleratedOperationalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.harness = DurableAcceleratedHarness()

    def test_exact_tick_and_heartbeat_manual_collision_are_idempotent(self):
        first = self.harness.advance(0)
        duplicate = self.harness.runtime.advance(
            expected_tick_index=0,
            execution_id="manual-same-cursor",
        )
        second = self.harness.advance(1)

        self.assertEqual(first.status, "ADVANCED")
        self.assertEqual(duplicate.status, "NO_OP_ALREADY_ADVANCED")
        self.assertEqual(second.status, "ADVANCED")
        self.assertEqual(
            second.simulation_time - first.simulation_time,
            timedelta(minutes=15),
        )
        self.assertEqual(
            self.harness.repository.status("heatwave").last_completed_tick_index,
            1,
        )

    def test_no_skip_and_off_boundary_tick_fail_before_publication(self):
        with self.assertRaisesRegex(
            OperationalRuntimeError, "next missing tick sequentially"
        ):
            self.harness.advance(2)

        tick = next(
            item
            for item in self.harness.repository.ticks.values()
            if item.run_id == self.harness.run.run_id and item.tick_index == 0
        )
        self.harness.repository.ticks[tick.tick_id] = replace(
            tick, simulation_time=tick.simulation_time + timedelta(minutes=1)
        )
        with self.assertRaisesRegex(Exception, "clock"):
            self.harness.advance(0)
        self.assertNotIn(tick.tick_id, self.harness.repository.published)

    def test_activate_queues_exact_controls_and_old_plan_becomes_stale(self):
        self.harness.advance(0)
        plan = self.harness.plan_at_completed_tick(0)
        audit = IdempotentAuditSpy()
        queue = RepositoryControlQueue(self.harness.repository)

        first = activate_simulated_plan(
            plan,
            audit_store=audit,
            accelerated_repository=self.harness.repository,
            control_queue=queue,
            execution_id="cloud-run-control-1",
        )
        duplicate = activate_simulated_plan(
            plan,
            audit_store=audit,
            accelerated_repository=self.harness.repository,
            control_queue=queue,
            execution_id="cloud-run-control-2",
        )

        self.assertEqual(first.status, "SIMULATED_QUEUED")
        self.assertEqual(first.dispatch_status, "NOT_APPLICABLE")
        self.assertTrue(first.controls)
        self.assertEqual(first.receipt_id, duplicate.receipt_id)
        self.assertEqual(
            len(self.harness.repository.controls),
            len({item.control_id for item in first.controls}),
        )
        self.assertTrue(
            all(item.requested_minute >= 15 for item in first.controls)
        )

        advanced = self.harness.advance(1)
        self.assertNotEqual(
            advanced.actual_checksum, advanced.shadow_checksum
        )
        stale = activate_simulated_plan(
            plan,
            audit_store=audit,
            accelerated_repository=self.harness.repository,
            control_queue=queue,
            execution_id="cloud-run-control-stale",
        )
        self.assertEqual(stale.status, "STALE_PLAN")

    def test_bigquery_queue_preserves_exact_plan_lineage(self):
        self.harness.advance(0)
        plan = self.harness.plan_at_completed_tick(0)
        proposal = next(
            row.best_window.proposal
            for row in plan.rows
            if row.zone_id in plan.selected_zone_ids
            and row.best_window is not None
        )

        class Writer:
            def __init__(self):
                self.kwargs = None

            def queue_many(self, **kwargs):
                self.kwargs = kwargs
                return (
                    SimpleNamespace(
                        pause_controls=("durable-control",)
                    ),
                )

        writer = Writer()
        controls = BigQueryAcceleratedControlQueue(writer).queue_plan(
            plan,
            (proposal,),
            execution_id="cloud-run-execution",
        )

        self.assertEqual(controls, ("durable-control",))
        self.assertEqual(
            writer.kwargs["run_id"],
            plan.evidence_lineage.simulation_run_id,
        )
        self.assertEqual(
            writer.kwargs["source_tick_id"], plan.evidence_lineage.tick_id
        )
        self.assertEqual(
            writer.kwargs["source_snapshot_id"],
            plan.evidence_lineage.snapshot_id,
        )
        self.assertEqual(
            writer.kwargs["request_execution_id"],
            "cloud-run-execution",
        )
        self.assertEqual(
            writer.kwargs["proposal_ids"],
            (proposal.proposal_id,),
        )


if __name__ == "__main__":
    unittest.main()
