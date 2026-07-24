#!/usr/bin/env python3
"""Run the isolated Phase 3 BigQuery Hybrid probe.

The script is dry-run by default.  `--execute` creates and later deletes only a
dataset whose name starts with ``heatsafe_phase3_probe_``; it never targets the
shared demo dataset.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google.cloud import bigquery  # noqa: E402

from heatsafe.config import Settings  # noqa: E402
from heatsafe.simulation.repository import BigQuerySimulationRepository  # noqa: E402
from infra.provision_gcp import ensure_bigquery  # noqa: E402


PROBE_PREFIX = "heatsafe_phase3_probe_"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project", required=True)
    result.add_argument("--dataset", required=True)
    result.add_argument("--execute", action="store_true")
    return result


def _require_disposable(dataset: str) -> None:
    if not dataset.startswith(PROBE_PREFIX):
        raise ValueError(
            f"dataset must start with {PROBE_PREFIX!r}; refusing shared/non-probe target"
        )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    _require_disposable(args.dataset)
    if not args.execute:
        print(json.dumps({
            "dry_run": True,
            "project": args.project,
            "dataset": args.dataset,
            "command": "rerun with --execute after confirming billing cap and disposable target",
        }, sort_keys=True))
        return 0

    settings = Settings(project_id=args.project, dataset_id=args.dataset)
    client = ensure_bigquery(settings, include_views=False)
    repository = BigQuerySimulationRepository(client, dataset=settings.dataset_path)
    try:
        run = repository.start(
            scenario_id="heatwave", scenario_version="hanoi_heatwave_v1", seed=42
        )
        # Provider-level rollback proof: an intentionally failed transaction
        # must leave the durable coordinator row unchanged before real work.
        try:
            client.query(f"""
BEGIN TRANSACTION;
UPDATE `{settings.dataset_path}.simulation_runs`
SET status = 'PAUSED'
WHERE simulation_run_id = '{run.run_id}';
ASSERT FALSE AS 'intentional phase3 rollback probe';
COMMIT TRANSACTION;
""").result()
        except Exception:
            pass
        else:
            raise RuntimeError("rollback probe unexpectedly committed")
        after_rollback = repository.status("heatwave")
        if after_rollback is None or after_rollback.status != "RUNNING":
            raise RuntimeError("rollback probe changed the durable run state")

        def acquire(candidate: str):
            contender = BigQuerySimulationRepository(
                bigquery.Client(project=args.project), dataset=settings.dataset_path
            )
            loaded = contender.status("heatwave")
            tick = next(
                item for item in contender.ticks.values()
                if item.run_id == loaded.run_id and item.tick_index == 0
            )
            try:
                lease = contender.acquire_tick_lease(loaded.run_id, tick.tick_id, candidate)
                return contender, lease, None
            except Exception as exc:  # BigQuery rejects the losing conditional lease.
                return contender, None, type(exc).__name__

        with ThreadPoolExecutor(max_workers=2) as executor:
            attempts = list(executor.map(acquire, ("phase3-probe-a", "phase3-probe-b")))
        winners = [(contender, lease) for contender, lease, _ in attempts if lease]
        if len(winners) != 1:
            raise RuntimeError(f"expected one lease winner, got {len(winners)}: {attempts!r}")
        winner, lease = winners[0]
        publication = winner.publish_tick(run.run_id, lease.tick_id, lease.fencing_token)
        finalized = winner.finalize_score(run.run_id, lease.tick_id, succeeded=True)
        # Prove a new worker can read the durable state and retry without a
        # second publication transaction or duplicated rows.
        retry_repository = BigQuerySimulationRepository(
            bigquery.Client(project=args.project), dataset=settings.dataset_path
        )
        durable_run = retry_repository.status("heatwave")
        assert durable_run is not None
        retry = retry_repository.publish_tick(
            durable_run.run_id, lease.tick_id, "already-published"
        )
        table_counts = {}
        for table_name in (
            "driver_simulation_state",
            "zone_snapshots_current",
            "order_events",
            "weather_observations",
            "zone_operations",
            "demand_history",
            "driver_state_history",
        ):
            query = (
                f"SELECT COUNT(*) AS count FROM `{settings.dataset_path}.{table_name}` "
                f"WHERE simulation_run_id = '{run.run_id}'"
            )
            rows = list(client.query(
                query,
                job_config=bigquery.QueryJobConfig(maximum_bytes_billed=250_000_000),
            ).result())
            table_counts[table_name] = rows[0]["count"]
        print(json.dumps({
            "run": asdict(durable_run),
            "tick_status": retry.tick.status,
            "driver_rows": len(publication.driver_rows),
            "zone_rows": len(publication.zone_rows),
            "order_rows": len(publication.order_rows),
            "byte_cap": 250_000_000,
            "concurrent_lease_winners": len(winners),
            "concurrent_lease_loser_errors": [error for _, _, error in attempts if error],
            "durable_table_counts": table_counts,
            "no_op_retry_rows": len(retry.driver_rows),
            "rollback_preserved_running_state": True,
        }, default=str, sort_keys=True))
        return 0
    finally:
        client.delete_dataset(settings.dataset_path, delete_contents=True, not_found_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
