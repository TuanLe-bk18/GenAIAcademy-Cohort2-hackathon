#!/usr/bin/env python3
"""Run the disposable Phase 4 replay-time TimesFM provider probe."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google.cloud import bigquery  # noqa: E402


PROBE_PREFIX = "heatsafe_phase4_probe_"
MAXIMUM_BYTES_BILLED = 250_000_000
SIMULATION_TIME = datetime(2026, 5, 26, 0, 0, tzinfo=UTC)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project", required=True)
    result.add_argument("--dataset", required=True)
    result.add_argument("--location", default="asia-southeast1")
    result.add_argument("--execute", action="store_true")
    return result


def _require_disposable(dataset: str) -> None:
    if not dataset.startswith(PROBE_PREFIX):
        raise ValueError(
            f"dataset must start with {PROBE_PREFIX!r}; refusing shared target"
        )


def _config(*parameters) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        query_parameters=list(parameters),
        maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
        labels={"app": "heatsafe", "component": "phase4-timesfm-probe"},
    )


def _forecast(client: bigquery.Client, table: str):
    sql = f"""
SELECT zone_id, forecast_timestamp, forecast_value, ai_forecast_status
FROM AI.FORECAST(
  (SELECT zone_id, interval_start, requests
   FROM `{table}`
   WHERE simulation_run_id = @run_id
     AND interval_start BETWEEN TIMESTAMP_SUB(@simulation_time, INTERVAL 30225 MINUTE)
                            AND @simulation_time),
  data_col => 'requests',
  timestamp_col => 'interval_start',
  id_cols => ['zone_id'],
  horizon => 16,
  confidence_level => 0.9,
  context_window => 2048
)
ORDER BY zone_id, forecast_timestamp
"""
    job = client.query(
        sql,
        job_config=_config(
            bigquery.ScalarQueryParameter("run_id", "STRING", "phase4-run"),
            bigquery.ScalarQueryParameter(
                "simulation_time", "TIMESTAMP", SIMULATION_TIME
            ),
        ),
    )
    return list(job.result()), int(job.total_bytes_processed or 0)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    _require_disposable(args.dataset)
    if not args.execute:
        print(json.dumps({
            "dry_run": True,
            "project": args.project,
            "dataset": args.dataset,
            "simulation_time": SIMULATION_TIME.isoformat(),
            "context_points_per_zone": 2016,
            "maximum_bytes_billed": MAXIMUM_BYTES_BILLED,
        }, sort_keys=True))
        return 0

    client = bigquery.Client(project=args.project, location=args.location)
    dataset_path = f"{args.project}.{args.dataset}"
    dataset = bigquery.Dataset(dataset_path)
    dataset.location = args.location
    client.create_dataset(dataset, exists_ok=False)
    table = f"{dataset_path}.timesfm_context"
    try:
        setup = f"""
CREATE TABLE `{table}`
PARTITION BY DATE(interval_start)
CLUSTER BY simulation_run_id, zone_id AS
WITH zones AS (
  SELECT FORMAT('zone-%02d', zone_number) zone_id, zone_number
  FROM UNNEST(GENERATE_ARRAY(1, 10)) zone_number
), points AS (
  SELECT interval_start
  FROM UNNEST(GENERATE_TIMESTAMP_ARRAY(
    TIMESTAMP_SUB(@simulation_time, INTERVAL 30225 MINUTE),
    @simulation_time,
    INTERVAL 15 MINUTE
  )) interval_start
)
SELECT
  'phase4-run' simulation_run_id,
  zone_id,
  interval_start,
  CAST(GREATEST(1, ROUND(
    18 + zone_number * 1.7
    + 9 * EXP(-POW(EXTRACT(HOUR FROM interval_start AT TIME ZONE 'Asia/Ho_Chi_Minh') - 8, 2) / 7.0)
    + 13 * EXP(-POW(EXTRACT(HOUR FROM interval_start AT TIME ZONE 'Asia/Ho_Chi_Minh') - 19, 2) / 8.0)
    + MOD(ABS(FARM_FINGERPRINT(CONCAT(zone_id, ':', CAST(interval_start AS STRING)))), 7)
  )) AS INT64) requests
FROM zones CROSS JOIN points
"""
        setup_job = client.query(
            setup,
            job_config=_config(
                bigquery.ScalarQueryParameter(
                    "simulation_time", "TIMESTAMP", SIMULATION_TIME
                )
            ),
        )
        setup_job.result()
        context = list(client.query(
            f"""
SELECT COUNT(*) count, COUNT(DISTINCT zone_id) zones,
       MIN(interval_start) min_context_time,
       MAX(interval_start) max_context_time
FROM `{table}`
WHERE simulation_run_id = 'phase4-run'
""",
            job_config=_config(),
        ).result())[0]
        first, first_bytes = _forecast(client, table)
        second, second_bytes = _forecast(client, table)
        if int(context["count"]) != 20_160 or int(context["zones"]) != 10:
            raise RuntimeError(f"unexpected context shape: {dict(context)!r}")
        if context["max_context_time"] > SIMULATION_TIME:
            raise RuntimeError("TimesFM context crosses simulation_time")
        if len(first) != 160 or len(second) != 160:
            raise RuntimeError("TimesFM did not return 16 points for all ten zones")
        errors = {
            str(row["ai_forecast_status"])
            for row in first + second
            if row["ai_forecast_status"]
        }
        if errors:
            raise RuntimeError(f"TimesFM provider statuses: {sorted(errors)!r}")
        if min(row["forecast_timestamp"] for row in first) <= SIMULATION_TIME:
            raise RuntimeError("TimesFM horizon is not replay-time-relative")
        deviations = [
            abs(float(left["forecast_value"]) - float(right["forecast_value"]))
            for left, right in zip(first, second, strict=True)
        ]
        tolerance = max(
            2.0,
            max(abs(float(row["forecast_value"])) for row in first) * 0.05,
        )
        if max(deviations) > tolerance:
            raise RuntimeError(
                f"two-run deviation {max(deviations):.3f} exceeds {tolerance:.3f}"
            )
        print(json.dumps({
            "context_rows": int(context["count"]),
            "zones": int(context["zones"]),
            "min_context_time": context["min_context_time"],
            "max_context_time": context["max_context_time"],
            "first_forecast_at": min(row["forecast_timestamp"] for row in first),
            "forecast_rows": len(first),
            "max_two_run_deviation": max(deviations),
            "recorded_tolerance": tolerance,
            "forecast_bytes_processed": max(first_bytes, second_bytes),
            "maximum_bytes_billed": MAXIMUM_BYTES_BILLED,
            "cleanup_dataset": dataset_path,
        }, default=str, sort_keys=True))
        return 0
    finally:
        client.delete_dataset(dataset_path, delete_contents=True, not_found_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
