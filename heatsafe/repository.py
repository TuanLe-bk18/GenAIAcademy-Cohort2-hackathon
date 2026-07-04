from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Settings
from .models import ZoneSnapshot

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "data" / "demo_snapshot.json"


@dataclass(frozen=True)
class SnapshotResult:
    zones: list[ZoneSnapshot]
    mode: str
    source_label: str
    fallback_reason: str | None = None


@dataclass(frozen=True)
class DemandForecast:
    zone_id: str
    horizon_minutes: int
    predicted_requests: int
    lower_bound: int
    upper_bound: int
    source: str

    def to_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "horizon_minutes": self.horizon_minutes,
            "predicted_requests": self.predicted_requests,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "source": self.source,
        }


def _parse_zone(raw: dict, *, observed_at: datetime, source: str, simulated: bool) -> ZoneSnapshot:
    return ZoneSnapshot(
        zone_id=raw["zone_id"],
        name=raw["name"],
        latitude=float(raw["latitude"]),
        longitude=float(raw["longitude"]),
        temperature_c=float(raw["temperature_c"]),
        humidity_percent=float(raw["humidity_percent"]),
        heat_index_c=float(raw["heat_index_c"]),
        observed_at=observed_at,
        active_drivers=int(raw["active_drivers"]),
        fresh_drivers=int(raw["fresh_drivers"]),
        exposed_2h=int(raw["exposed_2h"]),
        exposed_4h=int(raw["exposed_4h"]),
        forecast_requests_30m=int(raw["forecast_requests_30m"]),
        avg_platform_contribution_vnd=int(raw["avg_platform_contribution_vnd"]),
        avg_driver_earnings_vnd=int(raw["avg_driver_earnings_vnd"]),
        coolstop_name=raw["coolstop_name"],
        coolstop_latitude=float(raw["coolstop_latitude"]),
        coolstop_longitude=float(raw["coolstop_longitude"]),
        source=source,
        is_simulated=simulated,
    )


class SnapshotRepository:
    def __init__(self, path: Path = DEFAULT_SNAPSHOT):
        self.path = path

    def load(self) -> SnapshotResult:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        observed_at = datetime.fromisoformat(raw["scenario_time"]).astimezone(UTC)
        zones = [
            _parse_zone(item, observed_at=observed_at, source="Hackathon demo snapshot", simulated=True)
            for item in raw["zones"]
        ]
        return SnapshotResult(zones, "snapshot", raw["scenario_name"])

    def forecast_demand(self, zone_id: str, horizon_minutes: int = 60) -> DemandForecast:
        zone = next(zone for zone in self.load().zones if zone.zone_id == zone_id)
        predicted = round(zone.forecast_requests_30m * horizon_minutes / 30)
        return DemandForecast(
            zone_id=zone_id,
            horizon_minutes=horizon_minutes,
            predicted_requests=predicted,
            lower_bound=round(predicted * 0.9),
            upper_bound=round(predicted * 1.1),
            source="Demo snapshot heuristic",
        )

    def forecast_demand_many(
        self, zone_ids: list[str], horizon_minutes: int = 60
    ) -> dict[str, DemandForecast]:
        return {
            zone_id: self.forecast_demand(zone_id, horizon_minutes)
            for zone_id in zone_ids
        }


