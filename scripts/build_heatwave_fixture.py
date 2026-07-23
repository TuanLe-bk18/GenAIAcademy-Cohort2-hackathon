#!/usr/bin/env python3
"""Rebuild the reviewed Láng/ERA5 fixture; runtime never calls this script."""

from __future__ import annotations

import csv
import hashlib
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from heatsafe.simulation.scenario import (
    DERIVATION_VERSION,
    SOURCE_CANONICAL_SHA256,
    WEATHER_COLUMNS,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "scenarios" / "hanoi_heatwave_v1" / "weather_15m.csv"
URL = "https://archive-api.open-meteo.com/v1/archive"
PARAMS = {
    "latitude": 21.02,
    "longitude": 105.80,
    "start_date": "2026-05-25",
    "end_date": "2026-05-27",
    "hourly": (
        "temperature_2m,relative_humidity_2m,apparent_temperature,"
        "precipitation,cloud_cover,wind_speed_10m,wind_gusts_10m,"
        "shortwave_radiation"
    ),
    "wind_speed_unit": "ms",
    "timezone": "Asia/Ho_Chi_Minh",
    "models": "era5",
}
START_INDEX = 24


def canonical_checksum(raw_payload: bytes) -> str:
    """Match the reviewed `jq -cS 'del(.generationtime_ms)'` byte contract."""
    try:
        result = subprocess.run(
            ["jq", "-cS", "del(.generationtime_ms)"],
            input=raw_payload,
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("fixture rebuild requires jq") from exc
    return hashlib.sha256(result.stdout).hexdigest()


def lerp(left: float, right: float, fraction: float) -> float:
    return left + (right - left) * fraction


def calibrated_temperature(source_temperature_c: float) -> float:
    return 29.8 + (source_temperature_c - 29.8) * (41.1 - 29.8) / (39.5 - 29.8)


def rounded(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def build(payload: dict) -> list[dict[str, str | int]]:
    hourly = payload["hourly"]
    rows: list[dict[str, str | int]] = []
    continuous = {
        "source_temperature_c": "temperature_2m",
        "relative_humidity_percent": "relative_humidity_2m",
        "apparent_temperature_c": "apparent_temperature",
        "cloud_cover_pct": "cloud_cover",
        "wind_speed_mps": "wind_speed_10m",
        "wind_gust_mps": "wind_gusts_10m",
        "shortwave_radiation_wm2": "shortwave_radiation",
    }
    for tick_index in range(96):
        hour_offset, quarter = divmod(tick_index, 4)
        source_index = START_INDEX + hour_offset
        fraction = quarter / 4
        values = {
            output: lerp(
                float(hourly[source][source_index]),
                float(hourly[source][source_index + 1]),
                fraction,
            )
            for output, source in continuous.items()
        }
        source_temperature = values["source_temperature_c"]
        temperature = calibrated_temperature(source_temperature)
        hanoi_timezone = timezone(timedelta(hours=7))
        local_time = (
            datetime.fromisoformat(hourly["time"][START_INDEX]).replace(
                tzinfo=hanoi_timezone
            )
            + timedelta(minutes=tick_index * 15)
        )
        source_time = datetime.fromisoformat(
            hourly["time"][source_index]
        ).replace(tzinfo=hanoi_timezone)
        source_next_time = datetime.fromisoformat(
            hourly["time"][source_index + 1]
        ).replace(tzinfo=hanoi_timezone)
        row: dict[str, str | int] = {
            "simulation_offset_minutes": tick_index * 15,
            "local_time": local_time.isoformat(),
            "source_observed_at": source_time.isoformat(),
            "source_next_observed_at": source_next_time.isoformat(),
            "source_interpolation_fraction": rounded(fraction),
            **{field: rounded(value) for field, value in values.items()},
            "temperature_adjustment_c": rounded(temperature - source_temperature),
            "temperature_c": rounded(temperature),
            "station_peak_anchor_c": "41.1",
            "precipitation_mm": rounded(
                float(hourly["precipitation"][source_index]) / 4
            ),
            "source_grid_latitude": rounded(float(payload["latitude"])),
            "source_grid_longitude": rounded(float(payload["longitude"])),
            "derivation_version": DERIVATION_VERSION,
        }
        rows.append(row)
    return rows


def main() -> None:
    response = requests.get(URL, params=PARAMS, timeout=30)
    response.raise_for_status()
    payload = response.json()
    checksum = canonical_checksum(response.content)
    if checksum != SOURCE_CANONICAL_SHA256:
        raise RuntimeError(
            f"source response changed: expected {SOURCE_CANONICAL_SHA256}, got {checksum}"
        )
    rows = build(payload)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=WEATHER_COLUMNS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    fixture_checksum = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"Wrote {len(rows)} rows to {OUTPUT}")
    print(f"fixture_sha256={fixture_checksum}")


if __name__ == "__main__":
    main()
