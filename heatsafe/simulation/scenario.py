from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


SCENARIO_ROOT = Path(__file__).resolve().parents[2] / "data" / "scenarios"
SCENARIO_CATALOG = frozenset({"hanoi_heatwave_v1"})
DERIVATION_VERSION = "lang-max-anchor-era5-linear-15m-v2"
SOURCE_CANONICAL_SHA256 = (
    "13e289c702b3d4213986d237d54eeb3225fbd8b4c493e8b169c16af371c859ac"
)
WEATHER_COLUMNS = (
    "simulation_offset_minutes",
    "local_time",
    "source_observed_at",
    "source_next_observed_at",
    "source_interpolation_fraction",
    "source_temperature_c",
    "temperature_adjustment_c",
    "temperature_c",
    "station_peak_anchor_c",
    "relative_humidity_percent",
    "apparent_temperature_c",
    "precipitation_mm",
    "cloud_cover_pct",
    "wind_speed_mps",
    "wind_gust_mps",
    "shortwave_radiation_wm2",
    "source_grid_latitude",
    "source_grid_longitude",
    "derivation_version",
)
_VERSION_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


class ScenarioValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ScenarioFixture:
    manifest: dict[str, Any]
    weather: tuple[dict[str, Any], ...]
    realism_profile: dict[str, Any]
    directory: Path


def _fail(message: str) -> None:
    raise ScenarioValidationError(message)


