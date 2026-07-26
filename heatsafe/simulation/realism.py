"""Deterministic operational-realism gates for the synthetic heatwave day."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import TickResult
from .scenario import SCENARIO_ROOT


@dataclass(frozen=True)
class RealismAudit:
    profile_id: str
    passed: bool
    checks: tuple[tuple[str, bool, str], ...]
    hourly: tuple[dict[str, float | int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "passed": self.passed,
            "checks": [
                {"name": name, "passed": passed, "detail": detail}
                for name, passed, detail in self.checks
            ],
            "hourly": list(self.hourly),
        }


def load_realism_profile(scenario_version: str) -> dict[str, Any]:
    path = SCENARIO_ROOT / scenario_version / "realism_profile.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load realism profile {scenario_version!r}: {exc}") from exc
    required = {"schema_version", "profile_id", "classification", "shift", "dayparts", "acceptance"}
    missing = required - profile.keys()
    if missing or profile["classification"] != "synthetic-prior":
        raise ValueError("realism profile is incomplete or lacks synthetic-prior provenance")
    return profile


def _hourly(results: tuple[TickResult, ...]) -> tuple[dict[str, float | int], ...]:
    if len(results) != 96:
        raise ValueError("realism audit requires exactly 96 ticks")
    rows = []
    for hour in range(24):
        group = results[hour * 4:(hour + 1) * 4]
        latest = group[-1]
        active = sum(zone.active_drivers for zone in latest.zones)
        exposed_4h = sum(zone.exposed_4h for zone in latest.zones)
        rows.append(
            {
                "hour": hour,
                "requests": sum(
                    sum(zone.requests_15m for zone in tick.zones) for tick in group
                ),
                "active": active,
                "exposed_2h": sum(zone.exposed_2h for zone in latest.zones),
                "exposed_4h": exposed_4h,
                "exposed_4h_share": 0.0 if active == 0 else exposed_4h / active,
                "heat_index_c": latest.weather.heat_index_c,
                "shortwave_radiation_wm2": latest.weather.shortwave_radiation_wm2,
            }
        )
    return tuple(rows)


def _evaluate_hourly(
    hourly: tuple[dict[str, float | int], ...], *, scenario_version: str
) -> RealismAudit:
    profile = load_realism_profile(scenario_version)
    acceptance = profile["acceptance"]

    def mean(name: str, start: int, end: int) -> float:
        values = [float(row[name]) for row in hourly[start:end]]
        return sum(values) / len(values)

    night_demand = mean("requests", 0, 5)
    morning_demand = mean("requests", 6, 10)
    lunch_demand = mean("requests", 11, 14)
    evening_demand = mean("requests", 18, 21)
    lunch_peak = max(float(row["requests"]) for row in hourly[11:14])
    evening_peak = max(float(row["requests"]) for row in hourly[18:21])
    night_supply = mean("active", 0, 5)
    evening_supply = mean("active", 18, 21)
    saturation = max(float(row["exposed_4h_share"]) for row in hourly)
    checks = (
        (
            "night_demand_trough",
            night_demand <= evening_demand * float(acceptance["night_demand_max_fraction_of_evening"]),
            f"night={night_demand:.1f}, evening={evening_demand:.1f}",
        ),
        (
            "morning_commute_lift",
            morning_demand >= night_demand * float(acceptance["morning_demand_min_multiple_of_night"]),
            f"morning={morning_demand:.1f}, night={night_demand:.1f}",
        ),
        (
            "lunch_lift",
            lunch_demand >= night_demand * float(acceptance["lunch_demand_min_multiple_of_night"]),
            f"lunch={lunch_demand:.1f}, night={night_demand:.1f}",
        ),
        (
            "evening_dinner_peak",
            evening_peak >= lunch_peak * float(acceptance["evening_demand_min_multiple_of_lunch"]),
            f"evening_peak={evening_peak:.1f}, lunch_peak={lunch_peak:.1f}",
        ),
        (
            "night_supply_trough",
            night_supply <= evening_supply * float(acceptance["night_supply_max_fraction_of_evening"]),
            f"night={night_supply:.1f}, evening={evening_supply:.1f}",
        ),
        (
            "no_roster_wide_mandatory_saturation",
            saturation < float(acceptance["max_exposed_4h_share"]),
            f"max_4h_share={saturation:.3f}",
        ),
    )
    return RealismAudit(
        profile_id=str(profile["profile_id"]),
        passed=all(passed for _, passed, _ in checks),
        checks=checks,
        hourly=hourly,
    )


def audit_full_day(
    results: tuple[TickResult, ...], *, scenario_version: str = "hanoi_heatwave_v1"
) -> RealismAudit:
    """Audit retained tick results; primarily useful for bounded unit tests."""
    return _evaluate_hourly(_hourly(results), scenario_version=scenario_version)


def run_realism_audit(
    *,
    seed: int,
    scenario_version: str = "hanoi_heatwave_v1",
    fixture: Any | None = None,
    zones: tuple[Any, ...] | None = None,
) -> RealismAudit:
    """Run a full day with O(1) retained tick state.

    A full ten-zone tick retains thousands of immutable driver objects.  The
    certification runner therefore aggregates each completed hour immediately
    instead of retaining 96 complete state snapshots merely to make charts.
    """
    from .engine import advance_tick, initialize_state, load_zone_priors
    from .scenario import load_scenario

    fixture = fixture or load_scenario(scenario_version)
    zones = zones or load_zone_priors()
    state = initialize_state(seed=seed, fixture=fixture, zones=zones)
    hourly: list[dict[str, float | int]] = []
    hourly_requests = 0
    for index in range(96):
        result = advance_tick(state, fixture=fixture, zones=zones)
        state = result.state
        hourly_requests += sum(zone.requests_15m for zone in result.zones)
        if index % 4 != 3:
            continue
        active = sum(zone.active_drivers for zone in result.zones)
        exposed_4h = sum(zone.exposed_4h for zone in result.zones)
        hourly.append(
            {
                "hour": index // 4,
                "requests": hourly_requests,
                "active": active,
                "exposed_2h": sum(zone.exposed_2h for zone in result.zones),
                "exposed_4h": exposed_4h,
                "exposed_4h_share": 0.0 if active == 0 else exposed_4h / active,
                "heat_index_c": result.weather.heat_index_c,
                "shortwave_radiation_wm2": result.weather.shortwave_radiation_wm2,
            }
        )
        hourly_requests = 0
    return _evaluate_hourly(tuple(hourly), scenario_version=scenario_version)
