from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from heatsafe.copilot import (
    HeatSafeCopilot,
    ToolResult,
    _question_budget_vnd,
)
from heatsafe.currency import usd_to_vnd, vnd_to_usd
from heatsafe.models import DecisionConstraints, DriverActionPrediction
from heatsafe.repository import (
    BigQueryRepository,
    DemandForecast,
    ForecastPoint,
    ForecastUnavailable,
    SnapshotRepository,
)
from heatsafe.services.decision_service import (
    build_city_wide_plan,
    build_selected_zone_decision,
)


class ConstraintAndCurrencyTests(unittest.TestCase):
    def test_constraints_are_normalized_at_the_boundary(self):
        constraints = DecisionConstraints(
            horizon_minutes=999,
            budget_cap_vnd=-1,
            sponsor_per_driver_vnd=-2,
        )
        self.assertEqual(constraints.horizon_minutes, 240)
        self.assertEqual(constraints.budget_cap_vnd, 0)
        self.assertEqual(constraints.sponsor_per_driver_vnd, 0)

    def test_currency_uses_the_product_exchange_rate(self):
        self.assertEqual(usd_to_vnd(100), 2_500_000)
        self.assertEqual(vnd_to_usd(2_500_000), 100.0)

    def test_copilot_understands_usd_and_vnd_budgets(self):
        self.assertEqual(_question_budget_vnd("budget of $100"), 2_500_000)
        self.assertEqual(_question_budget_vnd("budget 150 USD"), 3_750_000)
        self.assertEqual(_question_budget_vnd("budget 2 million VND"), 2_000_000)


class CopilotExecutionTests(unittest.TestCase):
    def setUp(self):
        self.zones = SnapshotRepository().load().zones

    def test_routing_has_no_repository_side_effect(self):
        class ExplodingRepository:
            def __getattr__(self, name):
                raise AssertionError(f"repository accessed during routing: {name}")

        copilot = HeatSafeCopilot(
            self.zones,
            cast(Any, ExplodingRepository()),
            default_constraints=DecisionConstraints(budget_cap_vnd=3_000_000),
        )
        request = copilot._route(
            "Compare SafePause options in Hoan Kiem with the current budget"
        )
        self.assertEqual(request.tool_name, "compare_safepause_options")
        self.assertEqual(request.arguments["budget_cap_vnd"], 3_000_000)

    def test_forecast_failure_is_fail_closed_instead_of_escaping(self):
        class FailingRepository:
            def __init__(self):
                self.load_calls = 0
                self.forecast_calls = 0

            def load(self):
                self.load_calls += 1

            def forecast_demand(self, _zone_id, _horizon):
                self.forecast_calls += 1
                raise ForecastUnavailable("TimesFM unavailable")

        repository = FailingRepository()
        copilot = HeatSafeCopilot(self.zones, cast(Any, repository))
        copilot.settings = replace(copilot.settings, enable_ai=False)
        answer, tool = copilot.answer(
            "Forecast demand in Hoan Kiem for the next 60 minutes"
        )
        self.assertEqual(tool, "forecast_zone_demand")
        self.assertIn("monitoring-only", answer)
        self.assertEqual(repository.load_calls, 1)
        self.assertEqual(repository.forecast_calls, 1)

    def test_city_prediction_outage_is_not_reported_as_no_feasible(self):
        snapshot = SnapshotRepository()

        class FailingRepository:
            def load(self):
                return None

            def forecast_demand_many(self, zone_ids, horizon_minutes):
                return snapshot.forecast_demand_many(zone_ids, horizon_minutes)

            def load_driver_predictions(self, _zone_id, _snapshot_id):
                raise RuntimeError("predictions unavailable")

        copilot = HeatSafeCopilot(self.zones, cast(Any, FailingRepository()))
        result = copilot._execute_request(
            copilot._route("Which area should we intervene in?")
        )
        self.assertEqual(result.facts["status"], "MODEL_UNAVAILABLE")
        self.assertIn("monitoring-only", result.deterministic_answer)

    def test_gemini_selection_failure_executes_deterministic_tool_once(self):
        copilot = HeatSafeCopilot(self.zones)
        copilot.settings = replace(copilot.settings, enable_ai=True)
        result = ToolResult("get_operational_snapshot", {"status": "OK"}, "snapshot")
        with (
            mock.patch("google.genai.Client", return_value=object()),
            mock.patch.object(
                copilot, "_select_with_gemini", side_effect=RuntimeError("timeout")
            ),
            mock.patch.object(
                copilot, "_execute_request", return_value=result
            ) as execute,
        ):
            answer, tool = copilot.answer("What is the current snapshot?")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(tool, "get_operational_snapshot")
        self.assertIn("snapshot", answer)

    def test_gemini_explanation_failure_does_not_rerun_tool(self):
        copilot = HeatSafeCopilot(self.zones)
        copilot.settings = replace(copilot.settings, enable_ai=True)
        request = copilot._route("What is the current snapshot?")
        result = ToolResult("get_operational_snapshot", {"status": "OK"}, "snapshot")
        with (
            mock.patch("google.genai.Client", return_value=object()),
            mock.patch.object(copilot, "_select_with_gemini", return_value=request),
            mock.patch.object(
                copilot, "_execute_request", return_value=result
            ) as execute,
            mock.patch.object(
                copilot, "_explain_with_gemini", side_effect=RuntimeError("timeout")
            ),
        ):
            answer, tool = copilot.answer("What is the current snapshot?")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual((answer, tool), ("snapshot", "get_operational_snapshot"))


