#!/usr/bin/env python3
"""Disposable oracle-seeded BigQuery component profile for Phase 5R Stage 0E."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import timedelta
import io
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google.cloud import bigquery  # noqa: E402

from heatsafe.config import Settings  # noqa: E402
from heatsafe.simulation.repository import BigQuerySimulationRepository  # noqa: E402
from heatsafe.simulation.telemetry import (  # noqa: E402
    TickTelemetry,
    bind_telemetry,
    component_span,
)
from infra.ml_pipeline import score_snapshot  # noqa: E402
from infra.provision_gcp import ensure_bigquery  # noqa: E402


DATASET_RE = re.compile(r"^heatsafe_phase5r_probe_[0-9]{14}$")
LOCATION = "asia-southeast1"


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


def _settings(args: argparse.Namespace, staging_dataset: str) -> Settings:
    return Settings(
        project_id=args.project,
        region=args.region,
        vertex_location="global",
        dataset_id=args.dataset,
        raw_bucket=f"{args.project}-heatsafe-raw",
        current_snapshot_table="zone_snapshots_current",
        mode="cloud",
        scenario="heatwave",
        enable_ai=True,
        simulation_enabled=True,
        simulation_staging_dataset_id=staging_dataset,
    )


def _create_staging_dataset(
    client: bigquery.Client, *, project: str, dataset_id: str
) -> None:
    dataset = bigquery.Dataset(f"{project}.{dataset_id}")
    dataset.location = LOCATION
    dataset.default_table_expiration_ms = 60 * 60 * 1_000
    dataset.labels = {"app": "heatsafe", "component": "phase5r-disposable"}
    client.create_dataset(dataset, exists_ok=False)


def _active_model_version(
    client: bigquery.Client, *, project: str
) -> str:
    rows = list(
        client.query(
            f"""