def _required_path(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for component in path.split("."):
        if not isinstance(current, dict) or component not in current:
            _fail(f"manifest is missing required key {path!r}")
        current = current[component]
    return current


def _parse_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ScenarioValidationError(f"{field} is not ISO-8601: {value!r}") from exc
    if parsed.utcoffset() != timedelta(hours=7):
        _fail(f"{field} must carry Hanoi's +07:00 offset")
    return parsed


def _finite_float(row: dict[str, str], field: str, index: int) -> float:
    try:
        value = float(row[field])
    except (KeyError, ValueError) as exc:
        raise ScenarioValidationError(
            f"weather row {index} has invalid {field}"
        ) from exc
    if not math.isfinite(value):
        _fail(f"weather row {index} has non-finite {field}")
    return value


def _validate_manifest(manifest: dict[str, Any], scenario_version: str) -> None:
    required = (
        "schema_version", "scenario_id", "scenario_version", "display_name",
        "description", "timezone", "simulation_start_local", "tick_minutes",
        "expected_ticks", "source.provider", "source.dataset", "source.api_url",
        "source.requested_coordinate", "source.resolved_grid",
        "source.retrieved_at", "source.canonical_sha256", "source.license",
        "source.attribution", "source.modifications",
        "calibration_anchor.station_name", "calibration_anchor.wmo_id",
        "calibration_anchor.coordinate",
        "calibration_anchor.observed_daily_max_c",
        "calibration_anchor.observation_date",
        "calibration_anchor.source_urls",
        "calibration_anchor.era5_daily_min_c",
        "calibration_anchor.era5_daily_max_c",
        "calibration_anchor.method", "calibration_anchor.limitations",
        "derivation.version", "derivation.method_by_field",
        "derivation.output_columns", "validation.expected_first_time",
        "validation.expected_last_time", "validation.expected_rows",
        "validation.ranges", "zone_weather_offsets",
        "zone_weather_offset_method.type", "zone_weather_offset_method.source",
        "zone_weather_offset_method.derivation",
        "zone_weather_offset_method.limitations", "operational_priors",
        "disclaimer",
    )
    for path in required:
        _required_path(manifest, path)
    expected = {
        "scenario_id": "heatwave",
        "scenario_version": scenario_version,
        "timezone": "Asia/Ho_Chi_Minh",
        "simulation_start_local": "2026-05-26T00:00:00+07:00",
        "tick_minutes": 15,
        "expected_ticks": 96,
    }
    for key, value in expected.items():
        if manifest[key] != value:
            _fail(f"manifest {key} must be {value!r}; got {manifest[key]!r}")
    if _required_path(manifest, "derivation.version") != DERIVATION_VERSION:
        _fail("manifest derivation version is not supported")
    if _required_path(manifest, "source.canonical_sha256") != SOURCE_CANONICAL_SHA256:
        _fail("manifest source checksum does not match the reviewed ERA5 response")
    if tuple(_required_path(manifest, "derivation.output_columns")) != WEATHER_COLUMNS:
        _fail("manifest output columns differ from the Phase 1 contract")
    if _required_path(manifest, "zone_weather_offsets") != {
        "hoan-kiem": 0.9,
        "hai-ba-trung": 0.5,
        "dong-da": 0.4,
        "ba-dinh": 0.3,
        "cau-giay": 0.0,
        "thanh-xuan": -0.1,
        "hoang-mai": -0.2,
        "nam-tu-liem": -0.4,
        "ha-dong": -0.6,
        "bac-tu-liem": -0.8,
    }:
        _fail("scenario zone weather offsets differ from the reviewed profile")
    if (
        _required_path(manifest, "zone_weather_offset_method.type")
        != "synthetic_stable_temperature_offset_c"
        or _required_path(manifest, "zone_weather_offset_method.source")
        != "data/demo_snapshot.json"
    ):
        _fail("scenario zone weather offset provenance is unsupported")


def _validate_weather(
    manifest: dict[str, Any],
    rows: list[dict[str, str]],
) -> tuple[dict[str, Any], ...]:
    expected_rows = int(_required_path(manifest, "validation.expected_rows"))
    if len(rows) != expected_rows or len(rows) != 96:
        _fail(f"weather fixture must contain exactly 96 rows; got {len(rows)}")
    if not rows or tuple(rows[0].keys()) != WEATHER_COLUMNS:
        _fail("weather fixture columns differ from the Phase 1 contract")

    converted: list[dict[str, Any]] = []
    prior_time: datetime | None = None
    temperatures: list[float] = []
    source_temperatures: list[float] = []
    adjustments: list[float] = []
    precipitation_total = 0.0
    allowed_fractions = {0.0, 0.25, 0.5, 0.75}
    ranges = _required_path(manifest, "validation.ranges")
    numeric_ranges = {
        "source_temperature_c": ranges["source_temperature_c"],
        "temperature_adjustment_c": ranges["temperature_adjustment_c"],
        "temperature_c": ranges["temperature_c"],
        "relative_humidity_percent": ranges["relative_humidity_percent"],
        "apparent_temperature_c": ranges["apparent_temperature_c"],
        "precipitation_mm": ranges["precipitation_mm"],
        "cloud_cover_pct": ranges["cloud_cover_pct"],
        "wind_speed_mps": ranges["wind_speed_mps"],
        "wind_gust_mps": ranges["wind_gust_mps"],
        "shortwave_radiation_wm2": ranges["shortwave_radiation_wm2"],
    }
    for index, row in enumerate(rows):
        if row.get("simulation_offset_minutes") != str(index * 15):
            _fail(f"weather row {index} has an invalid simulation offset")
        offset = index * 15
        local_time = _parse_time(row["local_time"], f"weather row {index} local_time")
        source_time = _parse_time(
            row["source_observed_at"], f"weather row {index} source_observed_at"
        )
        source_next_time = _parse_time(
            row["source_next_observed_at"],
            f"weather row {index} source_next_observed_at",
        )
        if prior_time is not None and local_time - prior_time != timedelta(minutes=15):
            _fail(f"weather row {index} breaks the 15-minute time sequence")
        if source_next_time - source_time != timedelta(hours=1):
            _fail(f"weather row {index} source interval is not one hour")
        prior_time = local_time
        fraction = _finite_float(row, "source_interpolation_fraction", index)
        if fraction not in allowed_fractions:
            _fail(f"weather row {index} has unsupported interpolation fraction")
        expected_source_time = local_time.replace(minute=0)
        if source_time != expected_source_time:
            _fail(f"weather row {index} source timestamp is not the containing hour")
        if not math.isclose(fraction, local_time.minute / 60, abs_tol=1e-9):
            _fail(f"weather row {index} interpolation fraction and timestamp differ")

        numeric: dict[str, float] = {}
        for field, bounds in numeric_ranges.items():
            value = _finite_float(row, field, index)
            if not float(bounds[0]) <= value <= float(bounds[1]):
                _fail(f"weather row {index} {field} is outside {bounds}")
            numeric[field] = value
        if _finite_float(row, "station_peak_anchor_c", index) != 41.1:
            _fail(f"weather row {index} changed the station peak anchor")
        if numeric["temperature_c"] + 1e-9 < numeric["source_temperature_c"]:
            _fail(f"weather row {index} calibrated temperature is below ERA5")
        expected_temperature = 29.8 + (
            numeric["source_temperature_c"] - 29.8
        ) * (41.1 - 29.8) / (39.5 - 29.8)
        if not math.isclose(
            numeric["temperature_c"], expected_temperature, abs_tol=0.0001
        ):
            _fail(f"weather row {index} violates the reviewed calibration formula")
        if not math.isclose(
            numeric["temperature_adjustment_c"],
            numeric["temperature_c"] - numeric["source_temperature_c"],
            abs_tol=0.0001,
        ):
            _fail(f"weather row {index} has inconsistent temperature adjustment")
        if numeric["wind_gust_mps"] + 1e-9 < numeric["wind_speed_mps"]:
            _fail(f"weather row {index} wind gust is below wind speed")
        if row["derivation_version"] != DERIVATION_VERSION:
            _fail(f"weather row {index} has an unexpected derivation version")
        temperatures.append(numeric["temperature_c"])
        source_temperatures.append(numeric["source_temperature_c"])
        adjustments.append(numeric["temperature_adjustment_c"])
        precipitation_total += numeric["precipitation_mm"]
        converted.append(
            {
                **row,
                "simulation_offset_minutes": offset,
                "local_time": local_time,
                "source_observed_at": source_time,
                "source_next_observed_at": source_next_time,
                **numeric,
                "source_interpolation_fraction": fraction,
                "station_peak_anchor_c": 41.1,
                "source_grid_latitude": _finite_float(
                    row, "source_grid_latitude", index
                ),
                "source_grid_longitude": _finite_float(
                    row, "source_grid_longitude", index
                ),
            }
        )

    if rows[0]["local_time"] != _required_path(
        manifest, "validation.expected_first_time"
    ):
        _fail("weather fixture first timestamp differs from the manifest")
    if rows[-1]["local_time"] != _required_path(
        manifest, "validation.expected_last_time"
    ):
        _fail("weather fixture last timestamp differs from the manifest")
    peak = max(temperatures)
    peak_index = temperatures.index(peak)
    if not math.isclose(peak, 41.1, abs_tol=0.05) or peak_index != 64:
        _fail("calibrated temperature must peak at 41.1 C at 16:00")
    if not math.isclose(min(source_temperatures), 29.8, abs_tol=0.01):
        _fail("fixture no longer preserves the reviewed ERA5 daily minimum")
    if not math.isclose(max(source_temperatures), 39.5, abs_tol=0.01):
        _fail("fixture no longer preserves the reviewed ERA5 daily maximum")
    if not math.isclose(max(adjustments), 1.6, abs_tol=0.01):
        _fail("fixture maximum temperature adjustment must be 1.6 C")
    if not math.isclose(precipitation_total, 0.0, abs_tol=1e-9):
        _fail("reviewed scenario day must preserve zero daily precipitation")
    extrema = {
        "relative_humidity_percent": (44.0, 74.0),
        "apparent_temperature_c": (35.1, 45.4),
        "cloud_cover_pct": (0.0, 6.0),
        "wind_speed_mps": (1.96, 3.53),
        "wind_gust_mps": (4.1, 7.6),
        "shortwave_radiation_wm2": (0.0, 968.0),
    }
    for field, (expected_min, expected_max) in extrema.items():
        values = [float(row[field]) for row in converted]
        if not (
            math.isclose(min(values), expected_min, abs_tol=0.01)
            and math.isclose(max(values), expected_max, abs_tol=0.01)
        ):
            _fail(f"fixture no longer preserves the reviewed {field} envelope")
    if not all(
        math.isclose(row["source_grid_latitude"], 21.0, abs_tol=1e-9)
        and math.isclose(row["source_grid_longitude"], 105.75, abs_tol=1e-9)
        for row in converted
    ):
        _fail("fixture source grid differs from the reviewed ERA5 cell")
    return tuple(converted)


def _load_realism_profile(directory: Path) -> dict[str, Any]:
    try:
        profile = json.loads(
            (directory / "realism_profile.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioValidationError(
            f"cannot load scenario realism profile: {exc}"
        ) from exc
    required = {
        "schema_version", "profile_id", "classification", "scope", "shift",
        "dayparts", "acceptance",
    }
    if not required <= profile.keys():
        _fail("realism profile is missing required fields")
    if profile["classification"] != "synthetic-prior":
        _fail("realism profile must label operational assumptions as synthetic-prior")
    shift = profile["shift"]
    expected_shift = {
        "initial_carryover_max_minutes": 180,
        "standard_continuous_shift_minutes": 210,
        "extended_continuous_shift_minutes": 300,
        "minimum_recovery_minutes": 15,
    }
    if shift != expected_shift:
        _fail("realism profile shift assumptions differ from the reviewed engine")
    return profile


def load_scenario(
    scenario_version: str,
    *,
    root: Path | None = None,
) -> ScenarioFixture:
    if not _VERSION_RE.fullmatch(scenario_version):
        _fail(f"invalid scenario version {scenario_version!r}")
    if scenario_version not in SCENARIO_CATALOG:
        _fail(f"unknown scenario version {scenario_version!r}")
    directory = (root or SCENARIO_ROOT) / scenario_version
    try:
        manifest = json.loads(
            (directory / "manifest.json").read_text(encoding="utf-8")
        )
        with (directory / "weather_15m.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioValidationError(
            f"cannot load scenario fixture {scenario_version!r}: {exc}"
        ) from exc
    _validate_manifest(manifest, scenario_version)
    return ScenarioFixture(
        manifest=manifest,
        weather=_validate_weather(manifest, rows),
        realism_profile=_load_realism_profile(directory),
        directory=directory,
    )
