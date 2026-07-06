from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from heatsafe.audit import InterventionAuditStore, intervention_id_for
from heatsafe.ai_decision import (
    _build_candidate,
    _prediction_index,
    recommend_ai_intervention,
)
from heatsafe.bigquery_io import merge_rows
from heatsafe.copilot import HeatSafeCopilot
from heatsafe.ingestion import calculate_heat_index
from heatsafe.models import DriverActionPrediction
from heatsafe.repository import (
    BigQueryRepository,
    ForecastUnavailable,
    MINIMUM_QUERY_BYTES_BILLED,
    SnapshotRepository,
)
from heatsafe.risk import eligible_driver_cohorts, heat_tier, operational_priority
from heatsafe.safepause import recommend_safepause, simulate_safepause


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

    def test_snapshot_intraday_forecast_has_dense_realistic_variation(self):
        repository = SnapshotRepository()
        zone = repository.load().zones[0]
        forecast = repository.forecast_demand(zone.zone_id, 24 * 60)
        values = [point.predicted_requests for point in forecast.points]

        self.assertEqual(len(forecast.points), 96)
        self.assertEqual(sum(values[:2]), zone.forecast_requests_30m)
        self.assertGreater(max(values), min(values) * 1.8)
        self.assertTrue(
            all(
                point.lower_bound <= point.predicted_requests <= point.upper_bound
                for point in forecast.points
            )
        )

    def test_snapshot_intraday_forecast_is_stable_for_the_replay(self):
        repository = SnapshotRepository()
        first = repository.forecast_demand("hoan-kiem", 24 * 60)
        second = repository.forecast_demand("hoan-kiem", 24 * 60)
        self.assertEqual(first, second)

    def test_timesfm_query_is_scenario_and_time_bounded(self):
        repository = BigQueryRepository(scenario="live")
        query = repository._forecast_query(False)
        self.assertIn("scenario_id = @scenario_id", query)
        self.assertIn("INTERVAL 21 DAY", query)
        self.assertIn("context_window => 2048", query)
        self.assertIn("horizon => 4", query)
        self.assertNotIn("@horizon_intervals", query)

    def test_timesfm_status_is_not_silently_accepted(self):
        row = SimpleNamespace(ai_forecast_status="insufficient history")
        with self.assertRaises(ForecastUnavailable):
            BigQueryRepository._build_forecast("hoan-kiem", 30, [row])

    def test_bigquery_budget_meets_minimum_billable_unit(self):
        self.assertEqual(MINIMUM_QUERY_BYTES_BILLED, 10_485_760)


