from __future__ import annotations

import unittest
from dataclasses import replace

from heatsafe.simulation import (
    DriverStatus,
    SimulationInvariantError,
    advance_tick,
    initialize_state,
    hourly_summary,
    load_scenario,
    load_zone_priors,
    project_scoring,
    project_zones,
    run_full_day,
    validate_state,
)
from heatsafe.simulation.demand import target_active


class InvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_scenario("hanoi_heatwave_v1")
        cls.zones = load_zone_priors()

    def test_initial_roster_and_supply_are_exact(self):
        state = initialize_state(seed=42, fixture=self.fixture, zones=self.zones)
        self.assertEqual(
            len(state.drivers),
            2 * sum(zone.active_anchor for zone in self.zones),
        )
        projected = project_zones(state.drivers, self.zones, ())
        for zone, actual in zip(self.zones, projected):
            self.assertEqual(actual.active_drivers, target_active(zone, 0))
            self.assertEqual(
                actual.fresh_drivers + actual.exposed_2h,
                actual.active_drivers,
            )
            self.assertLessEqual(actual.exposed_4h, actual.exposed_2h)

    def test_exposure_boundaries_have_cumulative_semantics(self):
        state = initialize_state(seed=3, fixture=self.fixture, zones=self.zones)
        zone = self.zones[0]
        template = next(
            driver
            for driver in state.drivers
            if driver.zone_id == zone.zone_id and driver.status == DriverStatus.IDLE
        )
        drivers = tuple(
            replace(
                template,
                driver_id_hash=f"boundary-{exposure}",
                continuous_exposure_minutes=exposure,
            )
            for exposure in (119, 120, 239, 240)
        )
        projection = project_zones(drivers, (zone,), ())[0]
        self.assertEqual(projection.fresh_drivers, 1)
        self.assertEqual(projection.exposed_2h, 3)
        self.assertEqual(projection.exposed_4h, 1)
        self.assertEqual(projection.exposed_2_to_4h, 2)

    def test_raw_scoring_truth_is_retained_when_projection_clips(self):
        state = initialize_state(seed=4, fixture=self.fixture, zones=self.zones)
        driver = next(
            item for item in state.drivers if item.status == DriverStatus.IDLE
        )
        driver = replace(
            driver,
            continuous_exposure_minutes=500,
            hydration_gap_minutes=250,
        )
        projection = project_scoring(driver, 53.9, 44)
        raw = dict(projection.raw_features)
        model = dict(projection.model_features)
        self.assertEqual(raw["heat_index_c"], 53.9)
        self.assertEqual(model["heat_index_c"], 50.55)
        self.assertEqual(raw["continuous_exposure_minutes"], 500)
        self.assertEqual(model["continuous_exposure_minutes"], 360)
        self.assertIn("heat_index_c", projection.clipped_fields)
        self.assertIn("continuous_exposure_minutes", projection.clipped_fields)

    def test_duplicate_driver_fails_invariant_validation(self):
        state = initialize_state(seed=5, fixture=self.fixture, zones=self.zones)
        broken = replace(state, drivers=state.drivers + (state.drivers[0],))
        with self.assertRaises(SimulationInvariantError):
            validate_state(broken, self.zones)

    def test_two_ticks_preserve_ids_and_partitions(self):
        state = initialize_state(seed=6, fixture=self.fixture, zones=self.zones)
        initial_ids = {driver.driver_id_hash for driver in state.drivers}
        first = advance_tick(state, fixture=self.fixture, zones=self.zones)
        second = advance_tick(first.state, fixture=self.fixture, zones=self.zones)
        self.assertEqual(
            initial_ids,
            {driver.driver_id_hash for driver in second.state.drivers},
        )
        for zone in second.zones:
            self.assertEqual(
                zone.fresh_drivers + zone.exposed_2h,
                zone.active_drivers,
            )
            self.assertLessEqual(zone.active_drivers, zone.online_drivers)
            self.assertEqual(zone.request_flow_balance, 0)

    def test_full_day_runner_completes_96_ticks_on_bounded_fleet(self):
        base = self.zones[0]
        bounded = replace(
            base,
            active_anchor=12,
            exposed_2h_anchor=3,
            exposed_4h_anchor=1,
            forecast_requests_30m=8,
        )
        results = run_full_day(
            seed=77,
            fixture=self.fixture,
            zones=(bounded,),
        )
        self.assertEqual(len(results), 96)
        self.assertEqual([result.tick_index for result in results], list(range(96)))
        self.assertEqual(results[-1].state.minute_index, 24 * 60)
        self.assertEqual(len({result.checksum for result in results}), 96)
        self.assertTrue(
            all(
                zone.request_flow_balance == 0
                for result in results
                for zone in result.zones
            )
        )
        for result in results:
            self.assertEqual(
                result.zones[0].active_drivers,
                target_active(bounded, result.tick_index * 15),
            )
        summaries = hourly_summary(results)
        self.assertEqual(len(summaries), 24)
        self.assertIn("raw_feature_extrema", summaries[0])
        self.assertIn("heat_index_c", summaries[0]["raw_feature_extrema"])
        self.assertIn("behavior_clip_rates", summaries[0])
        self.assertIn("weather_clip_rate", summaries[0])
        self.assertIn("interventions", summaries[0])

    def test_bounded_full_day_is_same_seed_stable_and_seed_sensitive(self):
        base = self.zones[0]
        bounded = replace(
            base,
            active_anchor=8,
            exposed_2h_anchor=2,
            exposed_4h_anchor=1,
            forecast_requests_30m=6,
        )
        first = run_full_day(seed=90, fixture=self.fixture, zones=(bounded,))
        second = run_full_day(seed=90, fixture=self.fixture, zones=(bounded,))
        different = run_full_day(seed=91, fixture=self.fixture, zones=(bounded,))
        self.assertEqual(
            [result.checksum for result in first],
            [result.checksum for result in second],
        )
        self.assertNotEqual(first[-1].checksum, different[-1].checksum)


if __name__ == "__main__":
    unittest.main()
