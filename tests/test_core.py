from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from heatsafe.audit import InterventionAuditStore, intervention_id_for
from heatsafe.bigquery_io import merge_rows
from heatsafe.copilot import HeatSafeCopilot
from heatsafe.ingestion import calculate_heat_index
from heatsafe.repository import (
    BigQueryRepository,
    ForecastUnavailable,
    SnapshotRepository,
)
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

    def test_snapshot_has_explicit_scenario_and_component_provenance(self):
        zone = SnapshotRepository().load().zones[0]
        self.assertEqual(zone.scenario_id, "heatwave")
        self.assertTrue(zone.weather_is_simulated)
        self.assertTrue(zone.operations_is_simulated)
        self.assertTrue(zone.is_simulated)

    def test_forecast_keeps_pointwise_intervals_instead_of_aggregate_bounds(self):
        forecast = SnapshotRepository().forecast_demand("hoan-kiem", 30)
        self.assertEqual(len(forecast.points), 2)
        self.assertFalse(hasattr(forecast, "lower_bound"))
        self.assertEqual(
            forecast.predicted_requests,
            sum(point.predicted_requests for point in forecast.points),
        )

    def test_timesfm_query_is_scenario_and_time_bounded(self):
        repository = BigQueryRepository(scenario="live")
        query = repository._forecast_query(False)
        self.assertIn("scenario_id = @scenario_id", query)
        self.assertIn("INTERVAL 21 DAY", query)
        self.assertIn("context_window => 2016", query)

    def test_timesfm_status_is_not_silently_accepted(self):
        row = SimpleNamespace(ai_forecast_status="insufficient history")
        with self.assertRaises(ForecastUnavailable):
            BigQueryRepository._build_forecast("hoan-kiem", 30, [row])


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
            first = audit.approve(proposal)
            second = audit.approve(proposal)
            self.assertEqual(len(audit.list_recent()), 1)
            self.assertEqual(audit.protected_driver_count(), proposal.eligible_drivers)
            self.assertEqual(first.intervention_id, second.intervention_id)
            self.assertEqual(first.intervention_id, intervention_id_for(proposal.proposal_id))
            self.assertEqual(first.status, "SIMULATED")
            self.assertEqual(first.dispatch_status, "NOT_APPLICABLE")


class BigQuerySnapshotTests(unittest.TestCase):
    @staticmethod
    def _row(scenario: str, observed_at: datetime, snapshot_id: str = "snapshot-1") -> dict:
        zone = SnapshotRepository().load().zones[0]
        value = zone.to_dict()
        value.update(
            {
                "scenario_id": scenario,
                "snapshot_id": snapshot_id,
                "observed_at": observed_at,
                "weather_observed_at": observed_at,
                "operations_observed_at": observed_at,
                "weather_is_simulated": scenario == "heatwave",
                "operations_is_simulated": True,
            }
        )
        value.pop("is_simulated", None)
        return value

    @staticmethod
    def _fake_client(rows: list[dict]):
        class QueryResult:
            def result(self):
                return rows

        class Client:
            def query(self, *_args, **_kwargs):
                return QueryResult()

        return Client()

    def test_live_snapshot_rejects_stale_components(self):
        repository = BigQueryRepository(scenario="live")
        repository._client_instance = self._fake_client(
            [self._row("live", datetime.now(UTC) - timedelta(hours=2))]
        )
        result = repository.load()
        self.assertFalse(result.data_fresh)
        self.assertIn("older than", result.freshness_warning)

    def test_heatwave_replay_does_not_expire(self):
        repository = BigQueryRepository(scenario="heatwave")
        repository._client_instance = self._fake_client(
            [self._row("heatwave", datetime.now(UTC) - timedelta(days=30))]
        )
        result = repository.load()
        self.assertEqual(result.zones[0].scenario_id, "heatwave")

    def test_mixed_snapshot_ids_are_rejected(self):
        repository = BigQueryRepository(scenario="heatwave")
        now = datetime.now(UTC)
        repository._client_instance = self._fake_client(
            [self._row("heatwave", now, "one"), self._row("heatwave", now, "two")]
        )
        with self.assertRaisesRegex(RuntimeError, "mixed snapshot_id"):
            repository.load()


class BigQueryIoTests(unittest.TestCase):
    def test_merge_uses_staging_and_never_truncates_target(self):
        from google.cloud import bigquery

        class Done:
            def result(self):
                return None

        class Client:
            def __init__(self):
                self.loaded_table = None
                self.query_text = None
                self.deleted_table = None

            def load_table_from_json(self, _rows, table_id, job_config):
                self.loaded_table = table_id
                self.write_disposition = job_config.write_disposition
                return Done()

            def query(self, query, job_config=None):
                self.query_text = query
                self.query_config = job_config
                return Done()

            def delete_table(self, table_id, not_found_ok=False):
                self.deleted_table = table_id
                self.not_found_ok = not_found_ok

        client = Client()
        schema = [
            bigquery.SchemaField("scenario_id", "STRING"),
            bigquery.SchemaField("zone_id", "STRING"),
        ]
        target = "project.dataset.zone_snapshots_current"
        merge_rows(
            client,
            target,
            [{"scenario_id": "heatwave", "zone_id": "hoan-kiem"}],
            schema,
            ["scenario_id", "zone_id"],
            target_predicate="target.updated_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)",
        )
        self.assertNotEqual(client.loaded_table, target)
        self.assertIn("__staging_", client.loaded_table)
        self.assertIn(f"MERGE `{target}`", client.query_text)
        self.assertIn("target.updated_at >=", client.query_text)
        self.assertEqual(client.query_config.maximum_bytes_billed, 250_000_000)
        self.assertEqual(client.deleted_table, client.loaded_table)


class CopilotTests(unittest.TestCase):
    def test_copilot_safety(self):
        zones = [generate_zone_snapshot("TEST")]
        answer, tool = HeatSafeCopilot(zones).answer("Please delete the drivers table")
        self.assertEqual(tool, "safety_guard")
        self.assertIn("delete", answer)

    def test_copilot_routing(self):
        zones = [generate_zone_snapshot("Hoàn Kiếm")]
        _, tool = HeatSafeCopilot(zones).answer("What is the cost of pausing in Hoàn Kiếm?")
        self.assertEqual(tool, "compare_safepause_options")


if __name__ == "__main__":
    unittest.main()
