#!/usr/bin/env python3
"""Provision HeatSafe resources without mutating demo or intervention data by default."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.cloud import bigquery, storage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from heatsafe.bigquery_io import merge_rows  # noqa: E402
from heatsafe.config import Settings  # noqa: E402


def _f(name: str, field_type: str, mode: str = "REQUIRED") -> bigquery.SchemaField:
    return bigquery.SchemaField(name, field_type, mode=mode)


def table_schemas() -> dict[str, list[bigquery.SchemaField]]:
    scenario = [_f("scenario_id", "STRING", "NULLABLE"), _f("snapshot_id", "STRING", "NULLABLE")]
    operations = [
        _f("zone_id", "STRING"), _f("observed_at", "TIMESTAMP"),
        _f("active_drivers", "INT64"), _f("fresh_drivers", "INT64"),
        _f("exposed_2h", "INT64"), _f("exposed_4h", "INT64"),
        _f("forecast_requests_30m", "INT64"),
        _f("avg_platform_contribution_vnd", "INT64"),
        _f("avg_driver_earnings_vnd", "INT64"), _f("is_simulated", "BOOL"),
    ]
    return {
        "weather_observations": scenario + [
            _f("zone_id", "STRING"), _f("name", "STRING"),
            _f("latitude", "FLOAT64"), _f("longitude", "FLOAT64"),
            _f("temperature_c", "FLOAT64"), _f("humidity_percent", "FLOAT64"),
            _f("heat_index_c", "FLOAT64"), _f("observed_at", "TIMESTAMP"),
            _f("ingested_at", "TIMESTAMP"), _f("source", "STRING"),
            _f("raw_gcs_uri", "STRING"), _f("is_simulated", "BOOL"),
        ],
        "zone_operations": scenario + operations,
        "demand_history": [
            _f("scenario_id", "STRING", "NULLABLE"), _f("zone_id", "STRING"),
            _f("interval_start", "TIMESTAMP"), _f("requests", "INT64"),
            _f("is_simulated", "BOOL"),
        ],
        "coolstop_partners": [
            _f("scenario_id", "STRING", "NULLABLE"), _f("zone_id", "STRING"),
            _f("coolstop_name", "STRING"), _f("coolstop_latitude", "FLOAT64"),
            _f("coolstop_longitude", "FLOAT64"),
            _f("sponsor_per_driver_vnd", "INT64"), _f("is_simulated", "BOOL"),
        ],
        "intervention_proposals": [
            _f("proposal_id", "STRING"), _f("created_at", "TIMESTAMP"),
            _f("zone_id", "STRING"), _f("eligible_drivers", "INT64"),
            _f("selected_drivers", "INT64", "NULLABLE"),
            _f("exposure_minutes_avoided", "INT64"),
            _f("net_platform_cost_vnd", "INT64"),
            _f("projected_fulfillment_rate", "FLOAT64"),
            _f("within_guardrails", "BOOL"), _f("proposal_json", "JSON"),
        ],
        "intervention_events": [
            _f("intervention_id", "STRING"), _f("proposal_id", "STRING"),
            _f("approved_at", "TIMESTAMP"), _f("approved_by", "STRING"),
            _f("actor_type", "STRING", "NULLABLE"), _f("status", "STRING"),
            _f("dispatch_status", "STRING"), _f("zone_id", "STRING"),
            _f("eligible_drivers", "INT64"),
            _f("selected_drivers", "INT64", "NULLABLE"),
            _f("exposure_minutes_avoided", "INT64"),
            _f("net_platform_cost_vnd", "INT64"),
        ],
        "zone_snapshots_current": [
            _f("scenario_id", "STRING"), _f("snapshot_id", "STRING"),
            _f("zone_id", "STRING"), _f("name", "STRING"),
            _f("latitude", "FLOAT64"), _f("longitude", "FLOAT64"),
            _f("temperature_c", "FLOAT64"), _f("humidity_percent", "FLOAT64"),
            _f("heat_index_c", "FLOAT64"), _f("observed_at", "TIMESTAMP"),
            _f("weather_observed_at", "TIMESTAMP"),
            _f("operations_observed_at", "TIMESTAMP"), _f("refreshed_at", "TIMESTAMP"),
            *operations[2:-1],
            _f("coolstop_name", "STRING"), _f("coolstop_latitude", "FLOAT64"),
            _f("coolstop_longitude", "FLOAT64"), _f("source", "STRING"),
            _f("weather_is_simulated", "BOOL"),
            _f("operations_is_simulated", "BOOL"),
        ],
    }


def ensure_bucket(settings: Settings) -> storage.Bucket:
    client = storage.Client(project=settings.project_id)
    bucket = client.lookup_bucket(settings.raw_bucket)
    if bucket is None:
        bucket = client.bucket(settings.raw_bucket)
        bucket.iam_configuration.uniform_bucket_level_access_enabled = True
        bucket.labels = {"app": "heatsafe", "env": "demo", "managed_by": "scripts"}
        bucket = client.create_bucket(bucket, project=settings.project_id, location=settings.region)
        print(f"Created gs://{bucket.name}")
    else:
        labels = dict(bucket.labels or {})
        labels.update({"app": "heatsafe", "env": "demo", "managed_by": "scripts"})
        bucket.labels = labels
        bucket.patch()
        print(f"Using gs://{bucket.name}")
    if not any(rule.get("condition", {}).get("matchesPrefix") == ["weather/"] for rule in bucket.lifecycle_rules):
        bucket.add_lifecycle_delete_rule(age=30, matches_prefix=["weather/"])
        bucket.patch()
    return bucket


def _table_exists(client: bigquery.Client, table_id: str) -> bool:
    try:
        client.get_table(table_id)
        return True
    except Exception:
        return False


def _ensure_table(
    client: bigquery.Client,
    table_id: str,
    schema: list[bigquery.SchemaField],
    partition_field: str | None,
    clustering: list[str] | None,
) -> None:
    if not _table_exists(client, table_id):
        table = bigquery.Table(table_id, schema=schema)
        if partition_field:
            table.time_partitioning = bigquery.TimePartitioning(field=partition_field)
        table.clustering_fields = clustering
        table.labels = {"app": "heatsafe", "env": "demo", "managed_by": "scripts"}
        client.create_table(table)
        return
    table = client.get_table(table_id)
    existing = {field.name for field in table.schema}
    missing = [field for field in schema if field.name not in existing]
    fields = []
    if missing:
        table.schema = [*table.schema, *missing]
        fields.append("schema")
    labels = dict(table.labels or {})
    labels.update({"app": "heatsafe", "env": "demo", "managed_by": "scripts"})
    table.labels = labels
    fields.append("labels")
    client.update_table(table, fields)


def ensure_bigquery(settings: Settings) -> bigquery.Client:
    client = bigquery.Client(project=settings.project_id)
    dataset_ref = settings.dataset_path
    dataset = bigquery.Dataset(dataset_ref)
    dataset.location = settings.region
    dataset.labels = {"app": "heatsafe", "env": "demo", "managed_by": "scripts"}
    client.create_dataset(dataset, exists_ok=True)
    current_dataset = client.get_dataset(dataset_ref)
    current_dataset.labels = dataset.labels
    client.update_dataset(current_dataset, ["labels"])

    partitioned = {
        "weather_observations": "observed_at", "zone_operations": "observed_at",
        "demand_history": "interval_start", "intervention_proposals": "created_at",
        "intervention_events": "approved_at",
    }
    clustered = {
        "weather_observations": ["scenario_id", "zone_id"],
        "zone_operations": ["scenario_id", "zone_id"],
        "demand_history": ["scenario_id", "zone_id"],
        "intervention_proposals": ["zone_id"],
        "intervention_events": ["zone_id", "status"],
        "zone_snapshots_current": ["scenario_id", "zone_id"],
    }
    for name, schema in table_schemas().items():
        _ensure_table(
            client, f"{dataset_ref}.{name}", schema,
            partitioned.get(name), clustered.get(name),
        )

    view_specs = {
        "zone_snapshots": "SELECT * FROM `{table}`",
        "zone_snapshots_live": "SELECT * FROM `{table}` WHERE scenario_id = 'live'",
        "zone_snapshots_heatwave": "SELECT * FROM `{table}` WHERE scenario_id = 'heatwave'",
    }
    for view_name, query_template in view_specs.items():
        view_id = f"{dataset_ref}.{view_name}"
        view = bigquery.Table(view_id)
        view.view_query = query_template.format(
            table=f"{dataset_ref}.{settings.current_snapshot_table}"
        )
        if _table_exists(client, view_id):
            existing = client.get_table(view_id)
            existing.view_query = view.view_query
            client.update_table(existing, ["view_query"])
        else:
            client.create_table(view)
    print(f"Prepared BigQuery dataset {dataset_ref} without seeding data")
    return client


def seed_demo(settings: Settings, bucket: storage.Bucket, client: bigquery.Client) -> None:
    snapshot_path = ROOT / "data" / "demo_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    replay_blob = bucket.blob("demo-replay/heatwave-hanoi.json")
    if not replay_blob.exists():
        replay_blob.upload_from_filename(str(snapshot_path), if_generation_match=0)
    raw_uri = f"gs://{bucket.name}/{replay_blob.name}"
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    snapshot_id = f"heatwave-{now:%Y%m%dT%H%M}-{uuid.uuid4().hex[:8]}"
    weather_rows, operation_rows, partner_rows, current_rows = [], [], [], []
    for zone in snapshot["zones"]:
        weather_rows.append({
            "scenario_id": "heatwave", "snapshot_id": snapshot_id,
            "zone_id": zone["zone_id"], "name": zone["name"],
            "latitude": zone["latitude"], "longitude": zone["longitude"],
            "temperature_c": zone["temperature_c"], "humidity_percent": zone["humidity_percent"],
            "heat_index_c": zone["heat_index_c"], "observed_at": now.isoformat(),
            "ingested_at": now.isoformat(), "source": "GCS demo replay",
            "raw_gcs_uri": raw_uri, "is_simulated": True,
        })
        operation_rows.append({
            "scenario_id": "heatwave", "snapshot_id": snapshot_id,
            "zone_id": zone["zone_id"], "observed_at": now.isoformat(),
            "active_drivers": zone["active_drivers"], "fresh_drivers": zone["fresh_drivers"],
            "exposed_2h": zone["exposed_2h"], "exposed_4h": zone["exposed_4h"],
            "forecast_requests_30m": zone["forecast_requests_30m"],
            "avg_platform_contribution_vnd": zone["avg_platform_contribution_vnd"],
            "avg_driver_earnings_vnd": zone["avg_driver_earnings_vnd"], "is_simulated": True,
        })
        partner_rows.append({
            "scenario_id": "heatwave", "zone_id": zone["zone_id"],
            "coolstop_name": zone["coolstop_name"],
            "coolstop_latitude": zone["coolstop_latitude"],
            "coolstop_longitude": zone["coolstop_longitude"],
            "sponsor_per_driver_vnd": 8_000, "is_simulated": True,
        })
        current_rows.append({
            "scenario_id": "heatwave", "snapshot_id": snapshot_id,
            "zone_id": zone["zone_id"], "name": zone["name"],
            "latitude": zone["latitude"], "longitude": zone["longitude"],
            "temperature_c": zone["temperature_c"], "humidity_percent": zone["humidity_percent"],
            "heat_index_c": zone["heat_index_c"], "observed_at": now.isoformat(),
            "weather_observed_at": now.isoformat(), "operations_observed_at": now.isoformat(),
            "refreshed_at": now.isoformat(),
            "active_drivers": zone["active_drivers"], "fresh_drivers": zone["fresh_drivers"],
            "exposed_2h": zone["exposed_2h"], "exposed_4h": zone["exposed_4h"],
            "forecast_requests_30m": zone["forecast_requests_30m"],
            "avg_platform_contribution_vnd": zone["avg_platform_contribution_vnd"],
            "avg_driver_earnings_vnd": zone["avg_driver_earnings_vnd"],
            "coolstop_name": zone["coolstop_name"],
            "coolstop_latitude": zone["coolstop_latitude"],
            "coolstop_longitude": zone["coolstop_longitude"],
            "source": "GCS demo replay + simulated fleet operations",
            "weather_is_simulated": True, "operations_is_simulated": True,
        })

    random.seed(42)
    demand_rows = []
    end = now.replace(minute=(now.minute // 15) * 15)
    start = end - timedelta(days=21)
    for scenario_id in ("heatwave", "live"):
        for zone in snapshot["zones"]:
            cursor = start
            base_15m = zone["forecast_requests_30m"] / 2
            while cursor < end:
                hour = cursor.hour + cursor.minute / 60
                peak = 1 + 0.38 * math.exp(-((hour - 8) / 2.2) ** 2) + 0.5 * math.exp(-((hour - 18) / 2.5) ** 2)
                overnight = 0.35 if hour < 5 else 1.0
                weekend = 0.88 if cursor.weekday() >= 5 else 1.0
                requests = max(1, round(base_15m * peak * overnight * weekend * random.uniform(0.9, 1.1)))
                demand_rows.append({
                    "scenario_id": scenario_id, "zone_id": zone["zone_id"],
                    "interval_start": cursor.isoformat(), "requests": requests,
                    "is_simulated": True,
                })
                cursor += timedelta(minutes=15)

    schemas = table_schemas()
    dataset = settings.dataset_path
    batches = [
        (
            "weather_observations", weather_rows,
            ["scenario_id", "snapshot_id", "zone_id"],
            "target.observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)",
        ),
        (
            "zone_operations", operation_rows,
            ["scenario_id", "snapshot_id", "zone_id"],
            "target.observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)",
        ),
        ("coolstop_partners", partner_rows, ["scenario_id", "zone_id"], None),
        (
            "demand_history", demand_rows,
            ["scenario_id", "zone_id", "interval_start"],
            "target.interval_start >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 22 DAY)",
        ),
        (settings.current_snapshot_table, current_rows, ["scenario_id", "zone_id"], None),
    ]
    for name, rows, keys, target_predicate in batches:
        schema_name = "zone_snapshots_current" if name == settings.current_snapshot_table else name
        merge_rows(
            client, f"{dataset}.{name}", rows, schemas[schema_name], keys,
            target_predicate=target_predicate,
        )
        print(f"Merged {len(rows):,} demo rows into {dataset}.{name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed-demo", action="store_true",
        help="Explicitly upsert the heatwave replay and simulated demand history.",
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    bucket = ensure_bucket(settings)
    client = ensure_bigquery(settings)
    if args.seed_demo:
        seed_demo(settings, bucket, client)
    else:
        print("No data seeded. Re-run with --seed-demo for the hackathon replay.")


if __name__ == "__main__":
    main()
