#!/usr/bin/env python3
"""Run the isolated Phase 3 BigQuery Hybrid probe.

The script is dry-run by default.  `--execute` creates and later deletes only a
dataset whose name starts with ``heatsafe_phase3_probe_``; it never targets the
shared demo dataset.
"""

from __future__ import annotations

import argparse
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
        tick = next(
            item for item in repository.ticks.values()
            if item.run_id == run.run_id and item.tick_index == 0
        )
        lease = repository.acquire_tick_lease(run.run_id, tick.tick_id, "phase3-probe")
        publication = repository.publish_tick(run.run_id, tick.tick_id, lease.fencing_token)
        finalized = repository.finalize_score(run.run_id, tick.tick_id, succeeded=True)
        retry = repository.publish_tick(run.run_id, tick.tick_id, "already-published")
        print(json.dumps({
            "run": asdict(finalized),
            "tick_status": retry.tick.status,
            "driver_rows": len(publication.driver_rows),
            "zone_rows": len(publication.zone_rows),
            "order_rows": len(publication.order_rows),
            "byte_cap": 250_000_000,
        }, default=str, sort_keys=True))
        return 0
    finally:
        client.delete_dataset(settings.dataset_path, delete_contents=True, not_found_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