class BigQueryBatchForecastTests(unittest.TestCase):
    def test_batch_forecast_uses_exact_current_lineage_and_keeps_partial_results(self):
        captured = {}
        row = SimpleNamespace(
            zone_id="available-zone",
            forecast_timestamp=datetime.now(UTC),
            forecast_value=10.0,
            prediction_interval_lower_bound=8.0,
            prediction_interval_upper_bound=12.0,
            ai_forecast_status="",
            forecast_reused=True,
            forecast_source_tick_id="tick-0",
            forecast_source_snapshot_id="snapshot-0",
            forecast_source_prediction_run_id="prediction-0",
            forecast_age_minutes=15,
            generated_at=datetime.now(UTC),
        )

        class QueryResult:
            def result(self):
                return [row]

        class Client:
            def query(self, query, job_config):
                captured["query"] = query
                captured["job_config"] = job_config
                return QueryResult()

        repository = BigQueryRepository(scenario="heatwave")
        repository._client_instance = cast(Any, Client())
        forecasts = repository.forecast_demand_many(
            ["available-zone", "missing-zone"], 30
        )

        self.assertEqual(set(forecasts), {"available-zone"})
        self.assertIn("WITH latest_runs AS", captured["query"])
        self.assertIn("FROM latest_runs", captured["query"])
        self.assertIn("PARTITION BY zone_id ORDER BY forecast_timestamp", captured["query"])
        self.assertNotIn("MAX(prediction_run_id)", captured["query"])
        self.assertIn(
            "current_snapshot.tick_id IS NOT DISTINCT FROM",
            captured["query"],
        )
        self.assertNotIn(
            f"`{repository.table}` current\n",
            captured["query"],
        )
        self.assertTrue(forecasts["available-zone"].forecast_reused)
        self.assertIn("reused from tick tick-0", forecasts["available-zone"].source)


