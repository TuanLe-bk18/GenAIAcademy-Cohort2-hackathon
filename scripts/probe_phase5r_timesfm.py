#!/usr/bin/env python3
"""Guarded disposable TimesFM 2.5 context-window experiment for Phase 5R."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google.cloud import bigquery  # noqa: E402

from heatsafe.simulation.engine import load_zone_priors  # noqa: E402


DATASET_RE = re.compile(r"^heatsafe_phase5r_probe_[0-9]{14}$")
LOCATION = "asia-southeast1"
LOCAL_ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
AUTHORITATIVE_DATE = datetime(2026, 5, 26, tzinfo=LOCAL_ZONE).date()


def nearest_rank(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def _scheduler_paused(project: str) -> bool:
    result = subprocess.run(
        [
            "gcloud",
            "scheduler",
            "jobs",
            "describe",
            "heatsafe-simulation-every-minute",
            "--location",
            LOCATION,
            "--project",
            project,
            "--format=value(state)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "PAUSED"


class QueryBudget:
    def __init__(
        self,
        client: bigquery.Client,
        *,
        maximum_query_bytes: int,
        maximum_total_bytes: int,
    ) -> None:
        self.client = client
        self.maximum_query_bytes = maximum_query_bytes
        self.maximum_total_bytes = maximum_total_bytes
        self.total_billed = 0
        self.jobs: list[dict[str, object | None]] = []

    def run(
        self,
        sql: str,
        *,
        parameters: list[bigquery.ScalarQueryParameter] | None = None,
        label: str,
    ) -> tuple[list[Any], dict[str, object | None]]:
        config = bigquery.QueryJobConfig(
            query_parameters=parameters or [],
            maximum_bytes_billed=self.maximum_query_bytes,
            use_query_cache=False,
            labels={"app": "heatsafe", "component": "phase5r-timesfm"},
        )
        started = time.monotonic_ns()
        job = self.client.query(sql, job_config=config, location=LOCATION)
        rows = list(job.result())
        elapsed_ms = (time.monotonic_ns() - started) / 1_000_000
        billed = int(job.total_bytes_billed or 0)
        self.total_billed += billed
        if self.total_billed > self.maximum_total_bytes:
            raise RuntimeError(
                "Phase 5R cumulative billed-byte ceiling exceeded: "
                f"{self.total_billed}>{self.maximum_total_bytes}"
            )
        evidence = {
            "label": label,
            "job_id": job.job_id,
            "elapsed_ms": round(elapsed_ms, 3),
            "slot_millis": job.slot_millis,
            "total_bytes_processed": job.total_bytes_processed,
            "total_bytes_billed": job.total_bytes_billed,
            "row_count": len(rows),
        }
        self.jobs.append(evidence)
        return rows, evidence


def _corpus_bounds() -> tuple[list[datetime], datetime, datetime]:
    dates = [
        AUTHORITATIVE_DATE - timedelta(days=offset)
        for offset in reversed(range(7))
    ]
    cutoffs = [
        datetime.combine(day, datetime.min.time(), LOCAL_ZONE).replace(
            hour=hour, minute=45
        )
        for day in dates
        for hour in (5, 10, 16)
    ]
    return (
        cutoffs,
        min(cutoffs) - timedelta(minutes=2_047 * 15),
        max(cutoffs) + timedelta(minutes=16 * 15),
    )


def _corpus_sql(table_path: str) -> str:
    priors = load_zone_priors()
    structs = ",\n".join(
        "STRUCT("
        f"'{zone.zone_id}' AS zone_id, "
        f"{int(zone.forecast_requests_30m)} AS forecast_requests_30m)"
        for zone in priors
    )
    return f"""
