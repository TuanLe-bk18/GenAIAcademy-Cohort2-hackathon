from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .models import ZoneSnapshot

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "data" / "demo_snapshot.json"
FORECAST_CONTEXT_DAYS = 21
FORECAST_CONTEXT_POINTS = 2_048
MINIMUM_QUERY_BYTES_BILLED = 10 * 1024 * 1024
MAX_FORECAST_MINUTES = 24 * 60
HANOI_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass(frozen=True)
class SnapshotResult:
    zones: list[ZoneSnapshot]
    mode: str
    source_label: str
    fallback_reason: str | None = None
    data_fresh: bool = True
    freshness_warning: str | None = None


@dataclass(frozen=True)
class ForecastPoint:
    forecast_at: datetime
    predicted_requests: int
    lower_bound: int
    upper_bound: int

    def to_dict(self) -> dict:
        return {
            "forecast_at": self.forecast_at.isoformat(),
            "predicted_requests": self.predicted_requests,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
        }


@dataclass(frozen=True)
class DemandForecast:
    zone_id: str
    horizon_minutes: int
    predicted_requests: int
    source: str
    status: str
    points: tuple[ForecastPoint, ...]

    def to_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "horizon_minutes": self.horizon_minutes,
            "predicted_requests": self.predicted_requests,
            "source": self.source,
            "status": self.status,
            "points": [point.to_dict() for point in self.points],
        }


class ForecastUnavailable(RuntimeError):
    pass


def _parse_datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.astimezone(UTC)


def _parse_zone(raw: dict, *, source: str) -> ZoneSnapshot:
    observed_at = _parse_datetime(raw["observed_at"])
    weather_at = _parse_datetime(raw.get("weather_observed_at", observed_at))
    operations_at = _parse_datetime(raw.get("operations_observed_at", observed_at))
    simulated = bool(raw.get("is_simulated", True))
    return ZoneSnapshot(
        zone_id=raw["zone_id"],
        name=raw["name"],
        latitude=float(raw["latitude"]),
        longitude=float(raw["longitude"]),
        temperature_c=float(raw["temperature_c"]),
        humidity_percent=float(raw["humidity_percent"]),
        heat_index_c=float(raw["heat_index_c"]),
        observed_at=observed_at,
        scenario_id=str(raw.get("scenario_id", "heatwave")),
        snapshot_id=str(raw.get("snapshot_id", "local-demo")),
        weather_observed_at=weather_at,
        operations_observed_at=operations_at,
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
        weather_is_simulated=bool(raw.get("weather_is_simulated", simulated)),
        operations_is_simulated=bool(raw.get("operations_is_simulated", simulated)),
    )


