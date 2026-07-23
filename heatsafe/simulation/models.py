from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DriverStatus(StrEnum):
    OFFLINE = "OFFLINE"
    IDLE = "IDLE"
    TO_PICKUP = "TO_PICKUP"
    ON_TRIP = "ON_TRIP"
    TO_COOLSTOP = "TO_COOLSTOP"
    PAUSED = "PAUSED"


class OrderStatus(StrEnum):
    REQUESTED = "REQUESTED"
    MATCHED = "MATCHED"
    ON_TRIP = "ON_TRIP"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    UNFULFILLED = "UNFULFILLED"


class OrderEventType(StrEnum):
    REQUESTED = "REQUESTED"
    MATCHED = "MATCHED"
    PICKED_UP = "PICKED_UP"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    UNFULFILLED = "UNFULFILLED"


class InterventionStatus(StrEnum):
    ASSIGNED = "ASSIGNED"
    TO_COOLSTOP = "TO_COOLSTOP"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AcclimatizationClass(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


ACTIVE_STATUSES = frozenset(
    {DriverStatus.IDLE, DriverStatus.TO_PICKUP, DriverStatus.ON_TRIP}
)
ONLINE_STATUSES = frozenset(status for status in DriverStatus if status != DriverStatus.OFFLINE)
SCORING_STATUSES = ACTIVE_STATUSES
TERMINAL_ORDER_STATUSES = frozenset(
    {OrderStatus.COMPLETED, OrderStatus.CANCELLED, OrderStatus.UNFULFILLED}
)


@dataclass(frozen=True, slots=True)
class ZonePrior:
    zone_id: str
    name: str
    latitude: float
    longitude: float
    active_anchor: int
    exposed_2h_anchor: int
    exposed_4h_anchor: int
    forecast_requests_30m: int
    avg_platform_contribution_vnd: int
    avg_driver_earnings_vnd: int
    coolstop_name: str
    coolstop_latitude: float
    coolstop_longitude: float


@dataclass(frozen=True, slots=True)
class WeatherState:
    event_time: datetime
    temperature_c: float
    humidity_percent: float
    heat_index_c: float
    precipitation_mm: float
    cloud_cover_pct: float
    wind_speed_mps: float
    shortwave_radiation_wm2: float


@dataclass(frozen=True, slots=True)
class DriverState:
    driver_id_hash: str
    zone_id: str
    latitude: float
    longitude: float
    status: DriverStatus
    schedule_bits: int
    acclimatization_class: AcclimatizationClass
    continuous_exposure_minutes: int
    heat_dose_120m: float
    hydration_gap_minutes: int
    rest_minute_bits: int = 0
    trips_minute_bits: int = 0
    distance_by_minute: tuple[float, ...] = ()
    earnings_by_minute: tuple[int, ...] = ()
    contribution_by_minute: tuple[int, ...] = ()
    current_order_id: str | None = None
    current_intervention_id: str | None = None
    transition_due_minute: int | None = None
    pending_offline: bool = False
    online_since_minute: int | None = None
    offline_since_minute: int | None = None

    def scheduled_at(self, minute_index: int) -> bool:
        slot = min(95, max(0, minute_index // 15))
        return bool(self.schedule_bits & (1 << slot))

    @property
    def rest_minutes_120m(self) -> int:
        return (self.rest_minute_bits & ((1 << 120) - 1)).bit_count()

    @property
    def trips_60m(self) -> int:
        return (self.trips_minute_bits & ((1 << 60) - 1)).bit_count()

    @property
    def distance_km_60m(self) -> float:
        return round(sum(self.distance_by_minute[-60:]), 4)

    @property
    def earnings_60m_vnd(self) -> int:
        return sum(self.earnings_by_minute[-60:])

    @property
    def platform_contribution_60m_vnd(self) -> int:
        return sum(self.contribution_by_minute[-60:])


@dataclass(frozen=True, slots=True)
class OrderState:
    order_id: str
    origin_zone_id: str
    destination_zone_id: str
    requested_minute: int
    status: OrderStatus
    driver_id_hash: str | None = None
    accepted_minute: int | None = None
    pickup_minute: int | None = None
    completed_minute: int | None = None
    cancelled_minute: int | None = None
    distance_km: float = 0.0
    pickup_duration_minutes: int = 0
    trip_duration_minutes: int = 0
    fare_vnd: int = 0
    driver_pay_vnd: int = 0
    platform_contribution_vnd: int = 0


@dataclass(frozen=True, slots=True)
class OrderEvent:
    event_id: str
    order_id: str
    event_type: OrderEventType
    event_minute: int
    zone_id: str
    driver_id_hash: str | None
    prior_status: OrderStatus | None = None


@dataclass(frozen=True, slots=True)
class PauseControl:
    control_id: str
    driver_ids: tuple[str, ...]
    requested_minute: int
    pause_duration_minutes: int
    max_start_delay_minutes: int = 45


@dataclass(frozen=True, slots=True)
class InterventionState:
    intervention_id: str
    control_id: str
    driver_id_hash: str
    zone_id: str
    assigned_minute: int
    planned_duration_minutes: int
    max_start_delay_minutes: int
    status: InterventionStatus = InterventionStatus.ASSIGNED
    started_minute: int | None = None
    completed_minute: int | None = None
    completed_rest_minutes: int = 0
    cancel_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ScoringProjection:
    driver_id_hash: str
    raw_features: tuple[tuple[str, float], ...]
    model_features: tuple[tuple[str, float], ...]
    clipped_fields: tuple[str, ...]
    ood_reasons: tuple[str, ...]

    @property
    def ood_count(self) -> int:
        return len(self.clipped_fields)


@dataclass(frozen=True, slots=True)
class ZoneProjection:
    zone_id: str
    active_drivers: int
    online_drivers: int
    offline_drivers: int
    idle_drivers: int
    to_pickup_drivers: int
    on_trip_drivers: int
    to_coolstop_drivers: int
    paused_drivers: int
    fresh_drivers: int
    exposed_2h: int
    exposed_4h: int
    exposed_2_to_4h: int
    requests_15m: int
    matched_15m: int
    completed_15m: int
    cancelled_15m: int
    unfulfilled_15m: int
    open_unmatched_start: int
    open_unmatched_end: int
    request_flow_balance: int
    fulfillment_rate: float


@dataclass(frozen=True, slots=True)
class SimulationState:
    scenario_version: str
    generator_version: str
    run_id: str
    seed: int
    start_time: datetime
    minute_index: int
    drivers: tuple[DriverState, ...]
    orders: tuple[OrderState, ...]
    interventions: tuple[InterventionState, ...]
    events: tuple[OrderEvent, ...]
    city_shock: float
    zone_shocks: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class TickResult:
    tick_index: int
    simulation_time: datetime
    state: SimulationState
    weather: WeatherState
    zones: tuple[ZoneProjection, ...]
    scoring: tuple[ScoringProjection, ...]
    checksum: str
    clipped_feature_cells: int
    total_feature_cells: int
    behavior_clip_rates: tuple[tuple[str, float], ...]
    weather_clip_rate: float
    model_input_ood: bool
