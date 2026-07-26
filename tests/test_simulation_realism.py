from __future__ import annotations

import unittest
from dataclasses import replace

from heatsafe.simulation import (
    DriverStatus,
    advance_tick,
    audit_full_day,
    initialize_state,
    load_realism_profile,
    load_scenario,
    load_zone_priors,
    run_realism_audit,
    run_full_day,
)


class SimulationRealismTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_scenario("hanoi_heatwave_v1")
        cls.zone = load_zone_priors()[0]

    def test_midnight_carryover_is_bounded_and_never_preloads_four_hours(self):
        state = initialize_state(seed=42, fixture=self.fixture, zones=(self.zone,))
        active = [
            driver
            for driver in state.drivers
            if driver.status in {
                DriverStatus.IDLE,
                DriverStatus.TO_PICKUP,
                DriverStatus.ON_TRIP,
            }
        ]
        self.assertTrue(active)
        self.assertLessEqual(
            max(driver.continuous_exposure_minutes for driver in active), 180
        )
        self.assertEqual(
            sum(driver.continuous_exposure_minutes >= 240 for driver in active), 0
        )

    def test_completed_safepause_creates_a_recovery_boundary(self):
        state = initialize_state(seed=77, fixture=self.fixture, zones=(self.zone,))
        selected = next(
            driver
            for driver in state.drivers
            if driver.status == DriverStatus.IDLE and driver.scheduled_at(30)
        )
        stressed = replace(selected, continuous_exposure_minutes=250)
        state = replace(
            state,
            drivers=tuple(
                stressed if driver.driver_id_hash == stressed.driver_id_hash else driver
                for driver in state.drivers
            ),
        )
        from heatsafe.simulation import PauseControl

        control = PauseControl(
            control_id="recovery-boundary",
            driver_ids=(stressed.driver_id_hash,),
            requested_minute=0,
            pause_duration_minutes=15,
        )
        first = advance_tick(state, fixture=self.fixture, zones=(self.zone,), controls=(control,))
        second = advance_tick(
            first.state, fixture=self.fixture, zones=(self.zone,), controls=(control,)
        )
        recovered = next(
            driver
            for driver in second.state.drivers
            if driver.driver_id_hash == stressed.driver_id_hash
        )
        self.assertLess(recovered.continuous_exposure_minutes, 120)

    def test_full_day_exposure_does_not_saturate_every_active_driver(self):
        bounded = replace(
            self.zone,
            active_anchor=48,
            exposed_2h_anchor=0,
            exposed_4h_anchor=0,
            forecast_requests_30m=12,
        )
        results = run_full_day(seed=42, fixture=self.fixture, zones=(bounded,))
        for result in results[15::4]:
            zone = result.zones[0]
            self.assertLess(zone.exposed_4h, zone.active_drivers)

    def test_realism_profile_is_versioned_and_has_synthetic_provenance(self):
        profile = load_realism_profile("hanoi_heatwave_v1")
        self.assertEqual(profile["classification"], "synthetic-prior")
        self.assertEqual(profile["shift"]["minimum_recovery_minutes"], 15)
        self.assertEqual(profile["shift"]["extended_continuous_shift_minutes"], 300)

    def test_full_day_audit_locks_daypart_relationships(self):
        bounded = replace(
            self.zone,
            active_anchor=48,
            exposed_2h_anchor=0,
            exposed_4h_anchor=0,
            forecast_requests_30m=12,
        )
        audit = audit_full_day(
            run_full_day(seed=91, fixture=self.fixture, zones=(bounded,))
        )
        self.assertTrue(audit.passed, audit.to_dict())
        self.assertEqual(len(audit.hourly), 24)

    def test_streaming_audit_matches_retained_tick_audit(self):
        bounded = replace(
            self.zone,
            active_anchor=48,
            exposed_2h_anchor=0,
            exposed_4h_anchor=0,
            forecast_requests_30m=12,
        )
        retained = audit_full_day(
            run_full_day(seed=77, fixture=self.fixture, zones=(bounded,))
        )
        streaming = run_realism_audit(
            seed=77, fixture=self.fixture, zones=(bounded,)
        )
        self.assertEqual(streaming, retained)


if __name__ == "__main__":
    unittest.main()
