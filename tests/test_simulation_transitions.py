from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from heatsafe.simulation import (
    DriverStatus,
    OrderStatus,
    PauseControl,
    SimulationInvariantError,
    advance_tick,
    canonical_checksum,
    initialize_state,
    load_scenario,
    load_zone_priors,
    require_driver_transition,
    require_order_transition,
    stable_int,
)


class TransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_scenario("hanoi_heatwave_v1")
        cls.zones = load_zone_priors()

    def test_driver_transition_matrix_accepts_only_frozen_edges(self):
        expected = {
            (DriverStatus.OFFLINE, DriverStatus.OFFLINE),
            (DriverStatus.OFFLINE, DriverStatus.IDLE),
            (DriverStatus.IDLE, DriverStatus.IDLE),
            (DriverStatus.IDLE, DriverStatus.TO_PICKUP),
            (DriverStatus.IDLE, DriverStatus.TO_COOLSTOP),
            (DriverStatus.IDLE, DriverStatus.OFFLINE),
            (DriverStatus.TO_PICKUP, DriverStatus.TO_PICKUP),
            (DriverStatus.TO_PICKUP, DriverStatus.ON_TRIP),
            (DriverStatus.TO_PICKUP, DriverStatus.IDLE),
            (DriverStatus.ON_TRIP, DriverStatus.ON_TRIP),
            (DriverStatus.ON_TRIP, DriverStatus.IDLE),
            (DriverStatus.TO_COOLSTOP, DriverStatus.TO_COOLSTOP),
            (DriverStatus.TO_COOLSTOP, DriverStatus.PAUSED),
            (DriverStatus.TO_COOLSTOP, DriverStatus.IDLE),
            (DriverStatus.PAUSED, DriverStatus.PAUSED),
            (DriverStatus.PAUSED, DriverStatus.IDLE),
            (DriverStatus.PAUSED, DriverStatus.OFFLINE),
        }
        for before in DriverStatus:
            for after in DriverStatus:
                with self.subTest(before=before, after=after):
                    if (before, after) in expected:
                        require_driver_transition(before, after)
                    else:
                        with self.assertRaises(SimulationInvariantError):
                            require_driver_transition(before, after)

    def test_order_transition_matrix_rejects_completion_before_pickup(self):
        expected = {
            (OrderStatus.REQUESTED, OrderStatus.REQUESTED),
            (OrderStatus.REQUESTED, OrderStatus.MATCHED),
            (OrderStatus.REQUESTED, OrderStatus.CANCELLED),
            (OrderStatus.REQUESTED, OrderStatus.UNFULFILLED),
            (OrderStatus.MATCHED, OrderStatus.MATCHED),
            (OrderStatus.MATCHED, OrderStatus.ON_TRIP),
            (OrderStatus.MATCHED, OrderStatus.CANCELLED),
            (OrderStatus.ON_TRIP, OrderStatus.ON_TRIP),
            (OrderStatus.ON_TRIP, OrderStatus.COMPLETED),
            (OrderStatus.COMPLETED, OrderStatus.COMPLETED),
            (OrderStatus.CANCELLED, OrderStatus.CANCELLED),
            (OrderStatus.UNFULFILLED, OrderStatus.UNFULFILLED),
        }
        for before in OrderStatus:
            for after in OrderStatus:
                with self.subTest(before=before, after=after):
                    if (before, after) in expected:
                        require_order_transition(before, after)
                    else:
                        with self.assertRaises(SimulationInvariantError):
                            require_order_transition(before, after)

    def test_tick_generates_valid_order_lifecycle_events(self):
        state = initialize_state(seed=42, fixture=self.fixture, zones=self.zones)
        tick = advance_tick(state, fixture=self.fixture, zones=self.zones)
        event_types = {event.event_type.value for event in tick.state.events}
        self.assertIn("REQUESTED", event_types)
        self.assertIn("MATCHED", event_types)
        self.assertIn("PICKED_UP", event_types)
        self.assertIn("COMPLETED", event_types)
        event_times = {}
        for event in tick.state.events:
            event_times.setdefault(event.order_id, {})[event.event_type.value] = (
                event.event_minute
            )
        for times in event_times.values():
            if "PICKED_UP" in times and "COMPLETED" in times:
                self.assertLessEqual(times["PICKED_UP"], times["COMPLETED"])

    def test_safepause_moves_idle_driver_out_of_active_supply_then_recovers(self):
        state = initialize_state(seed=11, fixture=self.fixture, zones=self.zones)
        selected = next(
            driver
            for driver in state.drivers
            if driver.status == DriverStatus.IDLE and driver.scheduled_at(30)
        )
        control = PauseControl(
            control_id="control-1",
            driver_ids=(selected.driver_id_hash,),
            requested_minute=0,
            pause_duration_minutes=15,
        )
        first = advance_tick(
            state,
            fixture=self.fixture,
            zones=self.zones,
            controls=(control,),
        )
        driver = next(
            item
            for item in first.state.drivers
            if item.driver_id_hash == selected.driver_id_hash
        )
        self.assertIn(driver.status, {DriverStatus.TO_COOLSTOP, DriverStatus.PAUSED})
        self.assertIsNotNone(driver.current_intervention_id)
        second = advance_tick(
            first.state,
            fixture=self.fixture,
            zones=self.zones,
            controls=(control,),
        )
        recovered = next(
            item
            for item in second.state.drivers
            if item.driver_id_hash == selected.driver_id_hash
        )
        self.assertIn(
            recovered.status,
            {
                DriverStatus.IDLE,
                DriverStatus.OFFLINE,
                DriverStatus.TO_PICKUP,
                DriverStatus.ON_TRIP,
            },
        )
        self.assertIsNone(recovered.current_intervention_id)
        intervention = next(
            item
            for item in second.state.interventions
            if item.driver_id_hash == selected.driver_id_hash
        )
        self.assertEqual(intervention.completed_rest_minutes, 15)

    @staticmethod
    def _control_for_travel(
        state,
        driver_id: str,
        *,
        requested_minute: int,
        travel_minutes: int,
        duration: int = 15,
    ) -> PauseControl:
        for index in range(500):
            control_id = f"travel-{travel_minutes}-{index}"
            intervention_id = canonical_checksum(
                (state.run_id, control_id, driver_id)
            )[:32]
            if 2 + stable_int(intervention_id, "travel") % 9 == travel_minutes:
                return PauseControl(
                    control_id=control_id,
                    driver_ids=(driver_id,),
                    requested_minute=requested_minute,
                    pause_duration_minutes=duration,
                )
        raise AssertionError("could not find deterministic travel key")

    def test_partial_pause_progress_then_exact_completion(self):
        state = initialize_state(seed=111, fixture=self.fixture, zones=self.zones)
        selected = next(
            driver
            for driver in state.drivers
            if driver.status == DriverStatus.IDLE and driver.scheduled_at(30)
        )
        control = self._control_for_travel(
            state,
            selected.driver_id_hash,
            requested_minute=10,
            travel_minutes=2,
        )
        with patch("heatsafe.simulation.engine.sample_requests", return_value=0):
            partial = advance_tick(
                state,
                fixture=self.fixture,
                zones=self.zones,
                controls=(control,),
            )
            intervention = next(
                item
                for item in partial.state.interventions
                if item.driver_id_hash == selected.driver_id_hash
            )
            self.assertEqual(intervention.status.value, "PAUSED")
            self.assertEqual(intervention.completed_rest_minutes, 3)
            completed = advance_tick(
                partial.state,
                fixture=self.fixture,
                zones=self.zones,
                controls=(control,),
            )
        intervention = next(
            item
            for item in completed.state.interventions
            if item.driver_id_hash == selected.driver_id_hash
        )
        self.assertEqual(intervention.status.value, "COMPLETED")
        self.assertEqual(intervention.completed_rest_minutes, 15)

    def test_to_coolstop_is_online_but_not_active_supply(self):
        state = initialize_state(seed=112, fixture=self.fixture, zones=self.zones)
        selected = next(
            driver
            for driver in state.drivers
            if driver.status == DriverStatus.IDLE and driver.scheduled_at(30)
        )
        control = self._control_for_travel(
            state,
            selected.driver_id_hash,
            requested_minute=10,
            travel_minutes=10,
        )
        with patch("heatsafe.simulation.engine.sample_requests", return_value=0):
            result = advance_tick(
                state,
                fixture=self.fixture,
                zones=self.zones,
                controls=(control,),
            )
        driver = next(
            item
            for item in result.state.drivers
            if item.driver_id_hash == selected.driver_id_hash
        )
        zone = next(item for item in result.zones if item.zone_id == driver.zone_id)
        self.assertEqual(driver.status, DriverStatus.TO_COOLSTOP)
        self.assertEqual(zone.to_coolstop_drivers, 1)
        self.assertGreaterEqual(zone.online_drivers - zone.active_drivers, 1)

    def test_shift_end_records_explicit_partial_pause(self):
        state = initialize_state(seed=113, fixture=self.fixture, zones=self.zones)
        selected = next(
            driver
            for driver in state.drivers
            if driver.status == DriverStatus.IDLE
            and driver.scheduled_at(0)
            and not driver.scheduled_at(15)
        )
        control = self._control_for_travel(
            state,
            selected.driver_id_hash,
            requested_minute=0,
            travel_minutes=2,
        )
        with patch("heatsafe.simulation.engine.sample_requests", return_value=0):
            first = advance_tick(
                state,
                fixture=self.fixture,
                zones=self.zones,
                controls=(control,),
            )
            second = advance_tick(
                first.state,
                fixture=self.fixture,
                zones=self.zones,
                controls=(control,),
            )
        driver = next(
            item
            for item in second.state.drivers
            if item.driver_id_hash == selected.driver_id_hash
        )
        intervention = next(
            item
            for item in second.state.interventions
            if item.driver_id_hash == selected.driver_id_hash
        )
        self.assertEqual(driver.status, DriverStatus.OFFLINE)
        self.assertIsNone(driver.current_intervention_id)
        self.assertEqual(intervention.status.value, "CANCELLED")
        self.assertEqual(intervention.cancel_reason, "SHIFT_ENDED_PARTIAL_PAUSE")
        self.assertGreater(intervention.completed_rest_minutes, 0)
        self.assertLess(
            intervention.completed_rest_minutes,
            intervention.planned_duration_minutes,
        )

    def test_same_control_is_not_applied_twice(self):
        state = initialize_state(seed=12, fixture=self.fixture, zones=self.zones)
        selected = next(
            driver for driver in state.drivers if driver.status == DriverStatus.IDLE
        )
        duplicated = PauseControl(
            control_id="same-control",
            driver_ids=(selected.driver_id_hash, selected.driver_id_hash),
            requested_minute=0,
            pause_duration_minutes=30,
        )
        result = advance_tick(
            state,
            fixture=self.fixture,
            zones=self.zones,
            controls=(duplicated, duplicated),
        )
        matching = [
            item
            for item in result.state.interventions
            if item.control_id == duplicated.control_id
        ]
        self.assertEqual(len(matching), 1)

    def test_invalid_control_policy_fails_closed(self):
        state = initialize_state(seed=13, fixture=self.fixture, zones=self.zones)
        selected = next(
            driver for driver in state.drivers if driver.status == DriverStatus.IDLE
        )
        invalid = PauseControl(
            control_id="invalid",
            driver_ids=(selected.driver_id_hash,),
            requested_minute=0,
            pause_duration_minutes=20,
        )
        with self.assertRaises(ValueError):
            advance_tick(
                state,
                fixture=self.fixture,
                zones=self.zones,
                controls=(invalid,),
            )

    def test_busy_driver_control_cancels_after_max_start_delay(self):
        state = initialize_state(seed=14, fixture=self.fixture, zones=self.zones)
        selected = next(
            driver for driver in state.drivers if driver.status == DriverStatus.ON_TRIP
        )
        delayed_driver = replace(selected, transition_due_minute=100)
        state = replace(
            state,
            drivers=tuple(
                delayed_driver
                if driver.driver_id_hash == selected.driver_id_hash
                else driver
                for driver in state.drivers
            ),
        )
        control = PauseControl(
            control_id="delay-control",
            driver_ids=(selected.driver_id_hash,),
            requested_minute=0,
            pause_duration_minutes=15,
            max_start_delay_minutes=45,
        )
        result = None
        for _ in range(4):
            result = advance_tick(
                state,
                fixture=self.fixture,
                zones=self.zones,
                controls=(control,),
            )
            state = result.state
        self.assertIsNotNone(result)
        driver = next(
            item
            for item in result.state.drivers
            if item.driver_id_hash == selected.driver_id_hash
        )
        intervention = next(
            item
            for item in result.state.interventions
            if item.driver_id_hash == selected.driver_id_hash
        )
        self.assertIsNone(driver.current_intervention_id)
        self.assertEqual(intervention.status.value, "CANCELLED")
        self.assertEqual(intervention.cancel_reason, "MAX_START_DELAY_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
