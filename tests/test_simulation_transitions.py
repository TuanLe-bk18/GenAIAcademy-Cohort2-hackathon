from __future__ import annotations

import unittest
from dataclasses import replace

from heatsafe.simulation import (
    DriverStatus,
    OrderStatus,
    PauseControl,
    SimulationInvariantError,
    advance_tick,
    initialize_state,
    load_scenario,
    load_zone_priors,
    require_driver_transition,
    require_order_transition,
)


class TransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_scenario("hanoi_heatwave_v1")
        cls.zones = load_zone_priors()

    def test_driver_transition_matrix_accepts_only_frozen_edges(self):
        require_driver_transition(DriverStatus.IDLE, DriverStatus.TO_PICKUP)
        require_driver_transition(DriverStatus.ON_TRIP, DriverStatus.IDLE)
        require_driver_transition(DriverStatus.PAUSED, DriverStatus.OFFLINE)
        with self.assertRaises(SimulationInvariantError):
            require_driver_transition(DriverStatus.OFFLINE, DriverStatus.ON_TRIP)
        with self.assertRaises(SimulationInvariantError):
            require_driver_transition(DriverStatus.PAUSED, DriverStatus.ON_TRIP)

    def test_order_transition_matrix_rejects_completion_before_pickup(self):
        require_order_transition(OrderStatus.REQUESTED, OrderStatus.MATCHED)
        require_order_transition(OrderStatus.MATCHED, OrderStatus.ON_TRIP)
        require_order_transition(OrderStatus.ON_TRIP, OrderStatus.COMPLETED)
        with self.assertRaises(SimulationInvariantError):
            require_order_transition(OrderStatus.REQUESTED, OrderStatus.COMPLETED)

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
