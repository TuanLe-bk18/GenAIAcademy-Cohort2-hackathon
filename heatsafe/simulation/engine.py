from __future__ import annotations

import json
import hashlib
import math
from enum import StrEnum
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from heatsafe.ingestion import calculate_heat_index

from .demand import (
    advance_shocks,
    demand_mean_15m,
    sample_requests,
    target_active,
)
from .models import (
    ACTIVE_STATUSES,
    AcclimatizationClass,
    DriverState,
    DriverStatus,
    InterventionState,
    InterventionStatus,
    OrderEvent,
    OrderEventType,
    OrderState,
    OrderStatus,
    PauseControl,
    SimulationState,
    TickResult,
    WeatherState,
    ZonePrior,
)
from .randomness import (
    DeterministicRandom,
    canonical_checksum,
    stable_int,
)
from .scenario import ScenarioFixture, load_scenario
from .transitions import (
    MODEL_BOUNDS,
    WEATHER_FEATURES,
    project_scoring,
    project_zones,
    route_heat_load,
    validate_state,
    workload_intensity,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = ROOT / "data" / "demo_snapshot.json"
GENERATOR_VERSION = "stateful-replay-v1"
SCENARIO_VERSION = "hanoi_heatwave_v1"
_MASK_60 = (1 << 60) - 1
_MASK_120 = (1 << 120) - 1


def load_zone_priors(path: Path = DEFAULT_SNAPSHOT) -> tuple[ZonePrior, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    zones = []
    for raw in document["zones"]:
        zones.append(
            ZonePrior(
                zone_id=raw["zone_id"],
                name=raw["name"],
                latitude=float(raw["latitude"]),
                longitude=float(raw["longitude"]),
                active_anchor=int(raw["active_drivers"]),
                exposed_2h_anchor=int(raw["exposed_2h"]),
                exposed_4h_anchor=int(raw["exposed_4h"]),
                forecast_requests_30m=int(raw["forecast_requests_30m"]),
                avg_platform_contribution_vnd=int(
                    raw["avg_platform_contribution_vnd"]
                ),
                avg_driver_earnings_vnd=int(raw["avg_driver_earnings_vnd"]),
                coolstop_name=raw["coolstop_name"],
                coolstop_latitude=float(raw["coolstop_latitude"]),
                coolstop_longitude=float(raw["coolstop_longitude"]),
            )
        )
    return tuple(sorted(zones, key=lambda zone: zone.zone_id))


def weather_at(fixture: ScenarioFixture, minute_index: int) -> WeatherState:
    row = fixture.weather[min(95, max(0, minute_index // 15))]
    temperature = float(row["temperature_c"])
    humidity = float(row["relative_humidity_percent"])
    return WeatherState(
        event_time=row["local_time"],
        temperature_c=temperature,
        humidity_percent=humidity,
        heat_index_c=round(calculate_heat_index(temperature, humidity), 4),
        precipitation_mm=float(row["precipitation_mm"]),
        cloud_cover_pct=float(row["cloud_cover_pct"]),
        wind_speed_mps=float(row["wind_speed_mps"]),
        shortwave_radiation_wm2=float(row["shortwave_radiation_wm2"]),
    )


def _schedule_bits(
    zone: ZonePrior,
    driver_ids: tuple[str, ...],
    seed: int,
) -> dict[str, int]:
    """Build exact 15-minute supply targets with stable, sticky assignments."""
    bits = {driver_id: 0 for driver_id in driver_ids}
    active: set[str] = set()
    online_since: dict[str, int] = {}
    offline_since = {driver_id: 0 for driver_id in driver_ids}
    for slot in range(96):
        desired = target_active(zone, slot * 15)
        if len(active) > desired:
            removable = sorted(
                active,
                key=lambda driver_id: (
                    slot - online_since.get(driver_id, slot) < 4,
                    -(slot - online_since.get(driver_id, slot)),
                    stable_int(
                        SCENARIO_VERSION,
                        seed,
                        zone.zone_id,
                        slot,
                        driver_id,
                        "shift-exit",
                    ),
                ),
            )
            for driver_id in removable[: len(active) - desired]:
                active.remove(driver_id)
                offline_since[driver_id] = slot
        elif len(active) < desired:
            candidates = sorted(
                (driver_id for driver_id in driver_ids if driver_id not in active),
                key=lambda driver_id: (
                    slot - offline_since.get(driver_id, 0) < 2,
                    -(slot - offline_since.get(driver_id, 0)),
                    stable_int(
                        SCENARIO_VERSION,
                        seed,
                        zone.zone_id,
                        slot,
                        driver_id,
                        "shift-enter",
                    ),
                ),
            )
            for driver_id in candidates[: desired - len(active)]:
                active.add(driver_id)
                online_since[driver_id] = slot
        if len(active) != desired:
            raise RuntimeError(f"cannot allocate exact supply target for {zone.zone_id}")
        for driver_id in active:
            bits[driver_id] |= 1 << slot
    return bits


def _largest_remainder(total: int, shares: tuple[float, ...]) -> tuple[int, ...]:
    raw = [total * share for share in shares]
    values = [math.floor(value) for value in raw]
    remaining = total - sum(values)
    order = sorted(range(len(shares)), key=lambda i: (-(raw[i] - values[i]), i))
    for index in order[:remaining]:
        values[index] += 1
    return tuple(values)


def _initial_order(
    driver: DriverState,
    status: DriverStatus,
    seed: int,
    minute_index: int,
) -> tuple[DriverState, OrderState]:
    stream = DeterministicRandom(
        SCENARIO_VERSION, seed, driver.driver_id_hash, "initial-order"
    )
    order_id = f"initial-{driver.driver_id_hash}"
    distance = min(18.0, max(0.8, math.exp(math.log(4.0) + 0.55 * stream.normal())))
    trip_minutes = min(45, max(6, math.ceil(60 * distance / 22)))
    pickup_minutes = max(2, min(10, round(stream.triangular(2, 5, 10))))
    order = OrderState(
        order_id=order_id,
        origin_zone_id=driver.zone_id,
        destination_zone_id=driver.zone_id,
        requested_minute=-pickup_minutes,
        status=OrderStatus.MATCHED if status == DriverStatus.TO_PICKUP else OrderStatus.ON_TRIP,
        driver_id_hash=driver.driver_id_hash,
        accepted_minute=-pickup_minutes + 1,
        pickup_minute=None if status == DriverStatus.TO_PICKUP else -1,
        distance_km=round(distance, 4),
        pickup_duration_minutes=pickup_minutes,
        trip_duration_minutes=trip_minutes,
    )
    due = stream.randint(1, pickup_minutes if status == DriverStatus.TO_PICKUP else trip_minutes)
    return (
        replace(
            driver,
            status=status,
            current_order_id=order_id,
            transition_due_minute=minute_index + due,
        ),
        order,
    )


def initialize_state(
    *,
    seed: int = 42,
    fixture: ScenarioFixture | None = None,
    zones: tuple[ZonePrior, ...] | None = None,
) -> SimulationState:
    fixture = fixture or load_scenario(SCENARIO_VERSION)
    zones = zones or load_zone_priors()
    start_time = fixture.weather[0]["local_time"]
    run_id = canonical_checksum(
        {
            "scenario_version": SCENARIO_VERSION,
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "start_time": start_time,
        }
    )[:24]
    drivers: list[DriverState] = []
    orders: list[OrderState] = []
    for zone in zones:
        roster_size = zone.active_anchor * 2
        driver_ids = tuple(
            f"sim-{canonical_checksum((SCENARIO_VERSION, seed, zone.zone_id, index))[:20]}"
            for index in range(roster_size)
        )
        schedules = _schedule_bits(zone, driver_ids, seed)
        active_ids = sorted(
            driver_id for driver_id in driver_ids if schedules[driver_id] & 1
        )
        active_count = len(active_ids)
        exposed_2h = round(
            active_count * zone.exposed_2h_anchor / zone.active_anchor
        )
        exposed_4h = min(
            exposed_2h,
            round(active_count * zone.exposed_4h_anchor / zone.active_anchor),
        )
        exposure_order = sorted(
            active_ids,
            key=lambda driver_id: stable_int(
                SCENARIO_VERSION, seed, zone.zone_id, driver_id, "exposure"
            ),
        )
        four_hour_ids = set(exposure_order[:exposed_4h])
        two_hour_ids = set(exposure_order[exposed_4h:exposed_2h])
        idle_count, pickup_count, trip_count = _largest_remainder(
            active_count, (0.60, 0.20, 0.20)
        )
        status_order = sorted(
            active_ids,
            key=lambda driver_id: stable_int(
                SCENARIO_VERSION, seed, zone.zone_id, driver_id, "status"
            ),
        )
        statuses = {
            **{driver_id: DriverStatus.IDLE for driver_id in status_order[:idle_count]},
            **{
                driver_id: DriverStatus.TO_PICKUP
                for driver_id in status_order[idle_count:idle_count + pickup_count]
            },
            **{
                driver_id: DriverStatus.ON_TRIP
                for driver_id in status_order[
                    idle_count + pickup_count:idle_count + pickup_count + trip_count
                ]
            },
        }
        for driver_id in driver_ids:
            stream = DeterministicRandom(
                SCENARIO_VERSION, seed, zone.zone_id, driver_id, "initial-driver"
            )
            percentile = stream.uniform()
            acclimatization = (
                AcclimatizationClass.LOW
                if percentile < 0.20
                else AcclimatizationClass.MEDIUM
                if percentile < 0.80
                else AcclimatizationClass.HIGH
            )
            status = statuses.get(driver_id, DriverStatus.OFFLINE)
            if driver_id in four_hour_ids:
                exposure = stream.randint(240, 360)
            elif driver_id in two_hour_ids:
                exposure = stream.randint(120, 239)
            elif status in ACTIVE_STATUSES:
                exposure = stream.randint(0, 119)
            else:
                exposure = 0
            prior_distance = (
                round(3.0 + stream.uniform() * 5.0, 4)
                if status in ACTIVE_STATUSES
                else 0.0
            )
            prior_trip_bit = 1 << stream.randint(0, 59) if status in ACTIVE_STATUSES else 0
            driver = DriverState(
                driver_id_hash=driver_id,
                zone_id=zone.zone_id,
                latitude=zone.latitude + (stream.uniform() - 0.5) * 0.018,
                longitude=zone.longitude + (stream.uniform() - 0.5) * 0.018,
                status=status,
                schedule_bits=schedules[driver_id],
                acclimatization_class=acclimatization,
                continuous_exposure_minutes=exposure,
                heat_dose_120m=round(exposure * 0.08, 4),
                hydration_gap_minutes=stream.randint(15, 120)
                if status in ACTIVE_STATUSES
                else 0,
                trips_minute_bits=prior_trip_bit,
                distance_by_minute=(prior_distance,),
                online_since_minute=0 if status in ACTIVE_STATUSES else None,
                offline_since_minute=None if status in ACTIVE_STATUSES else 0,
            )
            if status in {DriverStatus.TO_PICKUP, DriverStatus.ON_TRIP}:
                driver, order = _initial_order(driver, status, seed, 0)
                orders.append(order)
            drivers.append(driver)
    state = SimulationState(
        scenario_version=SCENARIO_VERSION,
        generator_version=GENERATOR_VERSION,
        run_id=run_id,
        seed=seed,
        start_time=start_time,
        minute_index=0,
        drivers=tuple(sorted(drivers, key=lambda driver: driver.driver_id_hash)),
        orders=tuple(sorted(orders, key=lambda order: order.order_id)),
        interventions=(),
        events=(),
        city_shock=1.0,
        zone_shocks=tuple((zone.zone_id, 1.0) for zone in zones),
    )
    validate_state(state, zones)
    return state


def _event(
    state: SimulationState,
    order: OrderState,
    event_type: OrderEventType,
    minute: int,
    driver_id: str | None = None,
    prior_status: OrderStatus | None = None,
) -> OrderEvent:
    return OrderEvent(
        event_id=canonical_checksum(
            (state.run_id, order.order_id, event_type.value, minute)
        )[:32],
        order_id=order.order_id,
        event_type=event_type,
        event_minute=minute,
        zone_id=order.origin_zone_id,
        driver_id_hash=driver_id,
        prior_status=prior_status,
    )


def _destination_zone(
    origin: ZonePrior,
    zones: tuple[ZonePrior, ...],
    stream: DeterministicRandom,
) -> ZonePrior:
    if stream.uniform() < 0.65:
        return origin
    candidates = [zone for zone in zones if zone.zone_id != origin.zone_id]
    if not candidates:
        return origin
    weights = [
        1
        / max(
            0.01,
            math.hypot(
                zone.latitude - origin.latitude,
                zone.longitude - origin.longitude,
            ),
        )
        for zone in candidates
    ]
    threshold = stream.uniform() * sum(weights)
    running = 0.0
    for zone, weight in zip(candidates, weights):
        running += weight
        if running >= threshold:
            return zone
    return candidates[-1]


def _trip_details(
    state: SimulationState,
    order: OrderState,
    zones: tuple[ZonePrior, ...],
    minute: int,
) -> OrderState:
    zone_index = {zone.zone_id: zone for zone in zones}
    origin = zone_index[order.origin_zone_id]
    stream = DeterministicRandom(
        state.scenario_version, state.seed, minute, order.order_id, "trip"
    )
    destination = _destination_zone(origin, zones, stream)
    distance = min(18.0, max(0.8, math.exp(math.log(4.0) + 0.55 * stream.normal())))
    hour = minute / 60
    if hour < 5 or hour >= 23:
        speed = 26.0
    elif 7 <= hour <= 9.5 or 16.5 <= hour <= 19.5:
        speed = 16.0
    else:
        speed = 22.0
    duration = min(45, max(6, math.ceil(60 * distance / speed)))
    pickup = max(2, min(10, round(stream.triangular(2, 5, 10))))
    economics = 0.92 + 0.16 * stream.uniform()
    driver_pay = max(0, round(origin.avg_driver_earnings_vnd * economics))
    contribution = max(0, round(origin.avg_platform_contribution_vnd * economics))
    return replace(
        order,
        destination_zone_id=destination.zone_id,
        distance_km=round(distance, 4),
        pickup_duration_minutes=pickup,
        trip_duration_minutes=duration,
        driver_pay_vnd=driver_pay,
        platform_contribution_vnd=contribution,
        fare_vnd=driver_pay + contribution,
    )


def _apply_due_transitions(
    state: SimulationState,
    drivers: dict[str, DriverState],
    orders: dict[str, OrderState],
    interventions: dict[str, InterventionState],
    events: list[OrderEvent],
    minute: int,
) -> tuple[dict[str, tuple[float, int, int]], set[str]]:
    economics: dict[str, tuple[float, int, int]] = {}
    terminal_orders: set[str] = set()
    for driver_id in sorted(drivers):
        driver = drivers[driver_id]
        if driver.transition_due_minute is None or driver.transition_due_minute > minute:
            continue
        if driver.status == DriverStatus.TO_PICKUP:
            order = orders[driver.current_order_id or ""]
            if stable_int(order.order_id, "post-match-cancel") % 100 < 2:
                cancelled = replace(
                    order,
                    status=OrderStatus.CANCELLED,
                    cancelled_minute=minute,
                )
                events.append(
                    _event(
                        state,
                        cancelled,
                        OrderEventType.CANCELLED,
                        minute,
                        driver_id,
                        OrderStatus.MATCHED,
                    )
                )
                terminal_orders.add(order.order_id)
                drivers[driver_id] = replace(
                    driver,
                    status=DriverStatus.IDLE,
                    current_order_id=None,
                    transition_due_minute=None,
                )
            else:
                picked_up = replace(
                    order, status=OrderStatus.ON_TRIP, pickup_minute=minute
                )
                orders[order.order_id] = picked_up
                events.append(
                    _event(
                        state,
                        picked_up,
                        OrderEventType.PICKED_UP,
                        minute,
                        driver_id,
                        OrderStatus.MATCHED,
                    )
                )
                drivers[driver_id] = replace(
                    driver,
                    status=DriverStatus.ON_TRIP,
                    transition_due_minute=minute + order.trip_duration_minutes,
                )
        elif driver.status == DriverStatus.ON_TRIP:
            order = orders[driver.current_order_id or ""]
            completed = replace(
                order, status=OrderStatus.COMPLETED, completed_minute=minute
            )
            events.append(
                _event(
                    state,
                    completed,
                    OrderEventType.COMPLETED,
                    minute,
                    driver_id,
                    OrderStatus.ON_TRIP,
                )
            )
            terminal_orders.add(order.order_id)
            economics[driver_id] = (
                order.distance_km,
                order.driver_pay_vnd,
                order.platform_contribution_vnd,
            )
            next_status = DriverStatus.IDLE
            due = None
            if driver.current_intervention_id:
                intervention = interventions[driver.current_intervention_id]
                travel = 2 + stable_int(intervention.intervention_id, "travel") % 9
                interventions[intervention.intervention_id] = replace(
                    intervention, status=InterventionStatus.TO_COOLSTOP
                )
                next_status = DriverStatus.TO_COOLSTOP
                due = minute + travel
            elif driver.pending_offline:
                next_status = DriverStatus.OFFLINE
            drivers[driver_id] = replace(
                driver,
                status=next_status,
                current_order_id=None,
                transition_due_minute=due,
                pending_offline=False,
                offline_since_minute=minute if next_status == DriverStatus.OFFLINE else None,
            )
        elif driver.status == DriverStatus.TO_COOLSTOP:
            intervention = interventions[driver.current_intervention_id or ""]
            interventions[intervention.intervention_id] = replace(
                intervention,
                status=InterventionStatus.PAUSED,
                started_minute=minute,
            )
            drivers[driver_id] = replace(
                driver,
                status=DriverStatus.PAUSED,
                transition_due_minute=minute + intervention.planned_duration_minutes,
            )
        elif driver.status == DriverStatus.PAUSED:
            intervention = interventions[driver.current_intervention_id or ""]
            interventions[intervention.intervention_id] = replace(
                intervention,
                status=InterventionStatus.COMPLETED,
                completed_minute=minute,
            )
            scheduled = driver.scheduled_at(minute)
            drivers[driver_id] = replace(
                driver,
                status=DriverStatus.IDLE if scheduled else DriverStatus.OFFLINE,
                current_intervention_id=None,
                transition_due_minute=None,
                pending_offline=False,
                online_since_minute=driver.online_since_minute if scheduled else None,
                offline_since_minute=None if scheduled else minute,
            )
    return economics, terminal_orders


def _apply_controls(
    state: SimulationState,
    controls: tuple[PauseControl, ...],
    drivers: dict[str, DriverState],
    interventions: dict[str, InterventionState],
    minute: int,
) -> None:
    for control in sorted(controls, key=lambda item: item.control_id):
        if control.requested_minute != minute:
            continue
        duration = control.pause_duration_minutes
        if duration not in {15, 30} or not 0 <= control.max_start_delay_minutes <= 45:
            raise ValueError("control violates the P0 SafePause policy")
        baseline_by_driver = dict(control.baseline_risk_by_driver)
        action_by_driver = dict(control.action_risk_by_driver)
        for driver_id in sorted(set(control.driver_ids)):
            driver = drivers.get(driver_id)
            if driver is None or driver.current_intervention_id is not None:
                continue
            intervention_id = canonical_checksum(
                (state.run_id, control.control_id, driver_id)
            )[:32]
            intervention = InterventionState(
                intervention_id=intervention_id,
                control_id=control.control_id,
                driver_id_hash=driver_id,
                zone_id=driver.zone_id,
                assigned_minute=minute,
                planned_duration_minutes=duration,
                max_start_delay_minutes=control.max_start_delay_minutes,
                proposal_id=control.proposal_id,
                pause_start_delay_minutes=control.pause_start_delay_minutes,
                baseline_risk_probability=baseline_by_driver.get(driver_id),
                action_risk_probability=action_by_driver.get(driver_id),
            )
            if driver.status == DriverStatus.OFFLINE:
                interventions[intervention_id] = replace(
                    intervention,
                    status=InterventionStatus.CANCELLED,
                    completed_minute=minute,
                    cancel_reason="DRIVER_OFFLINE",
                )
                continue
            interventions[intervention_id] = intervention
            if driver.status == DriverStatus.IDLE:
                travel = 2 + stable_int(intervention_id, "travel") % 9
                interventions[intervention_id] = replace(
                    intervention, status=InterventionStatus.TO_COOLSTOP
                )
                drivers[driver_id] = replace(
                    driver,
                    status=DriverStatus.TO_COOLSTOP,
                    current_intervention_id=intervention_id,
                    transition_due_minute=minute + travel,
                )
            else:
                drivers[driver_id] = replace(
                    driver, current_intervention_id=intervention_id
                )


def _expire_delayed_interventions(
    drivers: dict[str, DriverState],
    interventions: dict[str, InterventionState],
    minute: int,
) -> None:
    for intervention_id in sorted(interventions):
        intervention = interventions[intervention_id]
        if (
            intervention.status != InterventionStatus.ASSIGNED
            or minute
            <= intervention.assigned_minute + intervention.max_start_delay_minutes
        ):
            continue
        interventions[intervention_id] = replace(
            intervention,
            status=InterventionStatus.CANCELLED,
            completed_minute=minute,
            cancel_reason="MAX_START_DELAY_EXCEEDED",
        )
        driver = drivers[intervention.driver_id_hash]
        if driver.current_intervention_id == intervention_id:
            drivers[intervention.driver_id_hash] = replace(
                driver, current_intervention_id=None
            )


def _apply_shift_boundaries(
    drivers: dict[str, DriverState],
    interventions: dict[str, InterventionState],
    minute: int,
) -> None:
    for driver_id in sorted(drivers):
        driver = drivers[driver_id]
        scheduled = driver.scheduled_at(minute)
        if scheduled and driver.status == DriverStatus.OFFLINE:
            drivers[driver_id] = replace(
                driver,
                status=DriverStatus.IDLE,
                online_since_minute=minute,
                offline_since_minute=None,
            )
        elif not scheduled and driver.status == DriverStatus.IDLE:
            drivers[driver_id] = replace(
                driver,
                status=DriverStatus.OFFLINE,
                online_since_minute=None,
                offline_since_minute=minute,
            )
        elif not scheduled and driver.status in {
            DriverStatus.TO_COOLSTOP,
            DriverStatus.PAUSED,
        }:
            intervention = interventions[driver.current_intervention_id or ""]
            reason = (
                "SHIFT_ENDED_PARTIAL_PAUSE"
                if intervention.completed_rest_minutes > 0
                else "SHIFT_ENDED_BEFORE_PAUSE"
            )
            interventions[intervention.intervention_id] = replace(
                intervention,
                status=InterventionStatus.CANCELLED,
                completed_minute=minute,
                cancel_reason=reason,
            )
            drivers[driver_id] = replace(
                driver,
                status=DriverStatus.OFFLINE,
                current_intervention_id=None,
                transition_due_minute=None,
                pending_offline=False,
                online_since_minute=None,
                offline_since_minute=minute,
            )
        elif not scheduled and driver.status in {
            DriverStatus.TO_PICKUP,
            DriverStatus.ON_TRIP,
        }:
            drivers[driver_id] = replace(driver, pending_offline=True)


def _expire_requests(
    state: SimulationState,
    orders: dict[str, OrderState],
    events: list[OrderEvent],
    minute: int,
) -> set[str]:
    terminal: set[str] = set()
    for order in sorted(orders.values(), key=lambda item: item.order_id):
        if order.status != OrderStatus.REQUESTED:
            continue
        age = minute - order.requested_minute
        cancels = stable_int(order.order_id, "pre-match-cancel") % 100 < 4
        cancel_minute = 1 + stable_int(order.order_id, "cancel-minute") % 8
        if cancels and age >= cancel_minute:
            terminal_order = replace(
                order, status=OrderStatus.CANCELLED, cancelled_minute=minute
            )
            events.append(
                _event(
                    state,
                    terminal_order,
                    OrderEventType.CANCELLED,
                    minute,
                    prior_status=OrderStatus.REQUESTED,
                )
            )
            terminal.add(order.order_id)
        elif age >= 8:
            terminal_order = replace(order, status=OrderStatus.UNFULFILLED)
            events.append(
                _event(
                    state,
                    terminal_order,
                    OrderEventType.UNFULFILLED,
                    minute,
                    prior_status=OrderStatus.REQUESTED,
                )
            )
            terminal.add(order.order_id)
    return terminal


def _generate_requests(
    state: SimulationState,
    zones: tuple[ZonePrior, ...],
    fixture: ScenarioFixture,
    city_shock: float,
    zone_shocks: tuple[tuple[str, float], ...],
    orders: dict[str, OrderState],
    events: list[OrderEvent],
    minute: int,
) -> None:
    event_time = state.start_time + timedelta(minutes=minute)
    weather = weather_at(fixture, minute)
    anchor_weather = weather_at(fixture, 13 * 60)
    shock_index = dict(zone_shocks)
    for zone in zones:
        expected = demand_mean_15m(
            zone,
            event_time,
            weather,
            anchor_weather,
            city_shock,
            shock_index[zone.zone_id],
        )
        count = sample_requests(
            scenario_version=state.scenario_version,
            seed=state.seed,
            minute_index=minute,
            zone=zone,
            expected_15m=expected,
        )
        for index in range(count):
            order_id = canonical_checksum(
                (state.run_id, minute, zone.zone_id, index, "order")
            )[:28]
            order = OrderState(
                order_id=order_id,
                origin_zone_id=zone.zone_id,
                destination_zone_id=zone.zone_id,
                requested_minute=minute,
                status=OrderStatus.REQUESTED,
            )
            orders[order_id] = order
            events.append(_event(state, order, OrderEventType.REQUESTED, minute))


def _match_requests(
    state: SimulationState,
    zones: tuple[ZonePrior, ...],
    drivers: dict[str, DriverState],
    orders: dict[str, OrderState],
    events: list[OrderEvent],
    minute: int,
) -> None:
    zone_index = {zone.zone_id: zone for zone in zones}
    available: dict[str, list[str]] = defaultdict(list)
    for driver in drivers.values():
        final_minute = min(1439, minute + 59)
        scheduled_through = all(
            driver.scheduled_at(candidate)
            for candidate in range(minute, final_minute + 1, 15)
        )
        if (
            driver.status == DriverStatus.IDLE
            and driver.current_intervention_id is None
            and scheduled_through
        ):
            available[driver.zone_id].append(driver.driver_id_hash)
    for zone_id, driver_ids in available.items():
        zone = zone_index[zone_id]
        driver_ids.sort(
            key=lambda driver_id: (
                math.hypot(
                    drivers[driver_id].latitude - zone.latitude,
                    drivers[driver_id].longitude - zone.longitude,
                ),
                driver_id,
            )
        )
    requested = sorted(
        (order for order in orders.values() if order.status == OrderStatus.REQUESTED),
        key=lambda order: (order.requested_minute, order.order_id),
    )
    for order in requested:
        pool = available[order.origin_zone_id]
        declined: list[str] = []
        matched_driver = None
        while pool:
            driver_id = pool.pop(0)
            driver = drivers[driver_id]
            acceptance = 0.90 - (0.10 if workload_intensity(driver) > 1.8 else 0)
            acceptance = min(0.95, max(0.70, acceptance))
            stream = DeterministicRandom(
                state.scenario_version,
                state.seed,
                minute,
                order.order_id,
                driver_id,
                "accept",
            )
            if stream.uniform() <= acceptance:
                matched_driver = driver_id
                break
            declined.append(driver_id)
        pool.extend(declined)
        if matched_driver is None:
            continue
        detailed = _trip_details(state, order, zones, minute)
        matched = replace(
            detailed,
            status=OrderStatus.MATCHED,
            driver_id_hash=matched_driver,
            accepted_minute=minute,
        )
        orders[order.order_id] = matched
        driver = drivers[matched_driver]
        drivers[matched_driver] = replace(
            driver,
            status=DriverStatus.TO_PICKUP,
            current_order_id=order.order_id,
            transition_due_minute=minute + matched.pickup_duration_minutes,
        )
        events.append(
            _event(
                state,
                matched,
                OrderEventType.MATCHED,
                minute,
                matched_driver,
                OrderStatus.REQUESTED,
            )
        )


def _update_driver_metrics(
    drivers: dict[str, DriverState],
    interventions: dict[str, InterventionState],
    economics: dict[str, tuple[float, int, int]],
    heat_index_c: float,
) -> None:
    decay = math.exp(-math.log(2) / 120)
    acclimatization = {
        AcclimatizationClass.LOW: 1.15,
        AcclimatizationClass.MEDIUM: 1.00,
        AcclimatizationClass.HIGH: 0.90,
    }
    for driver_id in sorted(drivers):
        driver = drivers[driver_id]
        exposed = driver.status in {
            DriverStatus.IDLE,
            DriverStatus.TO_PICKUP,
            DriverStatus.ON_TRIP,
            DriverStatus.TO_COOLSTOP,
        }
        if exposed:
            exposure = driver.continuous_exposure_minutes + 1
        elif driver.status == DriverStatus.PAUSED:
            exposure = max(0, driver.continuous_exposure_minutes - 3)
        else:
            exposure = max(0, driver.continuous_exposure_minutes - 1)
        paused = driver.status == DriverStatus.PAUSED
        rest_bits = ((driver.rest_minute_bits << 1) | int(paused)) & _MASK_120
        if paused and rest_bits & ((1 << 5) - 1) == (1 << 5) - 1:
            hydration = 0
        elif exposed:
            hydration = driver.hydration_gap_minutes + 1
        else:
            hydration = max(0, driver.hydration_gap_minutes - 1)
        distance, earnings, contribution = economics.get(driver_id, (0.0, 0, 0))
        trip_completed = driver_id in economics
        trips_bits = ((driver.trips_minute_bits << 1) | int(trip_completed)) & _MASK_60
        distances = (driver.distance_by_minute + (distance,))[-60:]
        earnings_values = (driver.earnings_by_minute + (earnings,))[-60:]
        contribution_values = (
            driver.contribution_by_minute + (contribution,)
        )[-60:]
        heat_input = (
            max(0.0, heat_index_c - 27)
            * route_heat_load(driver)
            * acclimatization[driver.acclimatization_class]
            / 60
            if exposed
            else 0.0
        )
        drivers[driver_id] = replace(
            driver,
            continuous_exposure_minutes=exposure,
            heat_dose_120m=round(driver.heat_dose_120m * decay + heat_input, 6),
            hydration_gap_minutes=hydration,
            rest_minute_bits=rest_bits,
            trips_minute_bits=trips_bits,
            distance_by_minute=distances,
            earnings_by_minute=earnings_values,
            contribution_by_minute=contribution_values,
        )
        if paused and driver.current_intervention_id:
            intervention = interventions[driver.current_intervention_id]
            interventions[intervention.intervention_id] = replace(
                intervention,
                completed_rest_minutes=intervention.completed_rest_minutes + 1,
            )

def advance_minute(
    state: SimulationState,
    *,
    fixture: ScenarioFixture,
    zones: tuple[ZonePrior, ...],
    controls: tuple[PauseControl, ...] = (),
) -> SimulationState:
    minute = state.minute_index
    if not 0 <= minute < 24 * 60:
        raise ValueError("full-day replay is already complete")
    minute_start_open = Counter(
        order.origin_zone_id
        for order in state.orders
        if order.status == OrderStatus.REQUESTED
    )
    prior_event_count = len(state.events)
    drivers = {driver.driver_id_hash: driver for driver in state.drivers}
    orders = {order.order_id: order for order in state.orders}
    interventions = {
        intervention.intervention_id: intervention
        for intervention in state.interventions
        if intervention.status not in {
            InterventionStatus.COMPLETED,
            InterventionStatus.CANCELLED,
        }
        or intervention.completed_minute is None
        or intervention.completed_minute >= minute - 15
    }
    events = list(state.events)
    economics, terminal = _apply_due_transitions(
        state, drivers, orders, interventions, events, minute
    )
    _apply_controls(state, controls, drivers, interventions, minute)
    _expire_delayed_interventions(drivers, interventions, minute)
    _apply_shift_boundaries(drivers, interventions, minute)
    terminal |= _expire_requests(state, orders, events, minute)
    for order_id in terminal:
        orders.pop(order_id, None)
    city_shock, zone_shocks = advance_shocks(
        scenario_version=state.scenario_version,
        seed=state.seed,
        minute_index=minute,
        zone_ids=tuple(zone.zone_id for zone in zones),
        city_shock=state.city_shock,
        zone_shocks=state.zone_shocks,
    )
    _generate_requests(
        state,
        zones,
        fixture,
        city_shock,
        zone_shocks,
        orders,
        events,
        minute,
    )
    _match_requests(state, zones, drivers, orders, events, minute)
    _update_driver_metrics(
        drivers,
        interventions,
        economics,
        weather_at(fixture, minute).heat_index_c,
    )
    next_state = replace(
        state,
        minute_index=minute + 1,
        drivers=tuple(sorted(drivers.values(), key=lambda item: item.driver_id_hash)),
        orders=tuple(sorted(orders.values(), key=lambda item: item.order_id)),
        interventions=tuple(
            sorted(interventions.values(), key=lambda item: item.intervention_id)
        ),
        events=tuple(sorted(events, key=lambda item: (item.event_minute, item.event_id))),
        city_shock=city_shock,
        zone_shocks=zone_shocks,
    )
    # Prove the complete structural, cohort, ownership, and queue-flow
    # invariants for this exact one-minute transition. The public tick later
    # repeats the same checks over the whole 15-minute aggregation boundary.
    minute_state = replace(
        next_state,
        events=next_state.events[prior_event_count:],
    )
    validate_state(
        minute_state,
        zones,
        tuple(sorted(minute_start_open.items())),
    )
    return next_state


def _tick_checksum(state: SimulationState, zones: tuple, scoring: tuple) -> str:
    digest = hashlib.sha256()

    def add(*values: object) -> None:
        for value in values:
            if isinstance(value, float):
                encoded = format(value, ".6f")
            elif value is None:
                encoded = "<null>"
            elif isinstance(value, StrEnum):
                encoded = value.value
            else:
                encoded = str(value)
            payload = encoded.encode("utf-8")
            digest.update(len(payload).to_bytes(4, "big"))
            digest.update(payload)

    add("run", state.run_id, state.minute_index)
    for driver in sorted(state.drivers, key=lambda item: item.driver_id_hash):
        add(
            "driver",
            driver.driver_id_hash,
            driver.zone_id,
            driver.latitude,
            driver.longitude,
            driver.status,
            driver.schedule_bits,
            driver.acclimatization_class,
            driver.continuous_exposure_minutes,
            driver.heat_dose_120m,
            driver.hydration_gap_minutes,
            driver.rest_minute_bits,
            driver.trips_minute_bits,
            ",".join(format(value, ".6f") for value in driver.distance_by_minute),
            ",".join(map(str, driver.earnings_by_minute)),
            ",".join(map(str, driver.contribution_by_minute)),
            driver.current_order_id,
            driver.current_intervention_id,
            driver.transition_due_minute,
            driver.pending_offline,
            driver.online_since_minute,
            driver.offline_since_minute,
        )
    for order in sorted(state.orders, key=lambda item: item.order_id):
        add(
            "order",
            order.order_id,
            order.origin_zone_id,
            order.destination_zone_id,
            order.requested_minute,
            order.status,
            order.driver_id_hash,
            order.accepted_minute,
            order.pickup_minute,
            order.completed_minute,
            order.cancelled_minute,
            order.distance_km,
            order.pickup_duration_minutes,
            order.trip_duration_minutes,
            order.fare_vnd,
            order.driver_pay_vnd,
            order.platform_contribution_vnd,
        )
    for intervention in sorted(
        state.interventions, key=lambda item: item.intervention_id
    ):
        add(
            "intervention",
            intervention.intervention_id,
            intervention.control_id,
            intervention.driver_id_hash,
            intervention.zone_id,
            intervention.assigned_minute,
            intervention.planned_duration_minutes,
            intervention.max_start_delay_minutes,
            intervention.status,
            intervention.started_minute,
            intervention.completed_minute,
            intervention.completed_rest_minutes,
            intervention.cancel_reason,
        )
    for event in sorted(
        state.events, key=lambda item: (item.event_minute, item.event_id)
    ):
        add(
            "event",
            event.event_id,
            event.order_id,
            event.event_type,
            event.event_minute,
            event.zone_id,
            event.driver_id_hash,
            event.prior_status,
        )
    for zone in zones:
        add("zone", *zone.__slots__, *(getattr(zone, name) for name in zone.__slots__))
    for projection in sorted(scoring, key=lambda item: item.driver_id_hash):
        add(
            "scoring",
            projection.driver_id_hash,
            projection.raw_features,
            projection.model_features,
            projection.clipped_fields,
            projection.ood_reasons,
        )
    return digest.hexdigest()


def advance_tick(
    state: SimulationState,
    *,
    fixture: ScenarioFixture | None = None,
    zones: tuple[ZonePrior, ...] | None = None,
    controls: tuple[PauseControl, ...] = (),
) -> TickResult:
    fixture = fixture or load_scenario(state.scenario_version)
    zones = zones or load_zone_priors()
    if state.minute_index % 15:
        raise ValueError("published ticks must begin on a 15-minute boundary")
    tick_index = state.minute_index // 15
    if tick_index >= 96:
        raise ValueError("full-day replay is already complete")
    simulation_time = state.start_time + timedelta(minutes=state.minute_index)
    working = replace(state, events=())
    for _ in range(15):
        working = advance_minute(
            working, fixture=fixture, zones=zones, controls=controls
        )
    # Per-driver numeric/ownership checks are enforced while each minute is
    # built; the complete cross-entity and projection invariant pass runs at
    # every public 15-minute boundary.
    start_open = Counter(
        order.origin_zone_id
        for order in state.orders
        if order.status == OrderStatus.REQUESTED
    )
    start_open_tuple = tuple(sorted(start_open.items()))
    validate_state(working, zones, start_open_tuple)
    weather = weather_at(fixture, tick_index * 15)
    zone_projection = project_zones(
        working.drivers,
        zones,
        working.events,
        working.orders,
        start_open_tuple,
    )
    scoring = tuple(
        project_scoring(driver, weather.heat_index_c, weather.humidity_percent)
        for driver in working.drivers
        if driver.status in ACTIVE_STATUSES
    )
    feature_clips: Counter[str] = Counter()
    for projection in scoring:
        feature_clips.update(projection.clipped_fields)
    total_cells = len(scoring) * len(MODEL_BOUNDS)
    clipped_cells = sum(feature_clips.values())
    behavior_rates = tuple(
        (
            name,
            0.0 if not scoring else round(feature_clips[name] / len(scoring), 6),
        )
        for name in sorted(set(MODEL_BOUNDS) - WEATHER_FEATURES)
    )
    weather_clips = sum(feature_clips[name] for name in WEATHER_FEATURES)
    weather_rate = (
        0.0 if not scoring else weather_clips / (len(scoring) * len(WEATHER_FEATURES))
    )
    combined_rate = 0.0 if total_cells == 0 else clipped_cells / total_cells
    model_ood = (
        combined_rate > 0.25
        or any(rate > 0.10 for _, rate in behavior_rates)
    )
    return TickResult(
        tick_index=tick_index,
        simulation_time=simulation_time,
        state=working,
        weather=weather,
        zones=zone_projection,
        scoring=scoring,
        checksum=_tick_checksum(working, zone_projection, scoring),
        clipped_feature_cells=clipped_cells,
        total_feature_cells=total_cells,
        behavior_clip_rates=behavior_rates,
        weather_clip_rate=round(weather_rate, 6),
        model_input_ood=model_ood,
    )


def run_full_day(
    *,
    seed: int = 42,
    controls: tuple[PauseControl, ...] = (),
    fixture: ScenarioFixture | None = None,
    zones: tuple[ZonePrior, ...] | None = None,
) -> tuple[TickResult, ...]:
    fixture = fixture or load_scenario(SCENARIO_VERSION)
    zones = zones or load_zone_priors()
    state = initialize_state(seed=seed, fixture=fixture, zones=zones)
    results = []
    for _ in range(96):
        result = advance_tick(
            state, fixture=fixture, zones=zones, controls=controls
        )
        results.append(result)
        state = result.state
    return tuple(results)


def hourly_summary(results: tuple[TickResult, ...]) -> tuple[dict[str, object], ...]:
    summary = []
    for result in results[3::4]:
        zones = result.zones
        feature_total = result.total_feature_cells
        raw_values: dict[str, list[float]] = defaultdict(list)
        for projection in result.scoring:
            for name, value in projection.raw_features:
                raw_values[name].append(value)
        raw_extrema = {
            name: {
                "min": round(min(values), 4),
                "max": round(max(values), 4),
            }
            for name, values in sorted(raw_values.items())
            if values
        }
        summary.append(
            {
                "time": result.simulation_time.isoformat(),
                "active": sum(zone.active_drivers for zone in zones),
                "online": sum(zone.online_drivers for zone in zones),
                "offline": sum(zone.offline_drivers for zone in zones),
                "requests": sum(
                    zone.requests_15m for tick in results[max(0, result.tick_index - 3):result.tick_index + 1]
                    for zone in tick.zones
                ),
                "matched": sum(
                    zone.matched_15m for tick in results[max(0, result.tick_index - 3):result.tick_index + 1]
                    for zone in tick.zones
                ),
                "completed": sum(
                    zone.completed_15m for tick in results[max(0, result.tick_index - 3):result.tick_index + 1]
                    for zone in tick.zones
                ),
                "cancelled": sum(
                    zone.cancelled_15m for tick in results[max(0, result.tick_index - 3):result.tick_index + 1]
                    for zone in tick.zones
                ),
                "unfulfilled": sum(
                    zone.unfulfilled_15m for tick in results[max(0, result.tick_index - 3):result.tick_index + 1]
                    for zone in tick.zones
                ),
                "exposed_2h": sum(zone.exposed_2h for zone in zones),
                "exposed_4h": sum(zone.exposed_4h for zone in zones),
                "paused": sum(zone.paused_drivers for zone in zones),
                "interventions": sum(
                    intervention.status
                    not in {
                        InterventionStatus.COMPLETED,
                        InterventionStatus.CANCELLED,
                    }
                    for intervention in result.state.interventions
                ),
                "raw_feature_extrema": raw_extrema,
                "clip_rate": round(
                    result.clipped_feature_cells / feature_total, 4
                )
                if feature_total
                else 0.0,
                "behavior_clip_rates": dict(result.behavior_clip_rates),
                "weather_clip_rate": result.weather_clip_rate,
                "model_input_ood": result.model_input_ood,
                "checksum": result.checksum,
            }
        )
    return tuple(summary)
