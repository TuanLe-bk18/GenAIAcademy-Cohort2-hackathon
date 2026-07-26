"""Provision the dedicated staging dataset and its runtime grant.

Table-level grants and Cloud Run IAM are applied by deploy_simulation_gcp.sh.
The exact-model conditional grant is applied at project IAM scope by gcloud,
where IAM policy version 3 is handled natively.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from heatsafe.config import Settings


def _append_access_entry(
    dataset: bigquery.Dataset,
    entry: bigquery.AccessEntry,
) -> bool:
    current = list(dataset.access_entries)
    if entry in current:
        return False
    dataset.access_entries = [*current, entry]
    return True


def configure(
    settings: Settings,
    *,
    runtime_service_account: str,
) -> dict[str, object]:
    client = bigquery.Client(project=settings.project_id)
    staging = bigquery.Dataset(settings.simulation_staging_dataset_path)
    staging.location = settings.region
    staging.default_table_expiration_ms = int(timedelta(hours=1).total_seconds() * 1_000)
    staging.labels = {
        "app": "heatsafe",
        "env": "demo",
        "component": "simulation-staging",
        "managed_by": "scripts",
    }
    client.create_dataset(staging, exists_ok=True)
    staging = client.get_dataset(settings.simulation_staging_dataset_path)
    if (
        staging.location is None
        or staging.location.lower() != settings.region.lower()
    ):
        raise RuntimeError(
            f"{staging.full_dataset_id} location conflict: "
            f"{staging.location!r} != {settings.region!r}"
        )
    staging.default_table_expiration_ms = int(
        timedelta(hours=1).total_seconds() * 1_000
    )
    staging.labels = {
        **(staging.labels or {}),
        "app": "heatsafe",
        "env": "demo",
        "component": "simulation-staging",
        "managed_by": "scripts",
    }
    staging_writer = bigquery.AccessEntry(
        role="roles/bigquery.dataEditor",
        entity_type="iamMember",
        entity_id=f"serviceAccount:{runtime_service_account}",
    )
    _append_access_entry(staging, staging_writer)
    client.update_dataset(
        staging, ["access_entries", "default_table_expiration_ms", "labels"]
    )

    return {
        "main_dataset": settings.dataset_path,
        "staging_dataset": settings.simulation_staging_dataset_path,
        "staging_default_expiration_seconds": 3_600,
        "runtime_service_account": runtime_service_account,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-service-account", required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    result = configure(
        settings,
        runtime_service_account=args.runtime_service_account,
    )
    for key, value in result.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
