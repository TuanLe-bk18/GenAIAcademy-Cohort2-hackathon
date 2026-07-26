from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import math
from types import SimpleNamespace
from typing import Any, cast
from unittest import TestCase, mock

from heatsafe.models import (
    AcceleratedForecastInput,
    CurrentForecastInput,
    DecisionConstraints,
    DriverActionPrediction,
    DriverCurrentFeature,
)
from heatsafe.repository import (
    BigQueryRepository,
    DemandForecast,
    ForecastPoint,
    SnapshotRepository,
)
from heatsafe.services.preventive_planning import (
    FORECAST_PATH_COUNT,
    MICROCLIMATE_MODEL_VERSION,
    build_accelerated_forecast_input,
    build_current_forecast_input,
    build_predictive_city_plan,
    project_city_forecast,
)
from heatsafe.simulation.engine import (
    advance_tick,
    initialize_state,
    load_zone_priors,
)
from heatsafe.simulation.scenario import load_scenario


class FakeCurrentEvidenceRepository:
    def __init__(self, zones):
        self.calls = {"features": 0, "predictions": 0, "forecasts": 0}
        self.features = {}
        self.predictions = {}
        self.forecasts = {}
        driver_specs = (
            ("mandatory", 250, 0, "ACTIVE", 0.80),
            ("projected", 180, 0, "ACTIVE", 0.60),
            ("watchlist", 230, 0, "PAUSED", 0.70),
            ("recovered", 30, 15, "ACTIVE", 0.20),
        )
        for zone in zones:
            zone_features = []
            zone_predictions = []
            for label, exposure, rest, status, baseline in driver_specs:
                driver_id = f"{zone.zone_id}-{label}"
                zone_features.append(
                    DriverCurrentFeature(
                        scenario_id=zone.scenario_id,
                        snapshot_id=zone.snapshot_id,
                        observed_at=zone.observed_at,
                        driver_id_hash=driver_id,
                        zone_id=zone.zone_id,
                        heat_index_c=zone.heat_index_c,
                        humidity_percent=zone.humidity_percent,
                        continuous_exposure_minutes=exposure,
                        trips_60m=2,
                        distance_km_60m=5.0,
                        rest_minutes_120m=rest,
                        hydration_gap_minutes=45,
                        route_heat_load=1.0,
                        workload_intensity=1.0,
                        is_simulated=True,
                        driver_status=status,
                        heat_dose_120m=20.0,
                        generator_version="current-features-v1",
                    )
                )
                for duration in (15, 30):
                    for delay in (0, 15, 30, 45):
                        zone_predictions.append(
                            DriverActionPrediction(
                                driver_id_hash=driver_id,
                                zone_id=zone.zone_id,
                                snapshot_id=zone.snapshot_id,
                                prediction_run_id="prediction-current-1",
                                model_version="bqml-risk-v1",
                                exposure_minutes=exposure,
                                baseline_risk=baseline,
                                action_risk=max(0.0, baseline - 0.10),
                                pause_start_delay_minutes=delay,
                                pause_duration_minutes=duration,
                            )
                        )
            self.features[zone.zone_id] = tuple(zone_features)
            self.predictions[zone.zone_id] = tuple(zone_predictions)
            points = tuple(
                ForecastPoint(
                    forecast_at=zone.observed_at
                    + timedelta(minutes=(index + 1) * 15),
                    predicted_requests=80 + index,
                    lower_bound=70 + index,
                    upper_bound=95 + index,
                )
                for index in range(8)
            )
            self.forecasts[zone.zone_id] = DemandForecast(
                zone_id=zone.zone_id,
                horizon_minutes=120,
                predicted_requests=sum(item.predicted_requests for item in points),
                source="BigQuery ML test",
                status="OK",
                points=points,
                forecast_source_snapshot_id=zone.snapshot_id,
                forecast_source_prediction_run_id="prediction-current-1",
            )

    def load_driver_features_many(self, zone_ids, snapshot_id):
        self.calls["features"] += 1
        return {zone_id: self.features[zone_id] for zone_id in zone_ids}

    def load_driver_predictions_many(self, zone_ids, snapshot_id):
        self.calls["predictions"] += 1
        return {zone_id: self.predictions[zone_id] for zone_id in zone_ids}

    def forecast_demand_many(self, zone_ids, horizon_minutes=120):
        self.calls["forecasts"] += 1
        return {zone_id: self.forecasts[zone_id] for zone_id in zone_ids}