class BigQueryRepository:
    """Read the production-shaped zone_snapshots view; never falls back silently."""

    def __init__(
        self,
        project_id: str | None = None,
        dataset_id: str | None = None,
        scenario: str | None = None,
    ):
        self.settings = Settings.from_env()
        self.project_id = project_id or self.settings.project_id
        self.dataset_id = dataset_id or self.settings.dataset_id
        self.dataset = f"{self.project_id}.{self.dataset_id}"
        self.scenario = (scenario or self.settings.scenario).lower()
        if self.scenario not in {"live", "heatwave"}:
            raise ValueError("HEATSAFE_SCENARIO must be live or heatwave")
        view_name = "zone_snapshots_live" if self.scenario == "live" else "zone_snapshots_heatwave"
        self.table = f"{self.dataset}.{view_name}"

    def load(self) -> SnapshotResult:
        from google.cloud import bigquery

        client = bigquery.Client(project=self.project_id)
        query = f"""
            SELECT * FROM `{self.table}`
            WHERE observed_at = (SELECT MAX(observed_at) FROM `{self.table}`)
        """
        rows = [dict(row) for row in client.query(query).result()]
        if not rows:
            raise RuntimeError("zone_snapshots is empty")
        observed_at = rows[0]["observed_at"].astimezone(UTC)
        max_age_hours = self.settings.max_data_age_hours
        if datetime.now(UTC) - observed_at > timedelta(hours=max_age_hours):
            raise RuntimeError(f"cloud snapshot is older than {max_age_hours} hours")
        zones = [
            _parse_zone(
                row,
                observed_at=observed_at,
                source=str(row.get("source", "BigQuery")),
                simulated=bool(row.get("is_simulated", False)),
            )
            for row in rows
        ]
        return SnapshotResult(zones, "cloud", f"BigQuery · {self.table}")

    def forecast_demand(self, zone_id: str, horizon_minutes: int = 60) -> DemandForecast:
        from google.cloud import bigquery

        horizon_minutes = max(15, min(240, horizon_minutes))
        horizon_intervals = max(1, round(horizon_minutes / 15))
        client = bigquery.Client(project=self.project_id)
        query = f"""
            SELECT
              CAST(ROUND(SUM(forecast_value)) AS INT64) AS predicted_requests,
              CAST(ROUND(SUM(prediction_interval_lower_bound)) AS INT64) AS lower_bound,
              CAST(ROUND(SUM(prediction_interval_upper_bound)) AS INT64) AS upper_bound
            FROM AI.FORECAST(
              (SELECT interval_start, requests
               FROM `{self.dataset}.demand_history`
               WHERE zone_id = @zone_id),
              data_col => 'requests',
              timestamp_col => 'interval_start',
              horizon => {horizon_intervals},
              confidence_level => 0.9
            )
        """
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("zone_id", "STRING", zone_id)],
            maximum_bytes_billed=50_000_000,
        )
        row = next(iter(client.query(query, job_config=config).result()), None)
        if not row:
            raise RuntimeError(f"No demand forecast for {zone_id}")
        return DemandForecast(
            zone_id=zone_id,
            horizon_minutes=horizon_minutes,
            predicted_requests=int(row.predicted_requests),
            lower_bound=int(row.lower_bound),
            upper_bound=int(row.upper_bound),
            source="BigQuery ML · TimesFM AI.FORECAST",
        )

    def forecast_demand_many(
        self, zone_ids: list[str], horizon_minutes: int = 60
    ) -> dict[str, DemandForecast]:
        from google.cloud import bigquery

        if not zone_ids:
            return {}
        horizon_minutes = max(15, min(240, horizon_minutes))
        horizon_intervals = max(1, round(horizon_minutes / 15))
        query = f"""
            SELECT
              zone_id,
              CAST(ROUND(SUM(forecast_value)) AS INT64) AS predicted_requests,
              CAST(ROUND(SUM(prediction_interval_lower_bound)) AS INT64) AS lower_bound,
              CAST(ROUND(SUM(prediction_interval_upper_bound)) AS INT64) AS upper_bound
            FROM AI.FORECAST(
              (SELECT zone_id, interval_start, requests
               FROM `{self.dataset}.demand_history`
               WHERE zone_id IN UNNEST(@zone_ids)),
              data_col => 'requests',
              timestamp_col => 'interval_start',
              id_cols => ['zone_id'],
              horizon => {horizon_intervals},
              confidence_level => 0.9
            )
            GROUP BY zone_id
        """
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("zone_ids", "STRING", zone_ids)
            ],
            maximum_bytes_billed=100_000_000,
        )
        rows = self._query(query, config)
        return {
            row.zone_id: DemandForecast(
                zone_id=row.zone_id,
                horizon_minutes=horizon_minutes,
                predicted_requests=int(row.predicted_requests),
                lower_bound=int(row.lower_bound),
                upper_bound=int(row.upper_bound),
                source="BigQuery ML · TimesFM AI.FORECAST",
            )
            for row in rows
        }

    def _query(self, query: str, config):
        from google.cloud import bigquery

        return list(bigquery.Client(project=self.project_id).query(query, job_config=config).result())


class HybridRepository:
    def __init__(self, mode: str | None = None, scenario: str | None = None):
        self.settings = Settings.from_env()
        self.mode = (mode or self.settings.mode).lower()
        if self.mode not in {"auto", "cloud", "snapshot"}:
            raise ValueError("HEATSAFE_MODE must be auto, cloud, or snapshot")
        self.snapshot = SnapshotRepository()
        self.scenario = (scenario or self.settings.scenario).lower()
        self.cloud = BigQueryRepository(
            self.settings.project_id, self.settings.dataset_id, self.scenario
        )
        self._active = self.snapshot if self.mode == "snapshot" else self.cloud

    def load(self) -> SnapshotResult:
        if self.mode == "snapshot":
            self._active = self.snapshot
            return self.snapshot.load()

        try:
            result = self.cloud.load()
            self._active = self.cloud
            return result
        except Exception as exc:
            if self.mode == "cloud":
                raise
            fallback = self.snapshot.load()
            self._active = self.snapshot
            return SnapshotResult(
                zones=fallback.zones,
                mode="snapshot",
                source_label=fallback.source_label,
                fallback_reason=str(exc),
            )

    def forecast_demand(self, zone_id: str, horizon_minutes: int = 60) -> DemandForecast:
        try:
            return self._active.forecast_demand(zone_id, horizon_minutes)
        except Exception:
            if self.mode == "cloud":
                raise
            return self.snapshot.forecast_demand(zone_id, horizon_minutes)

    def forecast_demand_many(
        self, zone_ids: list[str], horizon_minutes: int = 60
    ) -> dict[str, DemandForecast]:
        try:
            return self._active.forecast_demand_many(zone_ids, horizon_minutes)
        except Exception:
            if self.mode == "cloud":
                raise
            return self.snapshot.forecast_demand_many(zone_ids, horizon_minutes)
