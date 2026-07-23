from __future__ import annotations

import math
from collections import Counter

from .models import (
    ACTIVE_STATUSES,
    ONLINE_STATUSES,
    SCORING_STATUSES,
    DriverState,
    DriverStatus,
    InterventionState,
    InterventionStatus,
    OrderEvent,
    OrderEventType,
    OrderState,
    OrderStatus,
    ScoringProjection,
    SimulationState,
    ZonePrior,
    ZoneProjection,
)
from .randomness import stable_int


VALID_DRIVER_TRANSITIONS = {
    DriverStatus.OFFLINE: frozenset({DriverStatus.OFFLINE, DriverStatus.IDLE}),
    DriverStatus.IDLE: frozenset(
        {
            DriverStatus.IDLE,
            DriverStatus.TO_PICKUP,
            DriverStatus.TO_COOLSTOP,
            DriverStatus.OFFLINE,
        }
    ),
    DriverStatus.TO_PICKUP: frozenset(
        {DriverStatus.TO_PICKUP, DriverStatus.ON_TRIP, DriverStatus.IDLE}
    ),
    DriverStatus.ON_TRIP: frozenset({DriverStatus.ON_TRIP, DriverStatus.IDLE}),
    DriverStatus.TO_COOLSTOP: frozenset(
        {DriverStatus.TO_COOLSTOP, DriverStatus.PAUSED, DriverStatus.IDLE}
    ),
    DriverStatus.PAUSED: frozenset(
        {DriverStatus.PAUSED, DriverStatus.IDLE, DriverStatus.OFFLINE}
    ),
}

VALID_ORDER_TRANSITIONS = {
    OrderStatus.REQUESTED: frozenset(
        {
            OrderStatus.REQUESTED,
            OrderStatus.MATCHED,
            OrderStatus.CANCELLED,
            OrderStatus.UNFULFILLED,
        }
    ),
    OrderStatus.MATCHED: frozenset(
        {OrderStatus.MATCHED, OrderStatus.ON_TRIP, OrderStatus.CANCELLED}
    ),
    OrderStatus.ON_TRIP: frozenset({OrderStatus.ON_TRIP, OrderStatus.COMPLETED}),
    OrderStatus.COMPLETED: frozenset({OrderStatus.COMPLETED}),
    OrderStatus.CANCELLED: frozenset({OrderStatus.CANCELLED}),
    OrderStatus.UNFULFILLED: frozenset({OrderStatus.UNFULFILLED}),
}

MODEL_BOUNDS = {
    "heat_index_c": (33.05, 50.55),
    "humidity_percent": (46.0, 68.0),
    "continuous_exposure_minutes": (30.0, 360.0),
    "trips_60m": (1.0, 5.0),
    "distance_km_60m": (3.0, 20.9),
    "rest_minutes_120m": (0.0, 45.0),
    "hydration_gap_minutes": (15.0, 180.0),
    "route_heat_load": (0.60, 3.09),
    "workload_intensity": (0.50, 2.69),
}
WEATHER_FEATURES = frozenset({"heat_index_c", "humidity_percent"})


class SimulationInvariantError(ValueError):
    pass


def require_driver_transition(before: DriverStatus, after: DriverStatus) -> None:
    if after not in VALID_DRIVER_TRANSITIONS[before]:
        raise SimulationInvariantError(f"invalid driver transition {before} -> {after}")


def require_order_transition(before: OrderStatus, after: OrderStatus) -> None:
    if after not in VALID_ORDER_TRANSITIONS[before]:
        raise SimulationInvariantError(f"invalid order transition {before} -> {after}")


def route_heat_load(driver: DriverState) -> float:
    status_factor = {
        DriverStatus.OFFLINE: 0.0,
        DriverStatus.IDLE: 0.90,
        DriverStatus.TO_PICKUP: 1.10,
        DriverStatus.ON_TRIP: 1.25,
        DriverStatus.TO_COOLSTOP: 1.00,
        DriverStatus.PAUSED: 0.20,
    }[driver.status]
    base = 0.8 + (stable_int(driver.driver_id_hash, "route-load") % 601) / 1000
    return round(base * status_factor, 4)


def workload_intensity(driver: DriverState) -> float:
    status_load = {
        DriverStatus.OFFLINE: 0.0,
        DriverStatus.IDLE: 0.35,
        DriverStatus.TO_PICKUP: 0.65,
        DriverStatus.ON_TRIP: 0.85,
        DriverStatus.TO_COOLSTOP: 0.25,
        DriverStatus.PAUSED: 0.05,
    }[driver.status]
    value = status_load + 0.28 * driver.trips_60m + 0.045 * driver.distance_km_60m
    return round(min(3.5, max(0.0, value)), 4)