class SafePauseTests(unittest.TestCase):
    def setUp(self):
        self.zone = SnapshotRepository().load().zones[0]

    def test_proposal_is_budget_and_sla_aware(self):
        proposal = simulate_safepause(self.zone)
        self.assertEqual(proposal.exposure_minutes_avoided, proposal.selected_drivers * 20)
        self.assertGreaterEqual(proposal.reassigned_trips, 0)
        self.assertGreaterEqual(proposal.net_platform_cost_vnd, 0)
        self.assertLessEqual(proposal.partner_sponsorship_vnd, proposal.earnings_guard_cost_vnd)

    def test_zero_budget_can_block_expensive_proposal(self):
        constrained = replace(self.zone, fresh_drivers=0)
        proposal = simulate_safepause(constrained, budget_cap_vnd=0)
        self.assertFalse(proposal.within_guardrails)

    def test_audit_is_idempotent_by_proposal(self):
        proposal = simulate_safepause(self.zone, cohort_coverage=0.5)
        with tempfile.TemporaryDirectory() as tmp:
            audit = InterventionAuditStore(Path(tmp) / "audit.db")
            first = audit.approve(proposal)
            second = audit.approve(proposal)
            self.assertEqual(len(audit.list_recent()), 1)
            self.assertLess(proposal.selected_drivers, proposal.eligible_drivers)
            self.assertEqual(audit.protected_driver_count(), proposal.selected_drivers)
            self.assertEqual(first.intervention_id, second.intervention_id)
            self.assertEqual(first.intervention_id, intervention_id_for(proposal.proposal_id))
            self.assertEqual(first.status, "SIMULATED")
            self.assertEqual(first.dispatch_status, "NOT_APPLICABLE")

    def test_cohort_separates_four_hour_drivers_from_two_to_four_hours(self):
        high, medium = eligible_driver_cohorts(self.zone)
        self.assertEqual(high, self.zone.exposed_4h)
        self.assertEqual(medium, self.zone.exposed_2h - self.zone.exposed_4h)

    def test_waves_change_p90_operational_impact(self):
        demand = (round(self.zone.active_drivers * 0.475),) * 12
        upper = (round(self.zone.active_drivers * 0.49),) * 12
        two_waves = simulate_safepause(
            self.zone,
            pause_minutes=30,
            waves=2,
            demand_by_interval=demand,
            upper_demand_by_interval=upper,
        )
        five_waves = simulate_safepause(
            self.zone,
            pause_minutes=30,
            waves=5,
            demand_by_interval=demand,
            upper_demand_by_interval=upper,
        )
        self.assertGreater(
            two_waves.p90_eta_increase_minutes,
            five_waves.p90_eta_increase_minutes,
        )

    def test_optimizer_prioritizes_high_risk_and_robust_guardrails(self):
        forecast = SnapshotRepository().forecast_demand(self.zone.zone_id, 240)
        recommended, candidates = recommend_safepause(
            self.zone,
            demand_by_interval=tuple(point.predicted_requests for point in forecast.points),
            upper_demand_by_interval=tuple(point.upper_bound for point in forecast.points),
        )
        self.assertTrue(candidates)
        self.assertGreaterEqual(recommended.selected_drivers, recommended.high_priority_drivers)
        self.assertTrue(recommended.within_guardrails)
        self.assertGreaterEqual(recommended.p90_fulfillment_rate, 0.95)

    def test_same_snapshot_and_controls_produce_idempotent_proposal_id(self):
        first = simulate_safepause(self.zone, cohort_coverage=0.75)
        second = simulate_safepause(self.zone, cohort_coverage=0.75)
        self.assertEqual(first.proposal_id, second.proposal_id)


