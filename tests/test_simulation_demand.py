from __future__ import annotations

import statistics
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from heatsafe.simulation import (
    DeterministicRandom,
    advance_tick,
    initialize_state,
    load_scenario,
    load_zone_priors,
    weather_at,
)
from heatsafe.simulation.demand import (
    advance_shocks,
    demand_mean_15m,
    sample_requests,
    supply_multiplier,
    target_active,
    weather_demand_factor,
)


class DemandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_scenario("hanoi_heatwave_v1")
        cls.zone = load_zone_priors()[0]

    def test_supply_curve_locks_frozen_breakpoints(self):
        expected = {
            0: 0.25,
            4 * 60: 0.20,
            6 * 60: 0.50,
            8 * 60: 1.10,
            13 * 60: 1.00,
            18 * 60: 1.20,
            24 * 60: 0.25,
        }
        for minute, multiplier in expected.items():
            with self.subTest(minute=minute):
                self.assertAlmostEqual(supply_multiplier(minute), multiplier)
                self.assertEqual(
                    target_active(self.zone, minute),
                    round(self.zone.active_anchor * multiplier),
                )

    def test_demand_mean_has_exact_13h_anchor_without_shock(self):
        anchor_weather = weather_at(self.fixture, 13 * 60)
        event_time = self.fixture.weather[0]["local_time"].replace(hour=13)
        expected = self.zone.forecast_requests_30m / 2
        actual = demand_mean_15m(
            self.zone,
            event_time,
            anchor_weather,
            anchor_weather,
            1.0,
            1.0,
        )
        self.assertAlmostEqual(actual, expected)

    def test_intraday_expected_shape_has_commute_peaks(self):
        start = self.fixture.weather[0]["local_time"]
        anchor_weather = weather_at(self.fixture, 13 * 60)

        def mean(hour: int) -> float:
            return demand_mean_15m(
                self.zone,
                start + timedelta(hours=hour),
                weather_at(self.fixture, hour * 60),
                anchor_weather,
                1.0,
                1.0,
            )

        self.assertGreater(mean(8), mean(4) * 1.8)
        self.assertGreater(mean(18), mean(15))
        self.assertGreater(mean(13), mean(0))
        self.assertGreater(mean(13), mean(10))

    def test_correlated_shocks_remain_in_frozen_bounds(self):
        city = 1.0
        zones = ((self.zone.zone_id, 1.0),)
        for minute in range(500):
            city, zones = advance_shocks(
                scenario_version="hanoi_heatwave_v1",
                seed=42,
                minute_index=minute,
                zone_ids=(self.zone.zone_id,),
                city_shock=city,
                zone_shocks=zones,
            )
            self.assertGreaterEqual(city, 0.85)
            self.assertLessEqual(city, 1.20)
            self.assertGreaterEqual(zones[0][1], 0.90)
            self.assertLessEqual(zones[0][1], 1.10)

    def test_negative_binomial_request_counts_are_overdispersed(self):
        values = [
            sample_requests(
                scenario_version="hanoi_heatwave_v1",
                seed=seed,
                minute_index=60,
                zone=self.zone,
                expected_15m=900,
            )
            for seed in range(600)
        ]
        self.assertGreater(statistics.variance(values), statistics.mean(values))

    def test_zero_mean_never_creates_requests(self):
        stream = DeterministicRandom("zero")
        self.assertEqual(stream.negative_binomial(0, 40), 0)

    def test_rain_multiplier_is_bounded_at_fifteen_percent(self):
        dry = weather_at(self.fixture, 0)
        heavy_rain = replace(dry, precipitation_mm=20)
        self.assertAlmostEqual(
            weather_demand_factor(heavy_rain) / weather_demand_factor(dry),
            1.15,
        )

    def _tick_with_fixed_requests(self, active_anchor: int, count_per_minute: int):
        zone = replace(
            self.zone,
            active_anchor=active_anchor,
            exposed_2h_anchor=min(2, active_anchor),
            exposed_4h_anchor=min(1, active_anchor),
            forecast_requests_30m=count_per_minute * 30,
        )
        state = initialize_state(seed=222, fixture=self.fixture, zones=(zone,))
        with patch(
            "heatsafe.simulation.engine.sample_requests",
            return_value=count_per_minute,
        ):
            return advance_tick(state, fixture=self.fixture, zones=(zone,))

    def test_zero_demand_tick_is_coherent(self):
        result = self._tick_with_fixed_requests(20, 0)
        zone = result.zones[0]
        self.assertEqual(zone.requests_15m, 0)
        self.assertEqual(zone.matched_15m, 0)
        self.assertEqual(zone.open_unmatched_end, 0)
        self.assertEqual(zone.request_flow_balance, 0)

    def test_oversupply_matches_every_request(self):
        result = self._tick_with_fixed_requests(100, 1)
        zone = result.zones[0]
        self.assertEqual(zone.requests_15m, 15)
        self.assertEqual(zone.matched_15m, 15)
        self.assertEqual(zone.open_unmatched_end, 0)
        self.assertEqual(zone.request_flow_balance, 0)

    def test_undersupply_leaves_bounded_queue_or_terminal_outcome(self):
        result = self._tick_with_fixed_requests(4, 20)
        zone = result.zones[0]
        self.assertEqual(zone.requests_15m, 300)
        self.assertLess(zone.matched_15m, zone.requests_15m)
        self.assertGreater(
            zone.open_unmatched_end
            + zone.cancelled_15m
            + zone.unfulfilled_15m,
            0,
        )
        self.assertEqual(zone.request_flow_balance, 0)


if __name__ == "__main__":
    unittest.main()