class SnapshotRepository:
    def __init__(self, path: Path = DEFAULT_SNAPSHOT):
        self.path = path

    def load(self) -> SnapshotResult:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        observed_at = datetime.fromisoformat(raw["scenario_time"]).astimezone(UTC)
        zones = []
        for item in raw["zones"]:
            enriched = {
                **item,
                "observed_at": observed_at,
                "weather_observed_at": observed_at,
                "operations_observed_at": observed_at,
                "scenario_id": "heatwave",
                "snapshot_id": "local-heatwave-replay",
                "weather_is_simulated": True,
                "operations_is_simulated": True,
            }
            zones.append(_parse_zone(enriched, source="Hackathon demo snapshot"))
        return SnapshotResult(zones, "snapshot", raw["scenario_name"])

    @staticmethod
    def _intraday_demand_factor(forecast_at: datetime, zone_seed: int) -> float:
        """Return a smooth, zone-specific Hanoi ride-demand profile."""
        local = forecast_at.astimezone(HANOI_TZ)
        hour = local.hour + local.minute / 60
        weekend = local.weekday() >= 5
        phase = math.radians(zone_seed % 360)

        def peak(center: float, width: float, amplitude: float) -> float:
            return amplitude * math.exp(-0.5 * ((hour - center) / width) ** 2)

        # Typical urban demand: commuter peaks on weekdays, a lunch lift, and
        # the strongest peak in the evening. Weekends start later and stay busy
        # later at night.
        morning_center = (9.0 if weekend else 8.0) + ((zone_seed % 7) - 3) * 0.08
        evening_center = (19.0 if weekend else 18.25) + ((zone_seed % 5) - 2) * 0.1
        factor = 0.30
        factor += peak(morning_center, 1.35, 0.44 if weekend else 0.66)
        factor += peak(12.25, 1.65, 0.38)
        factor += peak(evening_center, 1.75, 0.88 if weekend else 0.78)
        factor += peak(22.0, 1.45, 0.22 if weekend else 0.12)

        # Two low-amplitude waves add local variation without producing the
        # jagged, independently-random points of the previous demo heuristic.
        variation = (
            1.0
            + 0.045 * math.sin(2 * math.pi * hour / 1.75 + phase)
            + 0.025 * math.sin(2 * math.pi * hour / 0.65 + phase / 2)
        )
        return max(0.2, factor * variation)

    def forecast_demand(self, zone_id: str, horizon_minutes: int = 60) -> DemandForecast:
        zone = next(zone for zone in self.load().zones if zone.zone_id == zone_id)
        horizon_minutes = max(15, min(MAX_FORECAST_MINUTES, horizon_minutes))
        intervals = max(1, round(horizon_minutes / 15))
        per_interval = zone.forecast_requests_30m / 2
        observed_at = zone.operations_observed_at.astimezone(UTC)
        start = observed_at.replace(
            minute=(observed_at.minute // 15) * 15,
            second=0,
            microsecond=0,
        )
        zone_seed = sum((index + 1) * ord(char) for index, char in enumerate(zone_id))
        forecast_times = [
            start + timedelta(minutes=15 * (index + 1)) for index in range(intervals)
        ]
        factors = [self._intraday_demand_factor(at, zone_seed) for at in forecast_times]
        anchor_count = min(2, intervals)
        anchor_factor = sum(factors[:anchor_count]) / anchor_count
        values = [max(0, round(per_interval * factor / anchor_factor)) for factor in factors]

        # Preserve the snapshot's 30-minute forecast exactly; this is the value
        # consumed by SafePause, while later points provide display context.
        if intervals >= 2:
            first_value = round(
                zone.forecast_requests_30m * factors[0] / (factors[0] + factors[1])
            )
            values[0] = max(0, min(zone.forecast_requests_30m, first_value))
            values[1] = zone.forecast_requests_30m - values[0]

        points: list[ForecastPoint] = []
        for index in range(intervals):
            val = values[index]
            hours_ahead = (index + 1) / 4
            uncertainty = 0.12 + min(0.14, hours_ahead / 24 * 0.14)
            points.append(
                ForecastPoint(
                    forecast_at=forecast_times[index],
                    predicted_requests=val,
                    lower_bound=max(0, round(val * (1 - uncertainty))),
                    upper_bound=round(val * (1 + uncertainty)),
                )
            )

        return DemandForecast(
            zone_id=zone_id,
            horizon_minutes=horizon_minutes,
            predicted_requests=sum(point.predicted_requests for point in points),
            source="Demo snapshot · calibrated intraday heuristic",
            status="HEURISTIC",
            points=tuple(points),
        )

    def forecast_demand_many(
        self, zone_ids: list[str], horizon_minutes: int = 60
    ) -> dict[str, DemandForecast]:
        return {
            zone_id: self.forecast_demand(zone_id, horizon_minutes)
            for zone_id in zone_ids
        }


class BigQueryRepository:
    """Read scenario-safe current snapshots and bounded TimesFM forecasts."""

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
        self.table = f"{self.dataset}.{self.settings.current_snapshot_table}"
        self._client_instance = None

    def _client(self):
        if self._client_instance is None:
            from google.cloud import bigquery

            self._client_instance = bigquery.Client(project=self.project_id)
        return self._client_instance

    @staticmethod
    def _job_config(parameters: list, maximum_bytes_billed: int):
        from google.cloud import bigquery

        return bigquery.QueryJobConfig(
            query_parameters=parameters,
            maximum_bytes_billed=maximum_bytes_billed,
            labels={"app": "heatsafe", "component": "demo"},
        )

    def load(self) -> SnapshotResult:
        from google.cloud import bigquery

        query = f"""
            SELECT * FROM `{self.table}`
            WHERE scenario_id = @scenario_id
            ORDER BY zone_id
        """
        config = self._job_config(
            [bigquery.ScalarQueryParameter("scenario_id", "STRING", self.scenario)],
            MINIMUM_QUERY_BYTES_BILLED,
        )
        rows = [dict(row) for row in self._client().query(query, job_config=config).result()]
        if not rows:
            raise RuntimeError(f"No current snapshot for scenario {self.scenario}")
        snapshot_ids = {row["snapshot_id"] for row in rows}
        if len(snapshot_ids) != 1:
            raise RuntimeError("Current snapshot is incomplete: mixed snapshot_id values")
        zones = [_parse_zone(row, source=str(row.get("source", "BigQuery"))) for row in rows]
        data_fresh = True
        freshness_warning = None
        if self.scenario == "live":
            oldest_component = min(
                min(zone.weather_observed_at, zone.operations_observed_at) for zone in zones
            )
            freshness = timedelta(minutes=self.settings.live_freshness_minutes)
            if datetime.now(UTC) - oldest_component > freshness:
                data_fresh = False
                freshness_warning = (
                    f"Live snapshot is older than {self.settings.live_freshness_minutes} minutes; "
                    "simulated intervention recording is disabled"
                )
        return SnapshotResult(
            zones,
            "cloud",
            f"BigQuery · {self.table} · {self.scenario}",
            data_fresh=data_fresh,
            freshness_warning=freshness_warning,
        )

    def _forecast_query(self, many: bool, horizon_intervals: int = 4) -> str:
        horizon_intervals = max(1, min(96, int(horizon_intervals)))
        zone_filter = "zone_id IN UNNEST(@zone_ids)" if many else "zone_id = @zone_id"
        id_cols = ", id_cols => ['zone_id']" if many else ""
        return f"""
            SELECT * FROM AI.FORECAST(
              (SELECT {"zone_id," if many else ""} interval_start, requests
               FROM `{self.dataset}.demand_history`
               WHERE scenario_id = @scenario_id
                 AND {zone_filter}
                 AND interval_start >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {FORECAST_CONTEXT_DAYS} DAY)),
              data_col => 'requests',
              timestamp_col => 'interval_start'
              {id_cols},
              horizon => {horizon_intervals},
              confidence_level => 0.9,
              context_window => {FORECAST_CONTEXT_POINTS}
            )
            ORDER BY {"zone_id," if many else ""} forecast_timestamp
        """

    @staticmethod
    def _build_forecast(zone_id: str, horizon_minutes: int, rows: list) -> DemandForecast:
        if not rows:
            raise ForecastUnavailable(f"No demand forecast for {zone_id}")
        errors = sorted(
            {
                str(row.ai_forecast_status)
                for row in rows
                if getattr(row, "ai_forecast_status", None)
            }
        )
        if errors:
            raise ForecastUnavailable(f"TimesFM failed for {zone_id}: {'; '.join(errors)}")
        points = tuple(
            ForecastPoint(
                forecast_at=row.forecast_timestamp.astimezone(UTC),
                predicted_requests=round(row.forecast_value),
                lower_bound=round(row.prediction_interval_lower_bound),
                upper_bound=round(row.prediction_interval_upper_bound),
            )
            for row in rows
            if row.forecast_value is not None
        )
        if not points:
            raise ForecastUnavailable(f"TimesFM returned no valid points for {zone_id}")
        return DemandForecast(
            zone_id=zone_id,
            horizon_minutes=horizon_minutes,
            predicted_requests=sum(point.predicted_requests for point in points),
            source="BigQuery ML · TimesFM AI.FORECAST",
            status="OK",
            points=points,
        )

    def forecast_demand(self, zone_id: str, horizon_minutes: int = 60) -> DemandForecast:
        from google.cloud import bigquery

        horizon_minutes = max(15, min(MAX_FORECAST_MINUTES, horizon_minutes))
        horizon_intervals = max(1, round(horizon_minutes / 15))
        config = self._job_config(
            [
                bigquery.ScalarQueryParameter("scenario_id", "STRING", self.scenario),
                bigquery.ScalarQueryParameter("zone_id", "STRING", zone_id),
            ],
            100_000_000,
        )
        rows = list(
            self._client()
            .query(self._forecast_query(False, horizon_intervals), job_config=config)
            .result()
        )
        return self._build_forecast(zone_id, horizon_minutes, rows)

    def forecast_demand_many(
        self, zone_ids: list[str], horizon_minutes: int = 60
    ) -> dict[str, DemandForecast]:
        from google.cloud import bigquery

        if not zone_ids:
            return {}
        horizon_minutes = max(15, min(MAX_FORECAST_MINUTES, horizon_minutes))
        horizon_intervals = max(1, round(horizon_minutes / 15))
        config = self._job_config(
            [
                bigquery.ScalarQueryParameter("scenario_id", "STRING", self.scenario),
                bigquery.ArrayQueryParameter("zone_ids", "STRING", zone_ids),
            ],
            100_000_000,
        )
        rows = list(
            self._client()
            .query(self._forecast_query(True, horizon_intervals), job_config=config)
            .result()
        )
        grouped = {zone_id: [] for zone_id in zone_ids}
        for row in rows:
            grouped.setdefault(row.zone_id, []).append(row)
        return {
            zone_id: self._build_forecast(zone_id, horizon_minutes, grouped[zone_id])
            for zone_id in zone_ids
        }


class HybridRepository:
    def __init__(self, mode: str | None = None, scenario: str | None = None):
        self.settings = Settings.from_env()
        self.mode = (mode or self.settings.mode).lower()
        if self.mode not in {"auto", "cloud", "snapshot"}:
            raise ValueError("HEATSAFE_MODE must be auto, cloud, or snapshot")
        self.snapshot = SnapshotRepository()
        self.scenario = (scenario or self.settings.scenario).lower()
        self.cloud = None
        self._active = self.snapshot

    def _cloud(self) -> BigQueryRepository:
        if self.cloud is None:
            self.cloud = BigQueryRepository(
                self.settings.project_id, self.settings.dataset_id, self.scenario
            )
        return self.cloud

    def load(self) -> SnapshotResult:
        if self.mode == "snapshot":
            self._active = self.snapshot
            return self.snapshot.load()
        try:
            cloud = self._cloud()
            result = cloud.load()
            self._active = cloud
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
