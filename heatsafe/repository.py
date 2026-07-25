from __future__ import annotations

import json
import math
from typing import Any
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .models import DriverActionPrediction, ZoneSnapshot

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
    forecast_reused: bool = False
    forecast_source_tick_id: str | None = None
    forecast_source_snapshot_id: str | None = None
    forecast_source_prediction_run_id: str | None = None
    forecast_age_minutes: int | None = None
    generated_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "horizon_minutes": self.horizon_minutes,
            "predicted_requests": self.predicted_requests,
            "source": self.source,
            "status": self.status,
            "forecast_reused": self.forecast_reused,
            "forecast_source_tick_id": self.forecast_source_tick_id,
            "forecast_source_snapshot_id": self.forecast_source_snapshot_id,
            "forecast_source_prediction_run_id": (
                self.forecast_source_prediction_run_id
            ),
            "forecast_age_minutes": self.forecast_age_minutes,
            "generated_at": (
                self.generated_at.isoformat() if self.generated_at else None
            ),
            "points": [point.to_dict() for point in self.points],
        }


class ForecastUnavailable(RuntimeError):
    pass


class AIModelUnavailable(RuntimeError):
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
        simulation_run_id=raw.get("simulation_run_id"),
        tick_id=raw.get("tick_id"),
        generator_version=raw.get("generator_version"),
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

    def load_driver_predictions(
        self, zone_id: str, snapshot_id: str
    ) -> tuple[DriverActionPrediction, ...]:
        raise AIModelUnavailable(
            "Driver-level BigQuery ML predictions are unavailable in snapshot mode"
        )

    def load_driver_predictions_many(
        self, zone_ids: list[str], snapshot_id: str
    ) -> dict[str, tuple[DriverActionPrediction, ...]]:
        if not zone_ids:
            return {}
        raise AIModelUnavailable(
            "Driver-level BigQuery ML predictions are unavailable in snapshot mode"
        )


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
        self._client_instance: Any | None = None

    def _client(self) -> Any:
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
        first = rows[0]
        reused = bool(getattr(first, "forecast_reused", False))
        source_tick_id = getattr(first, "forecast_source_tick_id", None)
        age_minutes = getattr(first, "forecast_age_minutes", None)
        source = "BigQuery ML · TimesFM AI.FORECAST"
        if reused:
            source = (
                "BigQuery ML · TimesFM reused"
                f" from tick {source_tick_id or 'unknown'}"
                f" · age {int(age_minutes or 0)} min"
            )
        return DemandForecast(
            zone_id=zone_id,
            horizon_minutes=horizon_minutes,
            predicted_requests=sum(point.predicted_requests for point in points),
            source=source,
            status="OK",
            points=points,
            forecast_reused=reused,
            forecast_source_tick_id=source_tick_id,
            forecast_source_snapshot_id=getattr(
                first, "forecast_source_snapshot_id", None
            ),
            forecast_source_prediction_run_id=getattr(
                first, "forecast_source_prediction_run_id", None
            ),
            forecast_age_minutes=(
                int(age_minutes) if age_minutes is not None else None
            ),
            generated_at=getattr(first, "generated_at", None),
        )

    def forecast_demand(self, zone_id: str, horizon_minutes: int = 60) -> DemandForecast:
        from google.cloud import bigquery

        horizon_minutes = max(15, min(MAX_FORECAST_MINUTES, horizon_minutes))
        horizon_intervals = max(1, round(horizon_minutes / 15))
        query = f"""
            SELECT
              forecast_at forecast_timestamp,
              predicted_requests forecast_value,
              lower_bound prediction_interval_lower_bound,
              upper_bound prediction_interval_upper_bound,
              status ai_forecast_status,
              forecast_reused,
              forecast_source_tick_id,
              forecast_source_snapshot_id,
              forecast_source_prediction_run_id,
              forecast_age_minutes,
              generated_at
            FROM `{self.dataset}.zone_demand_forecasts` forecast
            WHERE forecast.scenario_id = @scenario_id
              AND forecast.zone_id = @zone_id
              AND EXISTS (
                SELECT 1 FROM `{self.table}` current
                WHERE current.scenario_id = @scenario_id
                  AND current.zone_id = forecast.zone_id
                  AND current.snapshot_id = forecast.snapshot_id
                  AND current.simulation_run_id IS NOT DISTINCT FROM
                      forecast.simulation_run_id
                  AND current.tick_id IS NOT DISTINCT FROM forecast.tick_id
              )
            ORDER BY forecast_at
            LIMIT @horizon_intervals
        """
        config = self._job_config(
            [
                bigquery.ScalarQueryParameter("scenario_id", "STRING", self.scenario),
                bigquery.ScalarQueryParameter("zone_id", "STRING", zone_id),
                bigquery.ScalarQueryParameter("horizon_intervals", "INT64", horizon_intervals),
            ],
            100_000_000,
        )
        rows = list(self._client().query(query, job_config=config).result())
        return self._build_forecast(zone_id, horizon_minutes, rows)

    def forecast_demand_many(
        self, zone_ids: list[str], horizon_minutes: int = 60
    ) -> dict[str, DemandForecast]:
        from google.cloud import bigquery

        if not zone_ids:
            return {}
        horizon_minutes = max(15, min(MAX_FORECAST_MINUTES, horizon_minutes))
        horizon_intervals = max(1, round(horizon_minutes / 15))
        query = f"""
            WITH latest_runs AS (
              SELECT
                zone_id,
                forecast_at forecast_timestamp,
                predicted_requests forecast_value,
                lower_bound prediction_interval_lower_bound,
                upper_bound prediction_interval_upper_bound,
                status ai_forecast_status,
                forecast_reused,
                forecast_source_tick_id,
                forecast_source_snapshot_id,
                forecast_source_prediction_run_id,
                forecast_age_minutes,
                generated_at
              FROM `{self.dataset}.zone_demand_forecasts` forecast
              WHERE forecast.scenario_id = @scenario_id
                AND forecast.zone_id IN UNNEST(@zone_ids)
                AND EXISTS (
                  SELECT 1 FROM `{self.table}` current
                  WHERE current.scenario_id = @scenario_id
                    AND current.zone_id = forecast.zone_id
                    AND current.snapshot_id = forecast.snapshot_id
                    AND current.simulation_run_id IS NOT DISTINCT FROM
                        forecast.simulation_run_id
                    AND current.tick_id IS NOT DISTINCT FROM forecast.tick_id
                )
            )
            SELECT *
            FROM latest_runs
            QUALIFY ROW_NUMBER() OVER (
              PARTITION BY zone_id ORDER BY forecast_timestamp
            ) <= @horizon_intervals
            ORDER BY zone_id, forecast_timestamp
        """
        config = self._job_config(
            [
                bigquery.ScalarQueryParameter("scenario_id", "STRING", self.scenario),
                bigquery.ArrayQueryParameter("zone_ids", "STRING", zone_ids),
                bigquery.ScalarQueryParameter("horizon_intervals", "INT64", horizon_intervals),
            ],
            100_000_000,
        )
        rows = list(self._client().query(query, job_config=config).result())
        grouped = {zone_id: [] for zone_id in zone_ids}
        for row in rows:
            grouped.setdefault(str(row.zone_id), []).append(row)
        forecasts: dict[str, DemandForecast] = {}
        for zone_id in zone_ids:
            try:
                forecasts[zone_id] = self._build_forecast(
                    zone_id, horizon_minutes, grouped[zone_id]
                )
            except ForecastUnavailable:
                # The city service reports this zone explicitly while retaining
                # valid forecasts returned for other zones in the same batch.
                continue
        return forecasts

    @staticmethod
    def _factor_names(raw: Any) -> tuple[str, ...]:
        if raw is None:
            return ()
        value = json.loads(raw) if isinstance(raw, str) else raw
        return tuple(
            str(item.get("feature"))
            for item in value
            if isinstance(item, dict) and item.get("feature")
        )

    @classmethod
    def _build_driver_predictions(
        cls, rows: list[Any]
    ) -> tuple[DriverActionPrediction, ...]:
        return tuple(
            DriverActionPrediction(
                driver_id_hash=str(row.driver_id_hash),
                zone_id=str(row.zone_id),
                snapshot_id=str(row.snapshot_id),
                prediction_run_id=str(row.prediction_run_id),
                model_version=str(row.model_version),
                exposure_minutes=int(row.continuous_exposure_minutes),
                baseline_risk=float(row.baseline_risk_probability),
                action_risk=float(row.risk_probability),
                pause_start_delay_minutes=int(row.pause_start_delay_minutes),
                pause_duration_minutes=int(row.pause_duration_minutes),
                top_factors=cls._factor_names(row.top_factors_json),
            )
            for row in rows
        )

    def load_driver_predictions(
        self, zone_id: str, snapshot_id: str
    ) -> tuple[DriverActionPrediction, ...]:
        from google.cloud import bigquery

        query = f"""
            SELECT *
            FROM `{self.dataset}.driver_risk_predictions`
            WHERE scenario_id = @scenario_id
              AND zone_id = @zone_id
              AND snapshot_id = @snapshot_id
              AND action_type = 'SAFEPAUSE'
            QUALIFY prediction_run_id = MAX(prediction_run_id) OVER ()
            ORDER BY driver_id_hash, pause_duration_minutes, pause_start_delay_minutes
        """
        config = self._job_config(
            [
                bigquery.ScalarQueryParameter("scenario_id", "STRING", self.scenario),
                bigquery.ScalarQueryParameter("zone_id", "STRING", zone_id),
                bigquery.ScalarQueryParameter("snapshot_id", "STRING", snapshot_id),
            ],
            100_000_000,
        )
        rows = list(self._client().query(query, job_config=config).result())
        if not rows:
            raise AIModelUnavailable(
                f"No AI predictions for snapshot {snapshot_id} in zone {zone_id}"
            )
        return self._build_driver_predictions(rows)

    def load_driver_predictions_many(
        self, zone_ids: list[str], snapshot_id: str
    ) -> dict[str, tuple[DriverActionPrediction, ...]]:
        from google.cloud import bigquery

        if not zone_ids:
            return {}
        query = f"""
            SELECT *
            FROM `{self.dataset}.driver_risk_predictions`
            WHERE scenario_id = @scenario_id
              AND zone_id IN UNNEST(@zone_ids)
              AND snapshot_id = @snapshot_id
              AND action_type = 'SAFEPAUSE'
            QUALIFY prediction_run_id = MAX(prediction_run_id) OVER (PARTITION BY zone_id)
            ORDER BY zone_id, driver_id_hash, pause_duration_minutes,
                     pause_start_delay_minutes
        """
        config = self._job_config(
            [
                bigquery.ScalarQueryParameter("scenario_id", "STRING", self.scenario),
                bigquery.ArrayQueryParameter("zone_ids", "STRING", zone_ids),
                bigquery.ScalarQueryParameter("snapshot_id", "STRING", snapshot_id),
            ],
            100_000_000,
        )
        rows = list(self._client().query(query, job_config=config).result())
        grouped: dict[str, list[Any]] = {}
        for row in rows:
            grouped.setdefault(str(row.zone_id), []).append(row)
        return {
            zone_id: self._build_driver_predictions(zone_rows)
            for zone_id, zone_rows in grouped.items()
        }

    def load_zone_risk_summary(self, snapshot_id: str) -> dict[str, float]:
        from google.cloud import bigquery

        query = f"""
            SELECT zone_id, SUM(baseline_risk_probability) expected_events
            FROM (
              SELECT DISTINCT zone_id, driver_id_hash, baseline_risk_probability
              FROM `{self.dataset}.driver_risk_predictions`
              WHERE scenario_id = @scenario_id AND snapshot_id = @snapshot_id
            )
            GROUP BY zone_id
        """
        config = self._job_config(
            [
                bigquery.ScalarQueryParameter("scenario_id", "STRING", self.scenario),
                bigquery.ScalarQueryParameter("snapshot_id", "STRING", snapshot_id),
            ],
            100_000_000,
        )
        rows = list(self._client().query(query, job_config=config).result())
        if not rows:
            raise AIModelUnavailable(f"No zone AI summary for snapshot {snapshot_id}")
        return {str(row.zone_id): float(row.expected_events) for row in rows}

    def load_model_evaluations(self, limit: int = 10) -> list[dict]:
        from google.cloud import bigquery

        limit = max(1, min(limit, 50))
        query = f"""
            SELECT model_version, evaluated_at, model_name,
                   precision, recall, accuracy, f1_score, log_loss, roc_auc,
                   is_simulated
            FROM `{self.dataset}.model_evaluations`
            ORDER BY evaluated_at DESC
            LIMIT @limit
        """
        config = self._job_config(
            [bigquery.ScalarQueryParameter("limit", "INT64", limit)],
            MINIMUM_QUERY_BYTES_BILLED,
        )
        return [
            dict(row)
            for row in self._client().query(query, job_config=config).result()
        ]


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

    def load_driver_predictions(
        self, zone_id: str, snapshot_id: str
    ) -> tuple[DriverActionPrediction, ...]:
        if not isinstance(self._active, BigQueryRepository):
            raise AIModelUnavailable(
                "AI recommendations require materialized BigQuery ML predictions"
            )
        return self._active.load_driver_predictions(zone_id, snapshot_id)

    def load_driver_predictions_many(
        self, zone_ids: list[str], snapshot_id: str
    ) -> dict[str, tuple[DriverActionPrediction, ...]]:
        if not zone_ids:
            return {}
        if not isinstance(self._active, BigQueryRepository):
            raise AIModelUnavailable(
                "AI recommendations require materialized BigQuery ML predictions"
            )
        return self._active.load_driver_predictions_many(zone_ids, snapshot_id)

    def load_zone_risk_summary(self, snapshot_id: str) -> dict[str, float]:
        if not isinstance(self._active, BigQueryRepository):
            raise AIModelUnavailable(
                "Zone AI summary requires materialized BigQuery ML predictions"
            )
        return self._active.load_zone_risk_summary(snapshot_id)

    def load_model_evaluations(self, limit: int = 10) -> list[dict]:
        if not isinstance(self._active, BigQueryRepository):
            raise AIModelUnavailable(
                "Model evaluation metrics require the BigQuery ML repository"
            )
        return self._active.load_model_evaluations(limit)