CREATE OR REPLACE TABLE `{table_path}`
PARTITION BY DATE(interval_start)
CLUSTER BY zone_id AS
WITH zones AS (
  SELECT * FROM UNNEST([
    {structs}
  ])
), points AS (
  SELECT interval_start
  FROM UNNEST(GENERATE_TIMESTAMP_ARRAY(
    @corpus_start, @corpus_end, INTERVAL 15 MINUTE
  )) interval_start
)
SELECT
  zone.zone_id,
  points.interval_start,
  CAST(GREATEST(1, ROUND(
    zone.forecast_requests_30m / 2.0
    * (0.58
       + 0.38 * EXP(-POW(EXTRACT(
           HOUR FROM points.interval_start AT TIME ZONE 'Asia/Ho_Chi_Minh'
         ) - 8, 2) / 7.0)
       + 0.22 * EXP(-POW(EXTRACT(
           HOUR FROM points.interval_start AT TIME ZONE 'Asia/Ho_Chi_Minh'
         ) - 12, 2) / 5.0)
       + 0.52 * EXP(-POW(EXTRACT(
           HOUR FROM points.interval_start AT TIME ZONE 'Asia/Ho_Chi_Minh'
         ) - 19, 2) / 8.0)
       + MOD(ABS(FARM_FINGERPRINT(CONCAT(
           zone.zone_id, ':', CAST(points.interval_start AS STRING)
         ))), 9) / 100.0
      )
  )) AS INT64) requests
FROM zones zone
CROSS JOIN points
"""


def _forecast_sql(table_path: str, window: int, horizon: int) -> str:
    return f"""
WITH ranked AS (
  SELECT zone_id, interval_start, requests,
    ROW_NUMBER() OVER (
      PARTITION BY zone_id ORDER BY interval_start DESC
    ) AS recency_rank
  FROM `{table_path}`
  WHERE interval_start <= @cutoff
), inputs AS (
  SELECT zone_id, interval_start, requests
  FROM ranked
  WHERE recency_rank <= {window}
)
SELECT
  zone_id,
  forecast_timestamp,
  forecast_value,
  prediction_interval_lower_bound,
  prediction_interval_upper_bound,
  COALESCE(ai_forecast_status, '') AS status