def current_zones_with_one_city_weather():
    zones = SnapshotRepository().load().zones
    return tuple(
        replace(
            zone,
            temperature_c=39.0,
            humidity_percent=60.0,
            heat_index_c=49.8,
            source="BigQuery current city station",
            weather_is_simulated=False,
        )
        for zone in zones
    )


class CurrentForecastInputTests(TestCase):
    def setUp(self):
        self.zones = current_zones_with_one_city_weather()
        self.repository = FakeCurrentEvidenceRepository(self.zones)

    def test_current_adapter_batches_once_and_never_reads_future_scenario(self):
        with mock.patch(
            "heatsafe.services.preventive_planning.weather_at",
            side_effect=AssertionError("Current must not read scenario weather"),
        ) as future_weather:
            evidence = build_current_forecast_input(
                self.repository, self.zones
            )

        self.assertIsInstance(evidence, CurrentForecastInput)
        self.assertEqual(len(evidence.zones), 10)
        self.assertEqual(
            self.repository.calls,
            {"features": 1, "predictions": 1, "forecasts": 1},
        )
        future_weather.assert_not_called()

    def test_same_city_weather_gets_explicit_modeled_zone_heat(self):
        evidence = build_current_forecast_input(self.repository, self.zones)
        current_heat = {
            zone.zone.zone_id: zone.heat[0].heat_index_c
            for zone in evidence.zones
        }

        self.assertGreater(len(set(current_heat.values())), 1)
        for zone in evidence.zones:
            self.assertEqual(
                zone.heat[0].provenance, "MODELED_MICROCLIMATE_OFFSET"
            )
            self.assertEqual(
                zone.heat[0].model_version, MICROCLIMATE_MODEL_VERSION
            )
            self.assertEqual(
                zone.heat[1].provenance,
                "MODELED_MICROCLIMATE_HELD_CONSTANT",
            )

    def test_projection_deduplicates_actions_and_separates_safety_cohorts(self):
        evidence = build_current_forecast_input(self.repository, self.zones)
        city = project_city_forecast(evidence)
        zone = city.zones[0]
        now = next(item for item in zone.horizons if item.minutes_ahead == 0)
        future = next(
            item for item in zone.horizons if item.minutes_ahead == 120
        )

        self.assertEqual(len(evidence.zones[0].drivers), 4)
        self.assertAlmostEqual(now.baseline_expected_risk, 2.30)
        self.assertEqual(now.mandatory_now, 1)
        self.assertGreaterEqual(future.projected_mandatory, 1)
        self.assertGreaterEqual(future.watchlist, 1)
        self.assertGreater(future.expected_crossers, 1.0)
        self.assertEqual(len(city.path_ids), FORECAST_PATH_COUNT)
        self.assertEqual(city.path_ids[0], "path-00")
        self.assertEqual(city.path_ids[-1], "path-63")

    def test_prior_recovery_uses_current_continuous_exposure_not_day_start(self):
        for zone_id, rows in tuple(self.repository.features.items()):
            self.repository.features[zone_id] = tuple(
                item for item in rows if item.driver_id_hash.endswith("-recovered")
            )
            driver_ids = {
                item.driver_id_hash for item in self.repository.features[zone_id]
            }
            self.repository.predictions[zone_id] = tuple(
                item
                for item in self.repository.predictions[zone_id]
                if item.driver_id_hash in driver_ids
            )

        city = project_city_forecast(
            build_current_forecast_input(self.repository, self.zones)
        )
        for zone in city.zones:
            now, _, future = zone.horizons
            self.assertEqual(now.mandatory_now, 0)
            self.assertEqual(future.projected_mandatory, 0)
            self.assertEqual(future.watchlist, 0)
            self.assertEqual(future.expected_crossers, 0.0)

    def test_projection_is_stable_when_input_order_changes(self):
        first = project_city_forecast(
            build_current_forecast_input(self.repository, self.zones)
        )
        second_repository = FakeCurrentEvidenceRepository(tuple(reversed(self.zones)))
        second = project_city_forecast(
            build_current_forecast_input(
                second_repository, tuple(reversed(self.zones))
            )
        )
        self.assertEqual(first, second)


class AcceleratedForecastInputTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_scenario("hanoi_heatwave_v1")
        cls.zones = load_zone_priors()
        state = initialize_state(seed=42, fixture=cls.fixture, zones=cls.zones)
        cls.result = advance_tick(
            state, fixture=cls.fixture, zones=cls.zones
        )

    def test_accelerated_adapter_uses_same_projection_contract_without_mutation(self):
        original_state = self.result.state
        evidence = build_accelerated_forecast_input(
            self.result,
            fixture=self.fixture,
            zones=self.zones,
        )
        projection = project_city_forecast(evidence)

        self.assertIsInstance(evidence, AcceleratedForecastInput)
        self.assertEqual(len(evidence.zones), 10)
        self.assertEqual(len(projection.zones), 10)
        self.assertEqual(len(projection.path_ids), FORECAST_PATH_COUNT)
        self.assertEqual(self.result.state, original_state)
        self.assertGreater(
            len({zone.heat[0].heat_index_c for zone in evidence.zones}),
            1,
        )
        self.assertTrue(
            all(
                heat.provenance == "SIMULATED_MICROCLIMATE_FORECAST"
                for zone in evidence.zones
                for heat in zone.heat
            )
        )

    def test_accelerated_builds_the_same_all_zone_city_plan_contract(self):
        evidence = build_accelerated_forecast_input(
            self.result,
            fixture=self.fixture,
            zones=self.zones,
        )
        plan = build_predictive_city_plan(
            project_city_forecast(evidence),
            DecisionConstraints(),
        )

        self.assertEqual(plan.mode, "ACCELERATED")
        self.assertEqual(len(plan.rows), 10)
        self.assertLessEqual(
            plan.p95_reserved_cost_vnd, plan.budget_cap_vnd
        )
        self.assertTrue(all(row.portfolio_reason for row in plan.rows))


