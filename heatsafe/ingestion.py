from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, datetime
from pathlib import Path

import requests

from .bigquery_io import merge_rows
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
    """Build one coherent live snapshot from real weather and simulated operations."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.data_lake = CloudStorageDataLake(self.settings)
        self._client_instance = None

    def _client(self):
        if self._client_instance is None:
            from google.cloud import bigquery

            self._client_instance = bigquery.Client(project=self.settings.project_id)
        return self._client_instance

    def run(self) -> list[dict]:
        from infra.provision_gcp import table_schemas

        snapshot = json.loads((ROOT / "data" / "demo_snapshot.json").read_text(encoding="utf-8"))
        refreshed_at = datetime.now(UTC)
        snapshot_id = f"live-{refreshed_at:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        weather_rows: list[dict] = []
        operation_rows: list[dict] = []
        current_rows: list[dict] = []
        for zone in snapshot["zones"]:
            payload = fetch_weather_payload(zone["latitude"], zone["longitude"])
            current = payload["current"]
            observed_at = datetime.fromisoformat(current["time"]).replace(tzinfo=UTC)
            raw_uri = self.data_lake.upload_json(
                "weather",
                {"zone_id": zone["zone_id"], "snapshot_id": snapshot_id, "provider_payload": payload},
                observed_at=observed_at,
                object_name=f"{zone['zone_id']}-{refreshed_at:%H%M%S%f}.json",
            )
            temperature = float(current["temperature_2m"])
            humidity = float(current["relative_humidity_2m"])
            weather = {
                "scenario_id": "live", "snapshot_id": snapshot_id,
                "zone_id": zone["zone_id"], "name": zone["name"],
                "latitude": zone["latitude"], "longitude": zone["longitude"],
                "temperature_c": temperature, "humidity_percent": humidity,
                "heat_index_c": calculate_heat_index(temperature, humidity),
                "observed_at": observed_at.isoformat(), "ingested_at": refreshed_at.isoformat(),
                "source": "Open-Meteo", "raw_gcs_uri": raw_uri, "is_simulated": False,
            }
            operations = {
                "scenario_id": "live", "snapshot_id": snapshot_id,
                "zone_id": zone["zone_id"], "observed_at": refreshed_at.isoformat(),
                "active_drivers": zone["active_drivers"], "fresh_drivers": zone["fresh_drivers"],
                "exposed_2h": zone["exposed_2h"], "exposed_4h": zone["exposed_4h"],
                "forecast_requests_30m": zone["forecast_requests_30m"],
                "avg_platform_contribution_vnd": zone["avg_platform_contribution_vnd"],
                "avg_driver_earnings_vnd": zone["avg_driver_earnings_vnd"],
                "is_simulated": True,
            }
            weather_rows.append(weather)
            operation_rows.append(operations)
            current_rows.append(
                {
                    "scenario_id": "live", "snapshot_id": snapshot_id,
                    "zone_id": zone["zone_id"], "name": zone["name"],
                    "latitude": zone["latitude"], "longitude": zone["longitude"],
                    "temperature_c": temperature, "humidity_percent": humidity,
                    "heat_index_c": weather["heat_index_c"],
                    "observed_at": min(observed_at, refreshed_at).isoformat(),
                    "weather_observed_at": observed_at.isoformat(),
                    "operations_observed_at": refreshed_at.isoformat(),
                    "refreshed_at": refreshed_at.isoformat(),
                    "active_drivers": zone["active_drivers"], "fresh_drivers": zone["fresh_drivers"],
                    "exposed_2h": zone["exposed_2h"], "exposed_4h": zone["exposed_4h"],
                    "forecast_requests_30m": zone["forecast_requests_30m"],
                    "avg_platform_contribution_vnd": zone["avg_platform_contribution_vnd"],
                    "avg_driver_earnings_vnd": zone["avg_driver_earnings_vnd"],
                    "coolstop_name": zone["coolstop_name"],
                    "coolstop_latitude": zone["coolstop_latitude"],
                    "coolstop_longitude": zone["coolstop_longitude"],
                    "source": "Open-Meteo + simulated fleet operations",
                    "weather_is_simulated": False, "operations_is_simulated": True,
                }
            )

        schemas = table_schemas()
        dataset = self.settings.dataset_path
        client = self._client()
        merge_rows(
            client, f"{dataset}.weather_observations", weather_rows,
            schemas["weather_observations"], ["scenario_id", "snapshot_id", "zone_id"],
            target_predicate=(
                "target.observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)"
            ),
        )
        merge_rows(
            client, f"{dataset}.zone_operations", operation_rows,
            schemas["zone_operations"], ["scenario_id", "snapshot_id", "zone_id"],
            target_predicate=(
                "target.observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)"
            ),
        )
        merge_rows(
            client, f"{dataset}.{self.settings.current_snapshot_table}", current_rows,
            schemas["zone_snapshots_current"], ["scenario_id", "zone_id"],
        )
        log_event(
            "live_snapshot_ingestion_completed",
            row_count=len(current_rows), snapshot_id=snapshot_id,
            dataset=dataset, bucket=self.settings.raw_bucket,
            weather_simulated=False, operations_simulated=True,
        )
        return current_rows