FROM AI.FORECAST(
  (SELECT zone_id, interval_start, requests FROM inputs),
  data_col => 'requests',
  timestamp_col => 'interval_start',
  id_cols => ['zone_id'],
  horizon => {horizon},
  confidence_level => 0.9,
  context_window => {window},
  model => 'TimesFM 2.5'
)
ORDER BY zone_id, forecast_timestamp
"""


def _metrics(
    forecast_rows: list[Any],
    actual_rows: list[Any],
    *,
    cutoff: datetime,
) -> dict[str, object]:
    predicted: dict[tuple[str, datetime], tuple[float, float, float]] = {}
    statuses: set[str] = set()
    for raw in forecast_rows:
        row = dict(raw)
        key = (str(row["zone_id"]), row["forecast_timestamp"])
        predicted[key] = (
            float(row["forecast_value"]),
            float(row["prediction_interval_lower_bound"]),
            float(row["prediction_interval_upper_bound"]),
        )
        statuses.add(str(row["status"]))
    actual = {
        (str(row["zone_id"]), row["interval_start"]): int(row["requests"])
        for row in actual_rows
    }
    if set(predicted) != set(actual):
        raise RuntimeError(
            f"forecast/actual key mismatch at cutoff {cutoff.isoformat()}"
        )
    by_zone: dict[str, list[tuple[datetime, float, float, float, int]]] = (
        defaultdict(list)
    )
    for (zone_id, timestamp), (point, lower, upper) in predicted.items():
        by_zone[zone_id].append(
            (timestamp, point, lower, upper, actual[(zone_id, timestamp)])
        )

    def aggregate(
        values: list[tuple[datetime, float, float, float, int]]
    ) -> dict[str, object]:
        denominator = sum(item[4] for item in values)
        if denominator <= 0:
            raise RuntimeError("WAPE actual denominator is zero")
        errors = [abs(item[1] - item[4]) for item in values]
        coverage = sum(item[2] <= item[4] <= item[3] for item in values) / len(values)
        actual_peak = max(values, key=lambda item: (item[4], item[0]))[0]
        predicted_peak = max(values, key=lambda item: (item[1], item[0]))[0]
        return {
            "actual_denominator": denominator,
            "wape": sum(errors) / denominator,
            "mae": statistics.fmean(errors),
            "coverage_90": coverage,
            "actual_peak": actual_peak.isoformat(),
            "predicted_peak": predicted_peak.isoformat(),
            "peak_error_intervals": int(
                abs((predicted_peak - actual_peak).total_seconds()) // (15 * 60)
            ),
        }

    all_values = [item for values in by_zone.values() for item in values]
    return {
        "status_values": sorted(statuses),
        "city": aggregate(all_values),
        "zones": {
            zone_id: aggregate(sorted(values))
            for zone_id, values in sorted(by_zone.items())
        },
    }


def _quality_gate(
    candidate: list[dict[str, object]],
    baseline: list[dict[str, object]],
    args: argparse.Namespace,
) -> dict[str, object]:
    reasons: list[str] = []
    for candidate_fold, baseline_fold in zip(candidate, baseline):
        candidate_metrics = candidate_fold["metrics"]
        baseline_metrics = baseline_fold["metrics"]
        for scope in ["city", *sorted(candidate_metrics["zones"])]:
            candidate_scope = (
                candidate_metrics["city"]
                if scope == "city"
                else candidate_metrics["zones"][scope]
            )
            baseline_scope = (
                baseline_metrics["city"]
                if scope == "city"
                else baseline_metrics["zones"][scope]
            )
            baseline_wape = float(baseline_scope["wape"])
            relative = (
                (float(candidate_scope["wape"]) - baseline_wape) / baseline_wape
                if baseline_wape
                else 0.0
            )
            if relative * 100 > args.max_relative_wape_regression_pct:
                reasons.append(f"{candidate_fold['cutoff']}:{scope}:WAPE")
            coverage_regression = (
                float(baseline_scope["coverage_90"])
                - float(candidate_scope["coverage_90"])
            ) * 100
            if coverage_regression > args.max_coverage_regression_pp:
                reasons.append(f"{candidate_fold['cutoff']}:{scope}:COVERAGE")
            peak_regression = (
                int(candidate_scope["peak_error_intervals"])
                - int(baseline_scope["peak_error_intervals"])
            )
            if peak_regression > args.max_peak_regression_intervals:
                reasons.append(f"{candidate_fold['cutoff']}:{scope}:PEAK")
    return {"passed": not reasons, "reasons": reasons[:100]}


def run_probe(args: argparse.Namespace) -> dict[str, object]:
    if not _scheduler_paused(args.project):
        raise RuntimeError("production Scheduler must remain PAUSED")
    client = bigquery.Client(project=args.project, location=LOCATION)
    dataset_path = f"{args.project}.{args.dataset}"
    table_path = f"{dataset_path}.timesfm_corpus"
    budget = QueryBudget(
        client,
        maximum_query_bytes=args.maximum_bytes_billed,
        maximum_total_bytes=args.maximum_total_bytes_billed,
    )
    cutoffs, corpus_start, corpus_end = _corpus_bounds()
    expected_intervals = int(
        (corpus_end - corpus_start).total_seconds() // (15 * 60)
    ) + 1
    expected_rows = expected_intervals * 10
    cleanup = {"attempted": False, "succeeded": False}
    evidence: dict[str, object] = {
        "probe": "phase5r-timesfm-context",
        "outcome": "FAILED",
        "resources": {
            "dataset": dataset_path,
            "bucket": f"{args.project}-phase5r-reserved-{args.dataset[-14:]}",
            "job": f"heatsafe-phase5r-reserved-{args.dataset[-14:]}",
            "scheduler": f"heatsafe-phase5r-reserved-{args.dataset[-14:]}",
            "run": f"phase5r-timesfm-{args.dataset[-14:]}",
            "active_dataset_excluded": f"{args.project}.heatsafe_data",
        },
    }
    try:
        dataset = bigquery.Dataset(dataset_path)
        dataset.location = LOCATION
        dataset.default_table_expiration_ms = 24 * 60 * 60 * 1_000
        dataset.labels = {
            "app": "heatsafe",
            "component": "phase5r-disposable",
        }
        client.create_dataset(dataset, exists_ok=False)
        budget.run(
            _corpus_sql(table_path),
            parameters=[
                bigquery.ScalarQueryParameter(
                    "corpus_start", "TIMESTAMP", corpus_start
                ),
                bigquery.ScalarQueryParameter("corpus_end", "TIMESTAMP", corpus_end),
            ],
            label="corpus_create",
        )
        rows, _ = budget.run(
            f"""
SELECT
  COUNT(*) AS row_count,
  COUNT(DISTINCT zone_id) AS zone_count,
  MIN(interval_start) AS first_at,
  MAX(interval_start) AS last_at,
  TO_HEX(SHA256(STRING_AGG(
    CONCAT(zone_id, '|', CAST(interval_start AS STRING), '|', CAST(requests AS STRING)),
    '\\n' ORDER BY zone_id, interval_start
  ))) AS corpus_checksum