class PreventiveCityPlanTests(TestCase):
    def setUp(self):
        self.zones = current_zones_with_one_city_weather()
        self.repository = FakeCurrentEvidenceRepository(self.zones)

    def _city(self):
        return project_city_forecast(
            build_current_forecast_input(self.repository, self.zones)
        )

    def test_current_plan_covers_all_zones_without_a_fixed_top_three(self):
        plan = build_predictive_city_plan(
            self._city(),
            DecisionConstraints(budget_cap_vnd=5_000_000),
        )

        self.assertEqual(plan.mode, "CURRENT")
        self.assertEqual(len(plan.rows), 10)
        self.assertGreater(len(plan.selected_zone_ids), 3)
        self.assertEqual(
            {row.zone_id for row in plan.rows},
            {zone.zone_id for zone in self.zones},
        )
        self.assertTrue(all(row.portfolio_reason for row in plan.rows))

    def test_lower_demand_window_is_selected_before_the_peak(self):
        for zone_id, forecast in tuple(self.repository.forecasts.items()):
            self.repository.features[zone_id] = tuple(
                replace(item, continuous_exposure_minutes=100)
                if item.driver_id_hash.endswith("-watchlist")
                else item
                for item in self.repository.features[zone_id]
            )
            self.repository.predictions[zone_id] = tuple(
                replace(item, exposure_minutes=100)
                if item.driver_id_hash.endswith("-watchlist")
                else item
                for item in self.repository.predictions[zone_id]
            )
            requests = (180, 40, 220, 230, 240, 250, 260, 270)
            points = tuple(
                replace(
                    point,
                    predicted_requests=requests[index],
                    lower_bound=max(0, requests[index] - 10),
                    upper_bound=requests[index] + 15,
                )
                for index, point in enumerate(forecast.points)
            )
            self.repository.forecasts[zone_id] = replace(
                forecast,
                points=points,
                predicted_requests=sum(requests),
            )

        plan = build_predictive_city_plan(
            self._city(),
            DecisionConstraints(budget_cap_vnd=5_000_000),
        )

        self.assertTrue(
            all(
                row.best_window is not None
                and row.best_window.start_delay_minutes == 15
                for row in plan.rows
            )
        )

    def test_city_p95_is_taken_after_aligned_path_aggregation(self):
        plan = build_predictive_city_plan(
            self._city(),
            DecisionConstraints(budget_cap_vnd=5_000_000),
        )
        selected = [
            row
            for row in plan.rows
            if row.portfolio_status == "SELECTED"
        ]
        city_paths = tuple(
            sum(row.path_costs_vnd[index] for row in selected)
            for index in range(FORECAST_PATH_COUNT)
        )
        expected_p95 = sorted(city_paths)[
            math.ceil(0.95 * FORECAST_PATH_COUNT) - 1
        ]

        self.assertEqual(plan.p95_reserved_cost_vnd, expected_p95)
        self.assertLessEqual(
            plan.p95_reserved_cost_vnd, plan.budget_cap_vnd
        )

    def test_capacity_breach_is_explicit_when_safety_floor_cannot_be_funded(self):
        plan = build_predictive_city_plan(
            self._city(),
            DecisionConstraints(budget_cap_vnd=0),
        )

        self.assertEqual(plan.status, "SAFETY_CAPACITY_BREACH")
        self.assertEqual(plan.selected_zone_ids, ())
        self.assertEqual(plan.mandatory_now_covered, 0)
        self.assertEqual(plan.mandatory_now_uncovered, 10)
        self.assertTrue(
            all(
                row.portfolio_status in {"NO_ACTION", "UNAVAILABLE"}
                for row in plan.rows
            )
        )

    def test_ranks_and_portfolio_are_stable_when_zone_order_changes(self):
        city = self._city()
        first = build_predictive_city_plan(city, DecisionConstraints())
        second = build_predictive_city_plan(
            replace(city, zones=tuple(reversed(city.zones))),
            DecisionConstraints(),
        )

        self.assertEqual(first.portfolio_id, second.portfolio_id)
        self.assertEqual(first.selected_zone_ids, second.selected_zone_ids)
        self.assertEqual(
            [
                (
                    row.zone_id,
                    row.severity_rank,
                    row.future_safety_rank,
                    row.opportunity_rank,
                )
                for row in first.rows
            ],
            [
                (
                    row.zone_id,
                    row.severity_rank,
                    row.future_safety_rank,
                    row.opportunity_rank,
                )
                for row in second.rows
            ],
        )


class BigQueryFeatureBatchTests(TestCase):
    def test_current_features_use_one_exact_snapshot_batch(self):
        captured = {}
        row = SimpleNamespace(
            scenario_id="heatwave",
            snapshot_id="snapshot-1",
            observed_at=datetime.now(UTC),
            driver_id_hash="driver-1",
            zone_id="hoan-kiem",
            heat_index_c=49.2,
            humidity_percent=60.0,
            continuous_exposure_minutes=180,
            trips_60m=3,
            distance_km_60m=6.5,
            rest_minutes_120m=15,
            hydration_gap_minutes=40,
            route_heat_load=1.1,
            workload_intensity=1.2,
            is_simulated=True,
            simulation_run_id=None,
            tick_id=None,
            driver_status=None,
            heat_dose_120m=None,
            acclimatization_class=None,
            generator_version=None,
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
        grouped = repository.load_driver_features_many(
            ["hoan-kiem"], "snapshot-1"
        )

        self.assertEqual(tuple(grouped), ("hoan-kiem",))
        self.assertEqual(grouped["hoan-kiem"][0].driver_status, "ACTIVE")
        self.assertEqual(
            grouped["hoan-kiem"][0].continuous_exposure_minutes, 180
        )
        self.assertIn(".driver_current_features`", captured["query"])
        self.assertIn("snapshot_id = @snapshot_id", captured["query"])
        self.assertIn("zone_id IN UNNEST(@zone_ids)", captured["query"])


if __name__ == "__main__":
    import unittest

    unittest.main()
