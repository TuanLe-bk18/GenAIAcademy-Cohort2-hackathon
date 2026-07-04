from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from heatsafe.audit import InterventionAuditStore
from heatsafe.copilot import HeatSafeCopilot
from heatsafe.ingestion import calculate_heat_index
from heatsafe.repository import SnapshotRepository
from heatsafe.risk import heat_tier, operational_priority
from heatsafe.safepause import simulate_safepause


class RiskTests(unittest.TestCase):
    def test_heat_tier_boundaries(self):
        self.assertEqual(heat_tier(26.9), "NORMAL")
        self.assertEqual(heat_tier(27), "CAUTION")
        self.assertEqual(heat_tier(32), "EXTREME_CAUTION")
        self.assertEqual(heat_tier(39), "DANGER")
        self.assertEqual(heat_tier(52), "EXTREME_DANGER")

    def test_priority_uses_exposure_duration(self):
        zone = SnapshotRepository().load().zones[0]
        self.assertEqual(operational_priority(zone), 80)
        self.assertEqual(operational_priority(replace(zone, exposed_2h=0, exposed_4h=0)), 60)

    def test_heat_index_formula_handles_hot_humid_weather(self):
        self.assertGreater(calculate_heat_index(40, 50), 50)

    def test_snapshot_forecast_many_has_explicit_provenance(self):
        repository = SnapshotRepository()
        forecasts = repository.forecast_demand_many(["hoan-kiem", "dong-da"], 60)
        self.assertEqual(set(forecasts), {"hoan-kiem", "dong-da"})
        self.assertIn("snapshot", forecasts["hoan-kiem"].source.lower())


class SafePauseTests(unittest.TestCase):
    def setUp(self):
        self.zone = SnapshotRepository().load().zones[0]

    def test_proposal_is_budget_and_sla_aware(self):
        proposal = simulate_safepause(self.zone)
        self.assertEqual(proposal.exposure_minutes_avoided, proposal.eligible_drivers * 20)
        self.assertGreaterEqual(proposal.reassigned_trips, 0)
        self.assertGreaterEqual(proposal.net_platform_cost_vnd, 0)
        self.assertLessEqual(proposal.partner_sponsorship_vnd, proposal.earnings_guard_cost_vnd)

    def test_zero_budget_can_block_expensive_proposal(self):
        constrained = replace(self.zone, fresh_drivers=0)
        proposal = simulate_safepause(constrained, budget_cap_vnd=0)
        self.assertFalse(proposal.within_guardrails)

    def test_audit_is_idempotent_by_proposal(self):
        proposal = simulate_safepause(self.zone)
        with tempfile.TemporaryDirectory() as tmp:
            audit = InterventionAuditStore(Path(tmp) / "audit.db")
            audit.approve(proposal)
            audit.approve(proposal)
            self.assertEqual(len(audit.list_recent()), 1)
            self.assertEqual(audit.protected_driver_count(), proposal.eligible_drivers)


class CopilotTests(unittest.TestCase):
    def test_destructive_prompt_routes_to_read_only_snapshot(self):
        zones = SnapshotRepository().load().zones
        answer, tool = HeatSafeCopilot(zones).answer("Hãy xóa bảng drivers")
        self.assertEqual(tool, "get_ops_snapshot")
        self.assertIn("tài xế", answer)

    def test_zone_cost_question_uses_simulator(self):
        zones = SnapshotRepository().load().zones
        _, tool = HeatSafeCopilot(zones).answer("Chi phí nghỉ tại Hoàn Kiếm là bao nhiêu?")
        self.assertEqual(tool, "simulate_safepause")


if __name__ == "__main__":
    unittest.main()