def project_scoring(driver: DriverState, heat_index_c: float, humidity: float) -> ScoringProjection:
    if driver.status not in SCORING_STATUSES:
        raise ValueError("driver is not scoring-eligible")
    raw = {
        "heat_index_c": float(heat_index_c),
        "humidity_percent": float(humidity),
        "continuous_exposure_minutes": float(driver.continuous_exposure_minutes),
        "trips_60m": float(driver.trips_60m),
        "distance_km_60m": float(driver.distance_km_60m),
        "rest_minutes_120m": float(driver.rest_minutes_120m),
        "hydration_gap_minutes": float(driver.hydration_gap_minutes),
        "route_heat_load": route_heat_load(driver),
        "workload_intensity": workload_intensity(driver),
    }
    for name, value in raw.items():
        if not math.isfinite(value) or value < 0:
            raise SimulationInvariantError(f"invalid raw scoring feature {name}={value}")
    model = {}
    clipped = []
    reasons = []
    for name, value in raw.items():
        low, high = MODEL_BOUNDS[name]
        projected = min(high, max(low, value))
        model[name] = projected
        if projected != value:
            clipped.append(name)
            reasons.append(f"{name}:{'LOW' if value < low else 'HIGH'}")
    return ScoringProjection(
        driver_id_hash=driver.driver_id_hash,
        raw_features=tuple(sorted(raw.items())),
        model_features=tuple(sorted(model.items())),
        clipped_fields=tuple(sorted(clipped)),
        ood_reasons=tuple(sorted(reasons)),
    )


def _event_counts(events: tuple[OrderEvent, ...], zone_id: str) -> Counter:
    return Counter(
        event.event_type
        for event in events
        if event.zone_id == zone_id
    )


def project_zones(
    drivers: tuple[DriverState, ...],
    zones: tuple[ZonePrior, ...],
    events: tuple[OrderEvent, ...],
    orders: tuple[OrderState, ...] = (),
    start_open_unmatched: tuple[tuple[str, int], ...] = (),
) -> tuple[ZoneProjection, ...]:
    by_zone: dict[str, list[DriverState]] = {zone.zone_id: [] for zone in zones}
    for driver in drivers:
        by_zone[driver.zone_id].append(driver)
    start_open = dict(start_open_unmatched)
    end_open = Counter(
        order.origin_zone_id
        for order in orders
        if order.status == OrderStatus.REQUESTED
    )
    projections = []
    for zone in sorted(zones, key=lambda item: item.zone_id):
        current = by_zone[zone.zone_id]
        statuses = Counter(driver.status for driver in current)
        active = sum(statuses[status] for status in ACTIVE_STATUSES)
        online = sum(statuses[status] for status in ONLINE_STATUSES)
        exposed_2h = sum(
            driver.status in ACTIVE_STATUSES
            and driver.continuous_exposure_minutes >= 120
            for driver in current
        )
        exposed_4h = sum(
            driver.status in ACTIVE_STATUSES
            and driver.continuous_exposure_minutes >= 240
            for driver in current
        )
        counts = _event_counts(events, zone.zone_id)
        requested = counts[OrderEventType.REQUESTED]
        matched = counts[OrderEventType.MATCHED]
        cancelled = counts[OrderEventType.CANCELLED]
        pre_match_cancelled = sum(
            event.event_type == OrderEventType.CANCELLED
            and event.prior_status == OrderStatus.REQUESTED
            for event in events
            if event.zone_id == zone.zone_id
        )
        unfulfilled = counts[OrderEventType.UNFULFILLED]
        denominator = matched + cancelled + unfulfilled
        fulfillment = 1.0 if denominator == 0 else matched / denominator
        projections.append(
            ZoneProjection(
                zone_id=zone.zone_id,
                active_drivers=active,
                online_drivers=online,
                offline_drivers=statuses[DriverStatus.OFFLINE],
                idle_drivers=statuses[DriverStatus.IDLE],
                to_pickup_drivers=statuses[DriverStatus.TO_PICKUP],
                on_trip_drivers=statuses[DriverStatus.ON_TRIP],
                to_coolstop_drivers=statuses[DriverStatus.TO_COOLSTOP],
                paused_drivers=statuses[DriverStatus.PAUSED],
                fresh_drivers=active - exposed_2h,
                exposed_2h=exposed_2h,
                exposed_4h=exposed_4h,
                exposed_2_to_4h=exposed_2h - exposed_4h,
                requests_15m=requested,
                matched_15m=matched,
                completed_15m=counts[OrderEventType.COMPLETED],
                cancelled_15m=cancelled,
                unfulfilled_15m=unfulfilled,
                open_unmatched_start=start_open.get(zone.zone_id, 0),
                open_unmatched_end=end_open[zone.zone_id],
                request_flow_balance=(
                    requested
                    + start_open.get(zone.zone_id, 0)
                    - matched
                    - pre_match_cancelled
                    - unfulfilled
                    - end_open[zone.zone_id]
                ),
                fulfillment_rate=round(fulfillment, 4),
            )
        )
    return tuple(projections)


