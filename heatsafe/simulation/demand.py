from __future__ import annotations

import math
from datetime import datetime

from heatsafe.demand_profile import intraday_demand_factor

from .models import WeatherState, ZonePrior
from .randomness import DeterministicRandom, stable_int


SUPPLY_POINTS = (
    (0, 0.25),
    (4 * 60, 0.20),
    (6 * 60, 0.50),
    (8 * 60, 1.10),
    (10 * 60, 0.90),
    (13 * 60, 1.00),
    (15 * 60, 0.85),
    (18 * 60, 1.20),
    (20 * 60, 0.95),
    (22 * 60, 0.50),
    (24 * 60, 0.25),
)


def supply_multiplier(minute_of_day: int) -> float:
    minute = max(0, min(24 * 60, minute_of_day))
    for (left_minute, left), (right_minute, right) in zip(
        SUPPLY_POINTS, SUPPLY_POINTS[1:]
    ):
        if left_minute <= minute <= right_minute:
            fraction = (minute - left_minute) / (right_minute - left_minute)
            return left + (right - left) * fraction
    return SUPPLY_POINTS[-1][1]


def target_active(zone: ZonePrior, minute_of_day: int) -> int:
    return round(zone.active_anchor * supply_multiplier(minute_of_day))


def weather_demand_factor(weather: WeatherState) -> float:
    heat = 1 + 0.06 * min(1.0, max(0.0, (weather.heat_index_c - 35) / 18))
    rain = 1 + 0.15 * min(1.0, max(0.0, weather.precipitation_mm / 5))
    return heat * rain


def demand_mean_15m(
    zone: ZonePrior,
    event_time: datetime,
    weather: WeatherState,
    anchor_weather: WeatherState,
    city_shock: float,
    zone_shock: float,
) -> float:
    zone_seed = stable_int(zone.zone_id, bits=64)
    intraday = intraday_demand_factor(event_time, zone_seed)
    anchor_time = event_time.replace(hour=13, minute=0, second=0, microsecond=0)
    anchor = intraday_demand_factor(anchor_time, zone_seed)
    return max(
        0.0,
        zone.forecast_requests_30m
        / 2
        * intraday
        / anchor
        * weather_demand_factor(weather)
        / weather_demand_factor(anchor_weather)
        * city_shock
        * zone_shock,
    )


def advance_shocks(
    *,
    scenario_version: str,
    seed: int,
    minute_index: int,
    zone_ids: tuple[str, ...],
    city_shock: float,
    zone_shocks: tuple[tuple[str, float], ...],
) -> tuple[float, tuple[tuple[str, float], ...]]:
    current = dict(zone_shocks)
    city_noise = DeterministicRandom(
        scenario_version, seed, minute_index, "city-shock"
    ).normal()
    next_city = min(1.20, max(0.85, 1 + 0.85 * (city_shock - 1) + 0.04 * city_noise))
    next_zones = []
    for zone_id in sorted(zone_ids):
        noise = DeterministicRandom(
            scenario_version, seed, minute_index, zone_id, "zone-shock"
        ).normal()
        prior = current.get(zone_id, 1.0)
        value = min(1.10, max(0.90, 1 + 0.65 * (prior - 1) + 0.03 * noise))
        next_zones.append((zone_id, value))
    return next_city, tuple(next_zones)


def sample_requests(
    *,
    scenario_version: str,
    seed: int,
    minute_index: int,
    zone: ZonePrior,
    expected_15m: float,
) -> int:
    if not math.isfinite(expected_15m) or expected_15m < 0:
        raise ValueError("expected demand must be finite and non-negative")
    stream = DeterministicRandom(
        scenario_version,
        seed,
        minute_index,
        zone.zone_id,
        "request-count",
    )
    return stream.negative_binomial(expected_15m / 15, dispersion=40.0)