class SharedDecisionServiceTests(unittest.TestCase):
    @staticmethod
    def _predictions(zone):
        rows = []
        for index in range(12):
            baseline = 0.75 - index * 0.02
            exposure = 260 if index < 2 else 180
            for duration in (15, 30):
                for delay in (0, 15, 30, 45):
                    reduction = (0.08 if duration == 15 else 0.16) * (1 - delay / 120)
                    rows.append(
                        DriverActionPrediction(
                            driver_id_hash=f"{zone.zone_id}-driver-{index}",
                            zone_id=zone.zone_id,
                            snapshot_id=zone.snapshot_id,
                            prediction_run_id="shared-run",
                            model_version="shared-model",
                            exposure_minutes=exposure,
                            baseline_risk=baseline,
                            action_risk=max(0.01, baseline - reduction),
                            pause_start_delay_minutes=delay,
                            pause_duration_minutes=duration,
                            top_factors=("heat_index_c", "continuous_exposure_minutes"),
                        )
                    )
        return tuple(rows)

    def test_city_and_selected_zone_share_constraints_and_evidence(self):
        zones = SnapshotRepository().load().zones[:2]
        forecasts = {}
        predictions = {}
        for zone in zones:
            points = tuple(
                ForecastPoint(
                    forecast_at=zone.observed_at,
                    predicted_requests=10,
                    lower_bound=8,
                    upper_bound=12,
                )
                for _ in range(16)
            )
            forecasts[zone.zone_id] = DemandForecast(
                zone_id=zone.zone_id,
                horizon_minutes=240,
                predicted_requests=160,
                source="test forecast",
                status="OK",
                points=points,
            )
            predictions[zone.zone_id] = self._predictions(zone)

        class Repository:
            def __init__(self):
                self.forecast_many_calls = 0
                self.prediction_many_calls = 0

            def forecast_demand(self, zone_id, _horizon):
                return forecasts[zone_id]

            def load_driver_predictions(self, zone_id, _snapshot_id):
                return predictions[zone_id]

            def forecast_demand_many(self, zone_ids, _horizon):
                self.forecast_many_calls += 1
                return {zone_id: forecasts[zone_id] for zone_id in zone_ids}

            def load_driver_predictions_many(self, zone_ids, _snapshot_id):
                self.prediction_many_calls += 1
                return {zone_id: predictions[zone_id] for zone_id in zone_ids}

        repository = Repository()
        constraints = DecisionConstraints(
            horizon_minutes=240,
            budget_cap_vnd=10_000_000,
            sponsor_per_driver_vnd=8_000,
        )
        selected = build_selected_zone_decision(cast(Any, repository), zones[0], constraints)
        city = build_city_wide_plan(
            cast(Any, repository),
            zones,
            snapshot_id=zones[0].snapshot_id,
            constraints=constraints,
        )
        city_selected = next(row for row in city.rows if row.zone_id == zones[0].zone_id)

        self.assertEqual(repository.forecast_many_calls, 1)
        self.assertEqual(repository.prediction_many_calls, 1)
        self.assertEqual(city.constraints, constraints)
        selected_proposal = selected.proposal
        self.assertIsNotNone(selected_proposal)
        assert selected_proposal is not None
        self.assertEqual(
            city_selected.proposal.proposal_id,
            selected_proposal.proposal_id,
        )
        self.assertEqual(
            city_selected.proposal.net_platform_cost_vnd,
            selected_proposal.net_platform_cost_vnd,
        )

    def test_city_plan_reports_missing_zone_predictions(self):
        zones = SnapshotRepository().load().zones[:2]
        snapshot = SnapshotRepository()

        class Repository:
            def forecast_demand_many(self, zone_ids, horizon):
                return snapshot.forecast_demand_many(zone_ids, horizon)

            def load_driver_predictions_many(self, zone_ids, _snapshot_id):
                return {zone_ids[0]: SharedDecisionServiceTests._predictions(zones[0])}

        plan = build_city_wide_plan(
            cast(Any, Repository()),
            zones,
            constraints=DecisionConstraints(budget_cap_vnd=10_000_000),
        )
        unavailable = {item.zone_id: item.reason_code for item in plan.unavailable_zones}
        self.assertEqual(unavailable[zones[1].zone_id], "PREDICTIONS_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