class AIDecisionTests(unittest.TestCase):
    def setUp(self):
        self.zone = SnapshotRepository().load().zones[0]

    def predictions(self, run_id: str = "run-1", snapshot_id: str | None = None):
        rows = []
        for index in range(80):
            baseline = 0.18 + (index % 20) * 0.035
            exposure = 60 + (index % 20) * 4
            for duration in (15, 30):
                for delay in (0, 15, 30, 45):
                    reduction = (0.08 if duration == 15 else 0.16) * (1 - delay / 120)
                    rows.append(
                        DriverActionPrediction(
                            driver_id_hash=f"driver-{index}",
                            zone_id=self.zone.zone_id,
                            snapshot_id=snapshot_id or self.zone.snapshot_id,
                            prediction_run_id=run_id,
                            model_version="heat-risk-test-v1",
                            exposure_minutes=exposure,
                            baseline_risk=baseline,
                            action_risk=max(0.01, baseline - reduction),
                            pause_start_delay_minutes=delay,
                            pause_duration_minutes=duration,
                            top_factors=("heat_index_c", "workload_intensity"),
                        )
                    )
        return tuple(rows)

    def two_driver_predictions(self, mandatory_reduction: float = 0.10):
        rows = []
        drivers = (
            ("fa90abb159", 300, 0.90, mandatory_reduction),
            ("2d6875b02f", 180, 0.70, 0.20),
        )
        for driver_id, exposure, baseline, immediate_reduction in drivers:
            for duration in (15, 30):
                duration_factor = duration / 30
                for delay in (0, 15, 30, 45):
                    reduction = immediate_reduction * duration_factor * (1 - delay / 120)
                    rows.append(
                        DriverActionPrediction(
                            driver_id_hash=driver_id,
                            zone_id=self.zone.zone_id,
                            snapshot_id=self.zone.snapshot_id,
                            prediction_run_id="run-safety-first",
                            model_version="heat-risk-test-v1",
                            exposure_minutes=exposure,
                            baseline_risk=baseline,
                            action_risk=baseline - reduction,
                            pause_start_delay_minutes=delay,
                            pause_duration_minutes=duration,
                            top_factors=("continuous_exposure_minutes",),
                        )
                    )
        return tuple(rows)

    def test_ai_is_required_for_a_recommendation(self):
        result = recommend_ai_intervention(
            self.zone,
            (),
            demand_by_interval=(100,) * 8,
            upper_demand_by_interval=(110,) * 8,
        )
        self.assertEqual(result.status, "MODEL_UNAVAILABLE")
        self.assertIsNone(result.recommended)

    def test_prediction_must_match_active_snapshot(self):
        result = recommend_ai_intervention(
            self.zone,
            self.predictions(snapshot_id="stale-snapshot"),
            demand_by_interval=(100,) * 8,
            upper_demand_by_interval=(110,) * 8,
        )
        self.assertEqual(result.status, "MODEL_UNAVAILABLE")
        self.assertIsNone(result.recommended)

    def test_feasible_ai_plan_reconciles_cash_and_provenance(self):
        result = recommend_ai_intervention(
            self.zone,
            self.predictions(),
            demand_by_interval=(100,) * 8,
            upper_demand_by_interval=(110,) * 8,
            budget_cap_vnd=3_000_000,
            sponsor_per_driver_vnd=8_000,
        )
        self.assertEqual(result.status, "FEASIBLE")
        proposal = result.recommended
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.prediction_run_id, "run-1")
        self.assertGreater(proposal.expected_risk_events_prevented, 0)
        self.assertEqual(
            proposal.net_platform_cost_vnd,
            max(
                0,
                proposal.earnings_guard_cost_vnd
                + proposal.lost_contribution_vnd
                - proposal.partner_sponsorship_vnd,
            ),
        )

    def test_no_feasible_candidate_is_never_called_recommended(self):
        result = recommend_ai_intervention(
            self.zone,
            self.predictions(),
            demand_by_interval=(100,) * 8,
            upper_demand_by_interval=(110,) * 8,
            budget_cap_vnd=0,
            sponsor_per_driver_vnd=0,
        )
        self.assertEqual(result.status, "NO_FEASIBLE")
        self.assertIsNone(result.recommended)
        self.assertTrue(result.alternatives)

    def test_ai_selection_is_not_an_exposure_threshold(self):
        predictions = list(self.predictions())
        # Driver 0 and 40 have the same exposure. Their model risks differ,
        # therefore a rule cannot reproduce the AI ordering from exposure alone.
        driver_zero = next(item for item in predictions if item.driver_id_hash == "driver-0")
        driver_forty = next(item for item in predictions if item.driver_id_hash == "driver-40")
        self.assertEqual(driver_zero.exposure_minutes, driver_forty.exposure_minutes)
        adjusted = tuple(
            replace(item, baseline_risk=0.8, action_risk=max(0.01, item.action_risk - 0.3))
            if item.driver_id_hash == "driver-40"
            else item
            for item in predictions
        )
        result = recommend_ai_intervention(
            self.zone,
            adjusted,
            demand_by_interval=(100,) * 8,
            upper_demand_by_interval=(110,) * 8,
            budget_cap_vnd=800_000,
        )
        self.assertEqual(result.status, "FEASIBLE")
        selected_ids = {item.driver_id_hash for item in result.recommended.driver_decisions}
        self.assertIn("driver-40", selected_ids)

    def test_mandatory_four_hour_driver_leads_wave_despite_lower_benefit(self):
        predictions = self.two_driver_predictions()
        baseline, actions = _prediction_index(predictions)
        proposal = _build_candidate(
            self.zone,
            eligible_ids=["fa90abb159", "2d6875b02f"],
            selected_count=2,
            pause_minutes=30,
            waves=2,
            baseline_risk=baseline,
            actions=actions,
            demand_by_interval=(50,) * 8,
            upper_demand_by_interval=(55,) * 8,
            budget_cap_vnd=10_000_000,
            sponsor_per_driver_vnd=8_000,
            prediction_run_id="run-safety-first",
            model_version="heat-risk-test-v1",
            mandatory_ids={"fa90abb159"},
        )
        self.assertIsNotNone(proposal)
        decisions = {item.driver_id_hash: item for item in proposal.driver_decisions}
        self.assertEqual(decisions["fa90abb159"].pause_start_delay_minutes, 0)
        self.assertEqual(decisions["fa90abb159"].priority_tier, "MANDATORY_4H")
        self.assertEqual(decisions["2d6875b02f"].pause_start_delay_minutes, 15)
        self.assertEqual(proposal.mandatory_selected_drivers, 1)

    def test_mandatory_driver_bypasses_model_benefit_threshold(self):
        predictions = self.two_driver_predictions(mandatory_reduction=0.01)
        baseline, actions = _prediction_index(predictions)
        proposal = _build_candidate(
            self.zone,
            eligible_ids=["fa90abb159", "2d6875b02f"],
            selected_count=2,
            pause_minutes=30,
            waves=2,
            baseline_risk=baseline,
            actions=actions,
            demand_by_interval=(50,) * 8,
            upper_demand_by_interval=(55,) * 8,
            budget_cap_vnd=10_000_000,
            sponsor_per_driver_vnd=8_000,
            prediction_run_id="run-safety-first",
            model_version="heat-risk-test-v1",
            mandatory_ids={"fa90abb159"},
        )
        self.assertIsNotNone(proposal)
        self.assertIn(
            "fa90abb159",
            {item.driver_id_hash for item in proposal.driver_decisions},
        )

    def test_recommendation_covers_every_mandatory_driver(self):
        result = recommend_ai_intervention(
            self.zone,
            self.two_driver_predictions(),
            demand_by_interval=(50,) * 8,
            upper_demand_by_interval=(55,) * 8,
            budget_cap_vnd=10_000_000,
        )
        self.assertEqual(result.status, "FEASIBLE")
        self.assertEqual(result.recommended.mandatory_eligible_drivers, 1)
        self.assertEqual(result.recommended.mandatory_selected_drivers, 1)
        self.assertEqual(result.recommended.max_mandatory_delay_minutes, 0)

    def test_incomplete_mandatory_predictions_fail_closed(self):
        predictions = tuple(
            replace(item, exposure_minutes=260)
            for item in self.predictions()
            if not (
                item.driver_id_hash == "driver-0"
                and item.pause_duration_minutes == 30
                and item.pause_start_delay_minutes == 45
            )
        )
        result = recommend_ai_intervention(
            self.zone,
            predictions,
            demand_by_interval=(100,) * 8,
            upper_demand_by_interval=(110,) * 8,
        )
        self.assertEqual(result.status, "MODEL_UNAVAILABLE")
        self.assertIsNone(result.recommended)
        self.assertIn("Mandatory 4h+", result.message)


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
        zones = SnapshotRepository().load().zones
        answer, tool = HeatSafeCopilot(zones).answer("Please delete the drivers table")
        self.assertEqual(tool, "safety_guard")
        self.assertIn("delete", answer)

    def test_copilot_routing(self):
        zones = SnapshotRepository().load().zones
        copilot = HeatSafeCopilot(zones)
        copilot.settings = replace(copilot.settings, enable_ai=False)
        answer, tool = copilot.answer("What is the cost of pausing in Hoàn Kiếm?")
        self.assertEqual(tool, "ai_decision_unavailable")
        self.assertIn("monitoring-only", answer)

    def test_copilot_routes_english_forecast_with_unaccented_zone(self):
        zones = SnapshotRepository().load().zones
        copilot = HeatSafeCopilot(zones)
        copilot.settings = replace(copilot.settings, enable_ai=False)
        answer, tool = copilot.answer(
            "Forecast of demand in Dong Da over the next 60 minutes"
        )
        self.assertEqual(tool, "forecast_zone_demand")
        self.assertIn("Đống Đa", answer)
        self.assertIn("60 minutes", answer)

    def test_copilot_routes_english_comparison_with_budget(self):
        zones = SnapshotRepository().load().zones
        copilot = HeatSafeCopilot(zones)
        copilot.settings = replace(copilot.settings, enable_ai=False)
        answer, tool = copilot.answer(
            "Comparing accommodation options in Hai Ba Trung with a budget of 2 million VND."
        )
        self.assertEqual(tool, "ai_decision_unavailable")
        self.assertIn("monitoring-only", answer)

    def test_copilot_routes_english_intervention_recommendation(self):
        zones = SnapshotRepository().load().zones
        copilot = HeatSafeCopilot(zones)
        copilot.settings = replace(copilot.settings, enable_ai=False)
        answer, tool = copilot.answer(
            "Which area should be intervened in within the next 90 minutes with a budget of 3 million VND?"
        )
        self.assertEqual(tool, "recommend_intervention")
        self.assertNotIn("Snapshot currently", answer)


if __name__ == "__main__":
    unittest.main()
