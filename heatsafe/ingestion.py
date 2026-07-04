from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import requests

from .config import Settings
from .datalake import CloudStorageDataLake
from .telemetry import log_event

ROOT = Path(__file__).resolve().parents[1]


def calculate_heat_index(temperature_c: float, humidity_percent: float) -> float:
    temperature_f = temperature_c * 9 / 5 + 32
    simple = 0.5 * (
        temperature_f + 61 + (temperature_f - 68) * 1.2 + humidity_percent * 0.094
    )
    if (simple + temperature_f) / 2 < 80:
        heat_index_f = simple
    else:
        heat_index_f = (
            -42.379 + 2.04901523 * temperature_f + 10.14333127 * humidity_percent
            - 0.22475541 * temperature_f * humidity_percent
            - 0.00683783 * temperature_f**2 - 0.05481717 * humidity_percent**2
            + 0.00122874 * temperature_f**2 * humidity_percent
            + 0.00085282 * temperature_f * humidity_percent**2
            - 0.00000199 * temperature_f**2 * humidity_percent**2
        )
        if humidity_percent < 13 and 80 <= temperature_f <= 112:
            heat_index_f -= ((13 - humidity_percent) / 4) * math.sqrt(
                max(0, (17 - abs(temperature_f - 95)) / 17)
            )
        elif humidity_percent > 85 and 80 <= temperature_f <= 87:
            heat_index_f += ((humidity_percent - 85) / 10) * ((87 - temperature_f) / 5)
    return round((heat_index_f - 32) * 5 / 9, 1)


def fetch_weather_payload(latitude: float, longitude: float) -> dict:
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m",
            "timezone": "UTC",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


class WeatherIngestionService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.data_lake = CloudStorageDataLake(self.settings)

    def run(self) -> list[dict]:
        from google.cloud import bigquery

        snapshot = json.loads((ROOT / "data" / "demo_snapshot.json").read_text(encoding="utf-8"))
        ingested_at = datetime.now(UTC)
        rows: list[dict] = []
        for zone in snapshot["zones"]:
            payload = fetch_weather_payload(zone["latitude"], zone["longitude"])
            current = payload["current"]
            observed_at = datetime.fromisoformat(current["time"]).replace(tzinfo=UTC)
            raw_uri = self.data_lake.upload_json(
                "weather",
                {"zone_id": zone["zone_id"], "provider_payload": payload},
                observed_at=observed_at,
                object_name=f"{zone['zone_id']}-{ingested_at:%H%M%S%f}.json",
            )
            temperature = float(current["temperature_2m"])
            humidity = float(current["relative_humidity_2m"])
            rows.append(
                {
                    "zone_id": zone["zone_id"], "name": zone["name"],
                    "latitude": zone["latitude"], "longitude": zone["longitude"],
                    "temperature_c": temperature, "humidity_percent": humidity,
                    "heat_index_c": calculate_heat_index(temperature, humidity),
                    "observed_at": observed_at.isoformat(), "ingested_at": ingested_at.isoformat(),
                    "source": "Open-Meteo", "raw_gcs_uri": raw_uri, "is_simulated": False,
                }
            )

        client = bigquery.Client(project=self.settings.project_id)
        errors = client.insert_rows_json(
            f"{self.settings.dataset_path}.weather_observations",
            rows,
            row_ids=[f"{row['zone_id']}:{row['observed_at']}" for row in rows],
        )
        if errors:
            raise RuntimeError(f"BigQuery weather insert failed: {errors}")
        log_event(
            "weather_ingestion_completed",
            row_count=len(rows),
            source="Open-Meteo",
            dataset=self.settings.dataset_path,
            bucket=self.settings.raw_bucket,
        )
        return rows