def validate_state(
    state: SimulationState,
    zones: tuple[ZonePrior, ...],
    start_open_unmatched: tuple[tuple[str, int], ...] = (),
) -> None:
    driver_ids = [driver.driver_id_hash for driver in state.drivers]
    if len(driver_ids) != len(set(driver_ids)):
        raise SimulationInvariantError("duplicate driver ID")
    order_ids = [order.order_id for order in state.orders]
    if len(order_ids) != len(set(order_ids)):
        raise SimulationInvariantError("duplicate order ID")
    intervention_ids = [item.intervention_id for item in state.interventions]
    if len(intervention_ids) != len(set(intervention_ids)):
        raise SimulationInvariantError("duplicate intervention ID")

    driver_index = {driver.driver_id_hash: driver for driver in state.drivers}
    order_index = {order.order_id: order for order in state.orders}
    intervention_index = {
        intervention.intervention_id: intervention
        for intervention in state.interventions
    }
    for driver in state.drivers:
        numeric = (
            driver.continuous_exposure_minutes,
            driver.heat_dose_120m,
            driver.hydration_gap_minutes,
            driver.distance_km_60m,
            driver.earnings_60m_vnd,
            driver.platform_contribution_60m_vnd,
        )
        if any(not math.isfinite(float(value)) or value < 0 for value in numeric):
            raise SimulationInvariantError(
                f"driver {driver.driver_id_hash} has invalid numeric state"
            )
        owns_order = driver.current_order_id is not None
        if driver.status in {DriverStatus.TO_PICKUP, DriverStatus.ON_TRIP}:
            if not owns_order or driver.current_order_id not in order_index:
                raise SimulationInvariantError("working driver has no current order")
        elif owns_order:
            raise SimulationInvariantError("non-working driver owns an order")
        if driver.current_intervention_id is not None:
            if driver.current_intervention_id not in intervention_index:
                raise SimulationInvariantError("driver references missing intervention")
            if owns_order and driver.status in {
                DriverStatus.TO_COOLSTOP,
                DriverStatus.PAUSED,
            }:
                raise SimulationInvariantError("order and pause overlap")

    assigned_drivers = []
    for order in state.orders:
        if order.status in {OrderStatus.MATCHED, OrderStatus.ON_TRIP}:
            if order.driver_id_hash not in driver_index:
                raise SimulationInvariantError("assigned order has no driver")
            assigned_drivers.append(order.driver_id_hash)
        elif order.driver_id_hash is not None:
            raise SimulationInvariantError("unmatched order has a driver")
        if order.pickup_minute is not None and order.accepted_minute is not None:
            if order.pickup_minute < order.accepted_minute:
                raise SimulationInvariantError("pickup precedes acceptance")
        if order.completed_minute is not None:
            if order.pickup_minute is None or order.completed_minute < order.pickup_minute:
                raise SimulationInvariantError("completion precedes pickup")
    if len(assigned_drivers) != len(set(assigned_drivers)):
        raise SimulationInvariantError("driver owns multiple orders")

    for intervention in state.interventions:
        driver = driver_index.get(intervention.driver_id_hash)
        if driver is None:
            raise SimulationInvariantError("intervention has no driver")
        if intervention.completed_rest_minutes < 0:
            raise SimulationInvariantError("negative intervention rest")
        if intervention.status in {
            InterventionStatus.ASSIGNED,
            InterventionStatus.TO_COOLSTOP,
            InterventionStatus.PAUSED,
        } and driver.current_intervention_id != intervention.intervention_id:
            raise SimulationInvariantError("active intervention ownership mismatch")

    projections = project_zones(
        state.drivers,
        zones,
        state.events,
        state.orders,
        start_open_unmatched,
    )
    for zone in projections:
        if zone.fresh_drivers + zone.exposed_2h != zone.active_drivers:
            raise SimulationInvariantError("fresh/exposed partition mismatch")
        if not 0 <= zone.exposed_4h <= zone.exposed_2h:
            raise SimulationInvariantError("exposure cohorts are not nested")
        if zone.exposed_2_to_4h != zone.exposed_2h - zone.exposed_4h:
            raise SimulationInvariantError("exclusive exposure cohort mismatch")
        if zone.active_drivers > zone.online_drivers:
            raise SimulationInvariantError("active supply exceeds online supply")
        if zone.request_flow_balance != 0:
            raise SimulationInvariantError(
                f"request queue does not reconcile for {zone.zone_id}: "
                f"{zone.request_flow_balance}"
            )