FROM `{table_path}`
""",
            label="corpus_assert",
        )
        corpus = dict(rows[0])
        if (
            int(corpus["row_count"]) != expected_rows
            or int(corpus["zone_count"]) != 10
            or corpus["first_at"] != corpus_start
            or corpus["last_at"] != corpus_end
        ):
            raise RuntimeError(f"corpus assertion failed: {corpus}")
        input_counts, _ = budget.run(
            f"""
SELECT zone_id, COUNT(*) AS point_count
FROM `{table_path}`
WHERE interval_start <= @earliest_cutoff
GROUP BY zone_id
""",
            parameters=[
                bigquery.ScalarQueryParameter(
                    "earliest_cutoff", "TIMESTAMP", min(cutoffs)
                ),
            ],
            label="input_assert",
        )
        if len(input_counts) != 10 or any(
            int(row["point_count"]) < max(args.windows) for row in input_counts
        ):
            raise RuntimeError("TimesFM input count assertion failed")

        fixed_cutoff = datetime(2026, 5, 26, 10, 45, tzinfo=LOCAL_ZONE)
        latency: dict[int, list[dict[str, object | None]]] = {
            window: [] for window in args.windows
        }
        for window in args.windows:
            budget.run(
                _forecast_sql(table_path, window, args.horizon),
                parameters=[
                    bigquery.ScalarQueryParameter(
                        "cutoff", "TIMESTAMP", fixed_cutoff
                    )
                ],
                label=f"warmup_{window}",
            )
        fixed_results: dict[int, list[Any]] = {}
        for repeat in range(args.repeats):
            for window in args.windows:
                rows, job_evidence = budget.run(
                    _forecast_sql(table_path, window, args.horizon),
                    parameters=[
                        bigquery.ScalarQueryParameter(
                            "cutoff", "TIMESTAMP", fixed_cutoff
                        )
                    ],
                    label=f"latency_{repeat + 1}_{window}",
                )
                if len(rows) != 10 * args.horizon or any(
                    dict(row)["status"] for row in rows
                ):
                    raise RuntimeError(
                        f"TimesFM output assertion failed for window {window}"
                    )
                latency[window].append(job_evidence)
                fixed_results[window] = rows

        actual_cache: dict[datetime, list[Any]] = {}
        quality: dict[int, list[dict[str, object]]] = {
            window: [] for window in args.windows
        }
        for cutoff in cutoffs:
            actual_rows, _ = budget.run(
                f"""
SELECT zone_id, interval_start, requests
FROM `{table_path}`
WHERE interval_start > @cutoff
  AND interval_start <= TIMESTAMP_ADD(@cutoff, INTERVAL {args.horizon * 15} MINUTE)