SELECT model_version
FROM `{project}.heatsafe_data.model_evaluations`
WHERE model_name = 'heat_risk_escalation_model'
ORDER BY evaluated_at DESC
LIMIT 1
""",
            job_config=bigquery.QueryJobConfig(
                maximum_bytes_billed=50_000_000,
                use_query_cache=False,
            ),
            location=LOCATION,
        ).result()
    )
    if not rows:
        raise RuntimeError("active heat-risk model has no evaluation version")
    return str(rows[0]["model_version"])


def _aggregate(lines: list[str]) -> dict[str, object]:
    payloads = [json.loads(line) for line in lines]
    billed = sum(int(item.get("total_bytes_billed") or 0) for item in payloads)
    return {
        "events": payloads,
        "component_count": len(payloads),
        "total_bytes_billed_observed": billed,
        "tick_total_ms": next(
            int(item["elapsed_ms"])
            for item in payloads
            if item["component"] == "tick_total"
        ),
    }


def run_probe(args: argparse.Namespace) -> dict[str, object]:
    if not _scheduler_paused(args.project):
        raise RuntimeError("production Scheduler must remain PAUSED")
    staging_dataset = f"{args.dataset}_staging"
    settings = _settings(args, staging_dataset)
    client = bigquery.Client(project=args.project, location=args.region)
    dataset_path = settings.dataset_path
    staging_path = settings.simulation_staging_dataset_path
    cleanup: dict[str, object] = {
        "attempted": False,
        "dataset_deleted": False,
        "staging_dataset_deleted": False,
    }
    evidence: dict[str, object] = {
        "probe": "phase5r-disposable-component-baseline",
        "outcome": "FAILED",
        "resources": {
            "dataset": dataset_path,
            "staging_dataset": staging_path,
            "bucket": f"{args.project}-phase5r-reserved-{args.dataset[-14:]}",
            "job": f"heatsafe-phase5r-reserved-{args.dataset[-14:]}",
            "scheduler": f"heatsafe-phase5r-reserved-{args.dataset[-14:]}",
            "run_tag": f"phase5r-profile-{args.dataset[-14:]}",
            "active_dataset_excluded": f"{args.project}.heatsafe_data",
        },
    }
    try:
        with redirect_stdout(io.StringIO()):
            ensure_bigquery(settings, include_views=False)
        _create_staging_dataset(
            client, project=args.project, dataset_id=staging_dataset
        )
        model_version = _active_model_version(client, project=args.project)
        repository = BigQuerySimulationRepository(
            client,
            dataset=dataset_path,
            staging_dataset=staging_path,
            lease_seconds=360,
        )
        run = repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )
        profiles: list[dict[str, object]] = []
        cumulative_billed = 0
        for tick_index in args.sentinels:
            tick = next(
                item
                for item in repository.ticks.values()
                if item.run_id == run.run_id and item.tick_index == tick_index
            )
            lines: list[str] = []
            telemetry = TickTelemetry(sink=lines.append)
            telemetry.bind(
                simulation_run_id=run.run_id,
                tick_id=tick.tick_id,
                tick_index=tick.tick_index,
                snapshot_id=tick.snapshot_id,
                state_mode="oracle",
                execution_mode="FULL",
            )
            with telemetry.activate():
                try:
                    with component_span("lease_acquire"):
                        lease = repository.acquire_tick_lease(
                            run.run_id,
                            tick.tick_id,
                            f"phase5r-{tick_index}",
                        )
                    publication = repository.publish_tick(
                        run.run_id, tick.tick_id, lease.fencing_token
                    )
                    with redirect_stdout(io.StringIO()):
                        prediction_run_id = score_snapshot(
                            settings,
                            client,
                            scenario="heatwave",
                            model_version=model_version,
                            feature_source="simulation",
                            simulation_run_id=run.run_id,
                            tick_id=tick.tick_id,
                            snapshot_id=tick.snapshot_id,
                            simulation_time=tick.simulation_time,
                            model_dataset=f"{args.project}.heatsafe_data",
                        )
                    repository.mark_scored(run.run_id, tick.tick_id)
                    repository.finalize_score(
                        run.run_id, tick.tick_id, succeeded=True
                    )
                except Exception as exc:
                    telemetry.finish(
                        outcome="FAILED", error_code=type(exc).__name__
                    )
                    raise
                telemetry.finish(outcome="SUCCEEDED")
            profile = {
                "tick_index": tick_index,
                "tick_id": tick.tick_id,
                "snapshot_id": tick.snapshot_id,
                "prediction_run_id": prediction_run_id,
                **_aggregate(lines),
            }
            cumulative_billed += int(profile["total_bytes_billed_observed"])
            if cumulative_billed > args.maximum_total_bytes_billed:
                raise RuntimeError(
                    "Phase 5R cumulative byte ceiling exceeded: "
                    f"{cumulative_billed}>{args.maximum_total_bytes_billed}"
                )
            profiles.append(profile)
        evidence.update(
            {
                "outcome": "PASS",
                "simulation_run_id": run.run_id,
                "model_version": model_version,
                "model_dataset_read_only": f"{args.project}.heatsafe_data",
                "sentinels": profiles,
                "total_bytes_billed_observed": cumulative_billed,
                "maximum_total_bytes_billed": args.maximum_total_bytes_billed,
            }
        )
        return evidence
    except Exception as exc:
        evidence.update(
            {
                "outcome": "FAILED",
                "error_code": type(exc).__name__,
                "message": str(exc)[:500],
            }
        )
        return evidence
    finally:
        cleanup["attempted"] = True
        client.delete_dataset(
            staging_path, delete_contents=True, not_found_ok=True
        )
        cleanup["staging_dataset_deleted"] = True
        client.delete_dataset(
            dataset_path, delete_contents=True, not_found_ok=True
        )
        cleanup["dataset_deleted"] = True
        evidence["cleanup"] = cleanup


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True, choices=(LOCATION,))
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--sentinels", required=True)
    parser.add_argument("--oracle-seed-disposable", action="store_true")
    parser.add_argument("--maximum-total-bytes-billed", type=int, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute or not args.oracle_seed_disposable:
        parser.error("--execute and --oracle-seed-disposable are required")
    if not DATASET_RE.fullmatch(args.dataset):
        parser.error(
            "--dataset must match heatsafe_phase5r_probe_YYYYMMDDhhmmss"
        )
    try:
        args.sentinels = tuple(
            sorted({int(item) for item in args.sentinels.split(",")})
        )
    except ValueError as exc:
        parser.error(f"--sentinels must be comma-separated integers: {exc}")
    if args.sentinels != (0, 24, 48, 95):
        parser.error("--sentinels must be exactly 0,24,48,95")
    if args.maximum_total_bytes_billed <= 0:
        parser.error("--maximum-total-bytes-billed must be positive")
    return args


def main() -> None:
    result = run_probe(parse_args())
    print(json.dumps(result, default=str, sort_keys=True))
    if result["outcome"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
