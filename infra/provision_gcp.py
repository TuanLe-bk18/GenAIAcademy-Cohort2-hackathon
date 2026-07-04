#!/usr/bin/env python3
"""Provision and seed the minimal HeatSafe GCP data plane.

The script is idempotent for schemas and replaces only demo source tables. It
does not delete intervention history.
"""

from __future__ import annotations

import json
import math
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.cloud import bigquery, storage
from google.cloud import pubsub_v1

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from heatsafe.config import Settings  # noqa: E402


def table_schemas() -> dict[str, list[bigquery.SchemaField]]:
    return {
        "weather_observations": [
            bigquery.SchemaField("zone_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("latitude", "FLOAT64", mode="REQUIRED"),
            bigquery.SchemaField("longitude", "FLOAT64", mode="REQUIRED"),
            bigquery.SchemaField("temperature_c", "FLOAT64", mode="REQUIRED"),
            bigquery.SchemaField("humidity_percent", "FLOAT64", mode="REQUIRED"),
            bigquery.SchemaField("heat_index_c", "FLOAT64", mode="REQUIRED"),
            bigquery.SchemaField("observed_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("raw_gcs_uri", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("is_simulated", "BOOL", mode="REQUIRED"),
        ],
        "zone_operations": [
            bigquery.SchemaField("zone_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("observed_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("active_drivers", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("fresh_drivers", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("exposed_2h", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("exposed_4h", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("forecast_requests_30m", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("avg_platform_contribution_vnd", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("avg_driver_earnings_vnd", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("is_simulated", "BOOL", mode="REQUIRED"),
        ],
        "demand_history": [
            bigquery.SchemaField("zone_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("interval_start", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("requests", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("is_simulated", "BOOL", mode="REQUIRED"),
        ],
        "coolstop_partners": [
            bigquery.SchemaField("zone_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("coolstop_name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("coolstop_latitude", "FLOAT64", mode="REQUIRED"),
            bigquery.SchemaField("coolstop_longitude", "FLOAT64", mode="REQUIRED"),
            bigquery.SchemaField("sponsor_per_driver_vnd", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("is_simulated", "BOOL", mode="REQUIRED"),
        ],
        "intervention_proposals": [
            bigquery.SchemaField("proposal_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("zone_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("eligible_drivers", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("exposure_minutes_avoided", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("net_platform_cost_vnd", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("projected_fulfillment_rate", "FLOAT64", mode="REQUIRED"),
            bigquery.SchemaField("within_guardrails", "BOOL", mode="REQUIRED"),
            bigquery.SchemaField("proposal_json", "JSON", mode="REQUIRED"),
        ],
        "intervention_events": [
            bigquery.SchemaField("intervention_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("proposal_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("approved_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("approved_by", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("dispatch_status", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("zone_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("eligible_drivers", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("exposure_minutes_avoided", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("net_platform_cost_vnd", "INT64", mode="REQUIRED"),
        ],
    }


def ensure_bucket(settings: Settings) -> storage.Bucket:
    client = storage.Client(project=settings.project_id)
    bucket = client.lookup_bucket(settings.raw_bucket)
    if bucket is None:
        bucket = client.bucket(settings.raw_bucket)
        bucket.iam_configuration.uniform_bucket_level_access_enabled = True
        bucket = client.create_bucket(
            bucket, project=settings.project_id, location=settings.region
        )
        print(f"Created gs://{bucket.name}")
    else:
        print(f"Using gs://{bucket.name}")
    return bucket


def ensure_bigquery(settings: Settings) -> bigquery.Client:
    client = bigquery.Client(project=settings.project_id)
    dataset_ref = f"{settings.project_id}.{settings.dataset_id}"
    dataset = bigquery.Dataset(dataset_ref)
    dataset.location = settings.region
    client.create_dataset(dataset, exists_ok=True)

    partitioned = {
        "weather_observations": "observed_at",
        "zone_operations": "observed_at",
        "demand_history": "interval_start",
        "intervention_proposals": "created_at",
        "intervention_events": "approved_at",
    }
    clustered = {
        "weather_observations": ["zone_id"],
        "zone_operations": ["zone_id"],
        "demand_history": ["zone_id"],
        "intervention_proposals": ["zone_id"],
        "intervention_events": ["zone_id", "status"],
    }
    for name, schema in table_schemas().items():
        table = bigquery.Table(f"{dataset_ref}.{name}", schema=schema)
        if name in partitioned:
            table.time_partitioning = bigquery.TimePartitioning(field=partitioned[name])
        table.clustering_fields = clustered.get(name)
        client.create_table(table, exists_ok=True)

    live_order = """
      CASE
        WHEN NOT is_simulated
          AND observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 3 HOUR)
        THEN 0 ELSE 1
      END,
      observed_at DESC,
      ingested_at DESC
    """
    view_specs = {
        "zone_snapshots": ("", live_order),
        "zone_snapshots_live": ("", live_order),
        "zone_snapshots_heatwave": ("WHERE is_simulated", "observed_at DESC, ingested_at DESC"),
    }
    for view_name, (weather_filter, weather_order) in view_specs.items():
        view_id = f"{dataset_ref}.{view_name}"
        view = bigquery.Table(view_id)
        view.view_query = f"""
          WITH latest_weather AS (
            SELECT * EXCEPT(row_number) FROM (
              SELECT *, ROW_NUMBER() OVER(
                PARTITION BY zone_id ORDER BY {weather_order}
              ) row_number
              FROM `{dataset_ref}.weather_observations`
              {weather_filter}
            ) WHERE row_number = 1
          ), latest_ops AS (
            SELECT * EXCEPT(row_number) FROM (
              SELECT *, ROW_NUMBER() OVER(PARTITION BY zone_id ORDER BY observed_at DESC) row_number
              FROM `{dataset_ref}.zone_operations`
            ) WHERE row_number = 1
          )
          SELECT
            w.zone_id, w.name, w.latitude, w.longitude, w.temperature_c,
            w.humidity_percent, w.heat_index_c,
            LEAST(w.observed_at, o.observed_at) AS observed_at,
            o.active_drivers, o.fresh_drivers, o.exposed_2h, o.exposed_4h,
            o.forecast_requests_30m, o.avg_platform_contribution_vnd,
            o.avg_driver_earnings_vnd, c.coolstop_name, c.coolstop_latitude,
            c.coolstop_longitude, w.source, (w.is_simulated OR o.is_simulated) AS is_simulated
          FROM latest_weather w
          JOIN latest_ops o USING(zone_id)
          JOIN `{dataset_ref}.coolstop_partners` c USING(zone_id)
        """
        existing = client.get_table(view_id) if _table_exists(client, view_id) else None
        if existing:
            existing.view_query = view.view_query
            client.update_table(existing, ["view_query"])
        else:
            client.create_table(view)
    print(f"Prepared BigQuery dataset {dataset_ref}")
    return client


def _table_exists(client: bigquery.Client, table_id: str) -> bool:
    try:
        client.get_table(table_id)
        return True
    except Exception:
        return False


def upload_rows(
    client: bigquery.Client,
    table_id: str,
    rows: list[dict],
    schema: list[bigquery.SchemaField],
) -> None:
    config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(rows, table_id, job_config=config).result()
    print(f"Seeded {len(rows):,} rows into {table_id}")


def seed_demo(settings: Settings, bucket: storage.Bucket, client: bigquery.Client) -> None:
    snapshot_path = ROOT / "data" / "demo_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    replay_blob = bucket.blob("demo-replay/heatwave-hanoi.json")
    if not replay_blob.exists():
        replay_blob.upload_from_filename(str(snapshot_path), if_generation_match=0)
    raw_uri = f"gs://{bucket.name}/{replay_blob.name}"

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    weather_rows: list[dict] = []
    operation_rows: list[dict] = []
    partner_rows: list[dict] = []
    for zone in snapshot["zones"]:
        weather_rows.append(
            {
                "zone_id": zone["zone_id"], "name": zone["name"],
                "latitude": zone["latitude"], "longitude": zone["longitude"],
                "temperature_c": zone["temperature_c"],
                "humidity_percent": zone["humidity_percent"],
                "heat_index_c": zone["heat_index_c"], "observed_at": now.isoformat(),
                "ingested_at": now.isoformat(), "source": "GCS demo replay",
                "raw_gcs_uri": raw_uri, "is_simulated": True,
            }
        )
        operation_rows.append(
            {
                "zone_id": zone["zone_id"], "observed_at": now.isoformat(),
                "active_drivers": zone["active_drivers"], "fresh_drivers": zone["fresh_drivers"],
                "exposed_2h": zone["exposed_2h"], "exposed_4h": zone["exposed_4h"],
                "forecast_requests_30m": zone["forecast_requests_30m"],
                "avg_platform_contribution_vnd": zone["avg_platform_contribution_vnd"],
                "avg_driver_earnings_vnd": zone["avg_driver_earnings_vnd"],
                "is_simulated": True,
            }
        )
        partner_rows.append(
            {
                "zone_id": zone["zone_id"], "coolstop_name": zone["coolstop_name"],
                "coolstop_latitude": zone["coolstop_latitude"],
                "coolstop_longitude": zone["coolstop_longitude"],
                "sponsor_per_driver_vnd": 8_000, "is_simulated": True,
            }
        )

    random.seed(42)
    demand_rows: list[dict] = []
    end = now.replace(minute=(now.minute // 15) * 15)
    start = end - timedelta(days=21)
    for zone_index, zone in enumerate(snapshot["zones"]):
        base_15m = zone["forecast_requests_30m"] / 2
        cursor = start
        while cursor < end:
            hour = cursor.hour + cursor.minute / 60
            peak = 1 + 0.38 * math.exp(-((hour - 8) / 2.2) ** 2) + 0.5 * math.exp(-((hour - 18) / 2.5) ** 2)
            overnight = 0.35 if hour < 5 else 1.0
            weekend = 0.88 if cursor.weekday() >= 5 else 1.0
            noise = random.uniform(0.9, 1.1)
            requests = max(1, round(base_15m * peak * overnight * weekend * noise))
            demand_rows.append(
                {"zone_id": zone["zone_id"], "interval_start": cursor.isoformat(),
                 "requests": requests, "is_simulated": True}
            )
            cursor += timedelta(minutes=15)

    schemas = table_schemas()
    dataset = settings.dataset_path
    upload_rows(client, f"{dataset}.weather_observations", weather_rows, schemas["weather_observations"])
    upload_rows(client, f"{dataset}.zone_operations", operation_rows, schemas["zone_operations"])
    upload_rows(client, f"{dataset}.coolstop_partners", partner_rows, schemas["coolstop_partners"])
    upload_rows(client, f"{dataset}.demand_history", demand_rows, schemas["demand_history"])


def ensure_topic(settings: Settings) -> str:
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(settings.project_id, settings.dispatch_topic)
    try:
        publisher.get_topic(request={"topic": topic_path})
    except Exception:
        publisher.create_topic(request={"name": topic_path})
        print(f"Created {topic_path}")
    return topic_path


def main() -> None:
    settings = Settings.from_env()
    bucket = ensure_bucket(settings)
    client = ensure_bigquery(settings)
    seed_demo(settings, bucket, client)
    topic = ensure_topic(settings)
    print(f"Ready: {topic}")


if __name__ == "__main__":
    main()