ORDER BY zone_id, interval_start
""",
                parameters=[
                    bigquery.ScalarQueryParameter("cutoff", "TIMESTAMP", cutoff)
                ],
                label=f"actual_{cutoff:%Y%m%dT%H%M}",
            )
            actual_cache[cutoff] = actual_rows
            for window in args.windows:
                if cutoff == fixed_cutoff:
                    forecast_rows = fixed_results[window]
                else:
                    forecast_rows, _ = budget.run(
                        _forecast_sql(table_path, window, args.horizon),
                        parameters=[
                            bigquery.ScalarQueryParameter(
                                "cutoff", "TIMESTAMP", cutoff
                            )
                        ],
                        label=f"quality_{cutoff:%Y%m%dT%H%M}_{window}",
                    )
                quality[window].append(
                    {
                        "cutoff": cutoff.isoformat(),
                        "metrics": _metrics(
                            forecast_rows, actual_rows, cutoff=cutoff
                        ),
                    }
                )

        latency_summary = {}
        baseline_p95 = nearest_rank(
            [float(item["elapsed_ms"]) for item in latency[2048]], 0.95
        )
        gates = {}
        for window in args.windows:
            values = [float(item["elapsed_ms"]) for item in latency[window]]
            p95 = nearest_rank(values, 0.95)
            latency_improvement = (
                (baseline_p95 - p95) / baseline_p95 * 100 if baseline_p95 else 0
            )
            latency_summary[str(window)] = {
                "p50_ms": round(statistics.median(values), 3),
                "p95_ms": round(p95, 3),
                "max_ms": round(max(values), 3),
                "improvement_vs_2048_pct": round(latency_improvement, 3),
                "jobs": latency[window],
            }
            quality_gate = _quality_gate(
                quality[window], quality[2048], args
            )
            gates[str(window)] = {
                "latency_passed": (
                    window == 2048
                    or latency_improvement >= args.min_latency_improvement_pct
                ),
                "quality_passed": quality_gate["passed"],
                "quality_failures": quality_gate["reasons"],
                "downstream_decision_passed": window == 2048,
                "downstream_note": (
                    "Stage 0E did not alter the active model/decision tables; "
                    "smaller windows fail closed until sentinel downstream "
                    "decision equivalence is measured"
                ),
            }
        selected = 2048
        for window in sorted(args.windows):
            gate = gates[str(window)]
            if (
                window < selected
                and gate["latency_passed"]
                and gate["quality_passed"]
                and gate["downstream_decision_passed"]
            ):
                selected = window
        evidence.update(
            {
                "outcome": "PASS_RETAIN_BASELINE",
                "model": args.model,
                "input_columns": [
                    "zone_id",
                    "interval_start",
                    "requests",
                ],
                "corpus": {
                    "start": corpus_start.isoformat(),
                    "end": corpus_end.isoformat(),
                    "expected_rows": expected_rows,
                    **corpus,
                },
                "latency": latency_summary,
                "quality": {str(key): value for key, value in quality.items()},
                "gates": gates,
                "selected_context_window": selected,
                "horizon": args.horizon,
                "total_bytes_billed": budget.total_billed,
                "query_count": len(budget.jobs),
            }
        )
        return evidence
    except Exception as exc:
        evidence.update(
            {
                "outcome": "FAILED",
                "error_code": type(exc).__name__,
                "message": str(exc)[:500],
                "total_bytes_billed": budget.total_billed,
                "query_count": len(budget.jobs),
            }
        )
        return evidence
    finally:
        cleanup["attempted"] = True
        try:
            client.delete_dataset(
                dataset_path,
                delete_contents=True,
                not_found_ok=True,
            )
            cleanup["succeeded"] = True
        finally:
            evidence["cleanup"] = cleanup


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--model", required=True, choices=("TimesFM 2.5",))
    parser.add_argument("--windows", nargs="+", type=int, required=True)
    parser.add_argument("--repeats", type=int, required=True)
    parser.add_argument("--horizon", type=int, required=True)
    parser.add_argument("--quality-days", type=int, required=True)
    parser.add_argument("--origins", nargs="+", required=True)
    parser.add_argument("--min-latency-improvement-pct", type=float, required=True)
    parser.add_argument(
        "--max-relative-wape-regression-pct", type=float, required=True
    )
    parser.add_argument("--max-coverage-regression-pp", type=float, required=True)
    parser.add_argument(
        "--max-peak-regression-intervals", type=int, required=True
    )
    parser.add_argument(
        "--max-selected-driver-delta-pct", type=float, required=True
    )
    parser.add_argument("--maximum-bytes-billed", type=int, required=True)
    parser.add_argument(
        "--maximum-total-bytes-billed", type=int, default=250_000_000
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required for this provider-mutating probe")
    if not DATASET_RE.fullmatch(args.dataset):
        parser.error(
            "--dataset must match heatsafe_phase5r_probe_YYYYMMDDhhmmss"
        )
    if tuple(args.windows) != (512, 1024, 2048):
        parser.error("--windows must be exactly 512 1024 2048")
    if args.repeats != 10 or args.horizon != 16 or args.quality_days != 7:
        parser.error("frozen protocol requires repeats=10, horizon=16, days=7")
    if tuple(args.origins) != ("05:45", "10:45", "16:45"):
        parser.error("frozen origins must be 05:45 10:45 16:45")
    if args.maximum_bytes_billed <= 0 or args.maximum_total_bytes_billed <= 0:
        parser.error("byte ceilings must be positive")
    return args


def main() -> None:
    args = parse_args()
    result = run_probe(args)
    print(json.dumps(result, default=str, sort_keys=True))
    if result["outcome"] == "FAILED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
