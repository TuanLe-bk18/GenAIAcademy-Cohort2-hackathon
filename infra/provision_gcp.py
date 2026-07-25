#!/usr/bin/env python3
"""Provision HeatSafe resources without mutating demo or intervention data by default."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.api_core.exceptions import NotFound
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
    weather_lineage = [
        _f("simulation_run_id", "STRING", "NULLABLE"),
        _f("tick_id", "STRING", "NULLABLE"),
        _f("source_observed_at", "TIMESTAMP", "NULLABLE"),
        _f("source_next_observed_at", "TIMESTAMP", "NULLABLE"),
        _f("source_interpolation_fraction", "FLOAT64", "NULLABLE"),
        _f("source_temperature_c", "FLOAT64", "NULLABLE"),
        _f("temperature_adjustment_c", "FLOAT64", "NULLABLE"),
        _f("station_peak_anchor_c", "FLOAT64", "NULLABLE"),
        _f("apparent_temperature_c", "FLOAT64", "NULLABLE"),
        _f("wind_speed_mps", "FLOAT64", "NULLABLE"),
        _f("wind_gust_mps", "FLOAT64", "NULLABLE"),
        _f("precipitation_mm", "FLOAT64", "NULLABLE"),
        _f("cloud_cover_pct", "FLOAT64", "NULLABLE"),
        _f("shortwave_radiation_wm2", "FLOAT64", "NULLABLE"),
        _f("utci_c", "FLOAT64", "NULLABLE"),
        _f("derivation_version", "STRING", "NULLABLE"),
        _f("generator_version", "STRING", "NULLABLE"),
    ]
    operation_lineage = [
        _f("simulation_run_id", "STRING", "NULLABLE"),
        _f("tick_id", "STRING", "NULLABLE"),
        _f("online_drivers", "INT64", "NULLABLE"),
        _f("idle_drivers", "INT64", "NULLABLE"),
        _f("to_pickup_drivers", "INT64", "NULLABLE"),
        _f("on_trip_drivers", "INT64", "NULLABLE"),
        _f("to_coolstop_drivers", "INT64", "NULLABLE"),
        _f("paused_drivers", "INT64", "NULLABLE"),
        _f("exposed_2_to_4h", "INT64", "NULLABLE"),
        _f("requests_15m", "INT64", "NULLABLE"),
        _f("matched_15m", "INT64", "NULLABLE"),
        _f("completed_15m", "INT64", "NULLABLE"),
        _f("cancelled_15m", "INT64", "NULLABLE"),
        _f("unfulfilled_15m", "INT64", "NULLABLE"),
        _f("median_wait_minutes", "FLOAT64", "NULLABLE"),
        _f("p90_wait_minutes", "FLOAT64", "NULLABLE"),
        _f("fulfillment_rate", "FLOAT64", "NULLABLE"),
        _f("generator_version", "STRING", "NULLABLE"),
    ]
    prediction_lineage = [
        _f("simulation_run_id", "STRING", "NULLABLE"),
        _f("tick_id", "STRING", "NULLABLE"),
        _f("generator_version", "STRING", "NULLABLE"),
    ]
    audit_lineage = [
        _f("scenario_id", "STRING", "NULLABLE"),
        _f("source_snapshot_id", "STRING", "NULLABLE"),
        _f("simulation_run_id", "STRING", "NULLABLE"),
        _f("source_tick_id", "STRING", "NULLABLE"),
        _f("expires_at", "TIMESTAMP", "NULLABLE"),
    ]
    return {
        "weather_observations": scenario + [
            _f("zone_id", "STRING"), _f("name", "STRING"),
            _f("latitude", "FLOAT64"), _f("longitude", "FLOAT64"),
            _f("temperature_c", "FLOAT64"), _f("humidity_percent", "FLOAT64"),
            _f("heat_index_c", "FLOAT64"), _f("observed_at", "TIMESTAMP"),
            _f("ingested_at", "TIMESTAMP"), _f("source", "STRING"),
            _f("raw_gcs_uri", "STRING"), _f("is_simulated", "BOOL"),
            *weather_lineage,
        ],
        "zone_operations": scenario + operations + operation_lineage,
        "demand_history": [
            _f("scenario_id", "STRING", "NULLABLE"), _f("zone_id", "STRING"),
            _f("interval_start", "TIMESTAMP"), _f("requests", "INT64"),
            _f("is_simulated", "BOOL"),
            *prediction_lineage,
        ],
        "driver_state_history": [
            _f("state_id", "STRING"), _f("scenario_id", "STRING"),
            _f("event_time", "TIMESTAMP"), _f("driver_id_hash", "STRING"),
            _f("zone_id", "STRING"), _f("heat_index_c", "FLOAT64"),
            _f("humidity_percent", "FLOAT64"),
            _f("continuous_exposure_minutes", "INT64"),
            _f("trips_60m", "INT64"), _f("distance_km_60m", "FLOAT64"),
            _f("rest_minutes_120m", "INT64"),
            _f("hydration_gap_minutes", "INT64"),
            _f("route_heat_load", "FLOAT64"),
            _f("workload_intensity", "FLOAT64"), _f("is_simulated", "BOOL"),
            _f("simulation_run_id", "STRING", "NULLABLE"),
            _f("tick_id", "STRING", "NULLABLE"),
            _f("driver_status", "STRING", "NULLABLE"),
            _f("heat_dose_120m", "FLOAT64", "NULLABLE"),
            _f("acclimatization_class", "STRING", "NULLABLE"),
            _f("current_order_id", "STRING", "NULLABLE"),
            _f("current_intervention_id", "STRING", "NULLABLE"),
            _f("earnings_60m_vnd", "INT64", "NULLABLE"),
            _f("platform_contribution_60m_vnd", "INT64", "NULLABLE"),
            _f("generator_version", "STRING", "NULLABLE"),
        ],
        "driver_intervention_outcomes": [
            _f("state_id", "STRING"), _f("scenario_id", "STRING"),
            _f("decision_at", "TIMESTAMP"), _f("driver_id_hash", "STRING"),
            _f("zone_id", "STRING"), _f("action_type", "STRING"),
            _f("pause_start_delay_minutes", "INT64"),
            _f("pause_duration_minutes", "INT64"),
            _f("completed_rest_minutes", "INT64"),
            _f("heat_risk_escalation_60m", "BOOL"),
            _f("earnings_delta_vnd", "INT64"), _f("is_simulated", "BOOL"),
        ],
        "driver_current_features": [
            _f("scenario_id", "STRING"), _f("snapshot_id", "STRING"),
            _f("observed_at", "TIMESTAMP"), _f("driver_id_hash", "STRING"),
            _f("zone_id", "STRING"), _f("heat_index_c", "FLOAT64"),
            _f("humidity_percent", "FLOAT64"),
            _f("continuous_exposure_minutes", "INT64"),
            _f("trips_60m", "INT64"), _f("distance_km_60m", "FLOAT64"),
            _f("rest_minutes_120m", "INT64"),
            _f("hydration_gap_minutes", "INT64"),
            _f("route_heat_load", "FLOAT64"),
            _f("workload_intensity", "FLOAT64"), _f("is_simulated", "BOOL"),
            _f("simulation_run_id", "STRING", "NULLABLE"),
            _f("tick_id", "STRING", "NULLABLE"),
            _f("driver_status", "STRING", "NULLABLE"),
            _f("heat_dose_120m", "FLOAT64", "NULLABLE"),
            _f("acclimatization_class", "STRING", "NULLABLE"),
            _f("generator_version", "STRING", "NULLABLE"),
            _f("raw_features_json", "JSON", "NULLABLE"),
            _f("clipped_fields_json", "JSON", "NULLABLE"),
            _f("ood_reasons_json", "JSON", "NULLABLE"),
            _f("feature_ood", "BOOL", "NULLABLE"),
        ],
        "driver_risk_predictions": [
            _f("prediction_run_id", "STRING"), _f("generated_at", "TIMESTAMP"),
            _f("model_version", "STRING"), _f("scenario_id", "STRING"),
            _f("snapshot_id", "STRING"), _f("driver_id_hash", "STRING"),
            _f("zone_id", "STRING"), _f("continuous_exposure_minutes", "INT64"),
            _f("action_type", "STRING"),
            _f("pause_start_delay_minutes", "INT64"),
            _f("pause_duration_minutes", "INT64"),
            _f("risk_probability", "FLOAT64"),
            _f("baseline_risk_probability", "FLOAT64"),
            _f("top_factors_json", "JSON"), _f("is_simulated", "BOOL"),
            *prediction_lineage,
        ],
        "zone_demand_forecasts": [
            _f("prediction_run_id", "STRING"), _f("generated_at", "TIMESTAMP"),
            _f("scenario_id", "STRING"), _f("snapshot_id", "STRING"),
            _f("zone_id", "STRING"),
            _f("forecast_at", "TIMESTAMP"), _f("predicted_requests", "INT64"),
            _f("lower_bound", "INT64"), _f("upper_bound", "INT64"),
            _f("model_version", "STRING"), _f("status", "STRING"),
            _f("forecast_source_tick_id", "STRING", "NULLABLE"),
            _f("forecast_source_snapshot_id", "STRING", "NULLABLE"),
            _f("forecast_source_prediction_run_id", "STRING", "NULLABLE"),
            _f("forecast_reused", "BOOL", "NULLABLE"),
            _f("forecast_age_minutes", "INT64", "NULLABLE"),
            *prediction_lineage,
        ],
        "model_evaluations": [
            _f("model_version", "STRING"), _f("evaluated_at", "TIMESTAMP"),
            _f("model_name", "STRING"), _f("precision", "FLOAT64", "NULLABLE"),
            _f("recall", "FLOAT64", "NULLABLE"),
            _f("accuracy", "FLOAT64", "NULLABLE"),
            _f("f1_score", "FLOAT64", "NULLABLE"),
            _f("log_loss", "FLOAT64", "NULLABLE"),
            _f("roc_auc", "FLOAT64", "NULLABLE"),
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
            *audit_lineage,
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
            *audit_lineage,
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
            *prediction_lineage,
            *operation_lineage[2:-1],
        ],
        "simulation_scenario_locks": [
            _f("scenario_id", "STRING"),
            _f("active_simulation_run_id", "STRING", "NULLABLE"),
            _f("generation", "INT64"),
            _f("updated_at", "TIMESTAMP"),
        ],
        "simulation_runs": [
            _f("simulation_run_id", "STRING"), _f("scenario_id", "STRING"),
            _f("scenario_version", "STRING"), _f("seed", "INT64"),
            _f("status", "STRING"), _f("simulation_start_at", "TIMESTAMP"),
            _f("simulation_end_at", "TIMESTAMP"),
            _f("next_simulation_at", "TIMESTAMP"), _f("tick_minutes", "INT64"),
            _f("speed_multiplier", "FLOAT64"),
            _f("last_published_tick_index", "INT64", "NULLABLE"),
            _f("last_completed_tick_index", "INT64", "NULLABLE"),
            _f("pending_score_tick_id", "STRING", "NULLABLE"),
            _f("risk_model_version", "STRING", "NULLABLE"),
            _f("forecast_context_version", "STRING", "NULLABLE"),
            _f("forecast_context_seeded_at", "TIMESTAMP", "NULLABLE"),
            _f("forecast_context_point_count", "INT64", "NULLABLE"),
            _f("config_json", "JSON"), _f("created_at", "TIMESTAMP"),
            _f("updated_at", "TIMESTAMP"), _f("is_simulated", "BOOL"),
        ],
        "simulation_ticks": [
            _f("simulation_run_id", "STRING"), _f("scenario_id", "STRING"),
            _f("tick_id", "STRING"), _f("tick_index", "INT64"),
            _f("simulation_time", "TIMESTAMP"), _f("snapshot_id", "STRING"),
            _f("status", "STRING"), _f("lease_owner", "STRING", "NULLABLE"),
            _f("lease_expires_at", "TIMESTAMP", "NULLABLE"),
            _f("input_checksum", "STRING", "NULLABLE"),
            _f("output_checksum", "STRING", "NULLABLE"),
            _f("driver_count", "INT64", "NULLABLE"),
            _f("order_event_count", "INT64", "NULLABLE"),
            _f("started_at", "TIMESTAMP", "NULLABLE"),
            _f("finished_at", "TIMESTAMP", "NULLABLE"),
            _f("error_code", "STRING", "NULLABLE"),
            _f("error_message", "STRING", "NULLABLE"),
            _f("input_manifest_json", "JSON", "NULLABLE"),
            _f("input_manifest_checksum", "STRING", "NULLABLE"),
            _f("input_frozen_at", "TIMESTAMP", "NULLABLE"),
            _f("checkpoint_object_name", "STRING", "NULLABLE"),
            _f("checkpoint_format_version", "STRING", "NULLABLE"),
            _f("checkpoint_generation", "INT64", "NULLABLE"),
            _f("checkpoint_compressed_size", "INT64", "NULLABLE"),
            _f("checkpoint_expanded_size", "INT64", "NULLABLE"),
            _f("checkpoint_payload_sha256", "STRING", "NULLABLE"),
            _f("checkpoint_state_checksum", "STRING", "NULLABLE"),
            _f("state_mode", "STRING", "NULLABLE"),
            _f("execution_mode", "STRING", "NULLABLE"),
            _f("execution_reason_codes_json", "JSON", "NULLABLE"),
            _f("low_risk_streak", "INT64", "NULLABLE"),
            _f("recovery_streak", "INT64", "NULLABLE"),
            _f("scoring_outcome", "STRING", "NULLABLE"),
            _f("forecast_source_tick_id", "STRING", "NULLABLE"),
            _f("forecast_source_snapshot_id", "STRING", "NULLABLE"),
            _f("forecast_source_prediction_run_id", "STRING", "NULLABLE"),
            _f("forecast_generated_at", "TIMESTAMP", "NULLABLE"),
            _f("generator_version", "STRING"), _f("is_simulated", "BOOL"),
        ],
        "driver_simulation_state": [
            _f("simulation_run_id", "STRING"), _f("scenario_id", "STRING"),
            _f("driver_id_hash", "STRING"), _f("last_tick_id", "STRING"),
            _f("event_time", "TIMESTAMP"), _f("zone_id", "STRING"),
            _f("latitude", "FLOAT64"), _f("longitude", "FLOAT64"),
            _f("status", "STRING"),
            _f("shift_started_at", "TIMESTAMP", "NULLABLE"),
            _f("shift_ends_at", "TIMESTAMP", "NULLABLE"),
            _f("current_order_id", "STRING", "NULLABLE"),
            _f("current_intervention_id", "STRING", "NULLABLE"),
            _f("online_minutes_24h", "INT64"), _f("trips_60m", "INT64"),
            _f("distance_km_60m", "FLOAT64"),
            _f("workload_intensity", "FLOAT64"),
            _f("continuous_exposure_minutes", "INT64"),
            _f("heat_dose_120m", "FLOAT64"),
            _f("rest_minutes_120m", "INT64"),
            _f("hydration_gap_minutes", "INT64"),
            _f("route_heat_load", "FLOAT64"),
            _f("acclimatization_class", "STRING"),
            _f("earnings_60m_vnd", "INT64"),
            _f("platform_contribution_60m_vnd", "INT64"),
            _f("generator_version", "STRING"), _f("is_simulated", "BOOL"),
            _f("updated_at", "TIMESTAMP"),
        ],
        "order_events": [
            _f("event_id", "STRING"), _f("simulation_run_id", "STRING"),
            _f("tick_id", "STRING"), _f("scenario_id", "STRING"),
            _f("order_id", "STRING"), _f("event_time", "TIMESTAMP"),
            _f("event_type", "STRING"), _f("status", "STRING"),
            _f("driver_id_hash", "STRING", "NULLABLE"),
            _f("origin_zone_id", "STRING"), _f("destination_zone_id", "STRING"),
            _f("zone_id", "STRING"), _f("requested_at", "TIMESTAMP"),
            _f("accepted_at", "TIMESTAMP", "NULLABLE"),
            _f("pickup_at", "TIMESTAMP", "NULLABLE"),
            _f("dropoff_at", "TIMESTAMP", "NULLABLE"),
            _f("cancelled_at", "TIMESTAMP", "NULLABLE"),
            _f("distance_km", "FLOAT64", "NULLABLE"),
            _f("estimated_duration_minutes", "FLOAT64", "NULLABLE"),
            _f("actual_duration_minutes", "FLOAT64", "NULLABLE"),
            _f("wait_minutes", "FLOAT64", "NULLABLE"),
            _f("fare_vnd", "INT64", "NULLABLE"),
            _f("driver_pay_vnd", "INT64", "NULLABLE"),
            _f("platform_contribution_vnd", "INT64", "NULLABLE"),
            _f("generator_version", "STRING"), _f("is_simulated", "BOOL"),
        ],
        "driver_intervention_events": [
            _f("event_id", "STRING"), _f("simulation_run_id", "STRING"),
            _f("tick_id", "STRING"), _f("scenario_id", "STRING"),
            _f("intervention_id", "STRING"), _f("proposal_id", "STRING"),
            _f("driver_id_hash", "STRING"), _f("zone_id", "STRING"),
            _f("event_time", "TIMESTAMP"), _f("event_type", "STRING"),
            _f("pause_start_delay_minutes", "INT64", "NULLABLE"),
            _f("planned_duration_minutes", "INT64", "NULLABLE"),
            _f("completed_rest_minutes", "INT64", "NULLABLE"),
            _f("coolstop_name", "STRING", "NULLABLE"),
            _f("baseline_risk_probability", "FLOAT64", "NULLABLE"),
            _f("action_risk_probability", "FLOAT64", "NULLABLE"),
            _f("earnings_delta_vnd", "INT64", "NULLABLE"),
            _f("is_simulated", "BOOL"), _f("generator_version", "STRING"),
        ],
        "simulation_control_events": [
            _f("control_event_id", "STRING"), _f("scenario_id", "STRING"),
            _f("simulation_run_id", "STRING"), _f("source_tick_id", "STRING"),
            _f("source_snapshot_id", "STRING"), _f("proposal_id", "STRING"),
            _f("proposal_payload_checksum", "STRING"),
            _f("status", "STRING"),
            _f("selected_driver_count", "INT64"), _f("requested_by", "STRING"),
            _f("actor_type", "STRING"), _f("request_execution_id", "STRING"),
            _f("created_at", "TIMESTAMP"),
            _f("authorization_expires_at", "TIMESTAMP"),
            _f("valid_from_simulation_at", "TIMESTAMP"),
            _f("valid_until_simulation_at", "TIMESTAMP"),
            _f("max_selected_drivers", "INT64"), _f("is_simulated", "BOOL"),
            _f("generator_version", "STRING"),
        ],
        "simulation_control_consumptions": [
            _f("consumption_id", "STRING"), _f("control_event_id", "STRING"),
            _f("scenario_id", "STRING"), _f("simulation_run_id", "STRING"),
            _f("consumed_by_tick_id", "STRING", "NULLABLE"),
            _f("outcome", "STRING"), _f("recorded_at", "TIMESTAMP"),
            _f("rejection_reason", "STRING", "NULLABLE"),
            _f("generator_version", "STRING"), _f("is_simulated", "BOOL"),
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


def ensure_checkpoint_bucket(settings: Settings) -> storage.Bucket:
    """Create/read back the dedicated, non-public, regional checkpoint bucket."""
    client = storage.Client(project=settings.project_id)
    bucket = client.lookup_bucket(settings.simulation_checkpoint_bucket)
    if bucket is None:
        bucket = client.bucket(settings.simulation_checkpoint_bucket)
        bucket.iam_configuration.uniform_bucket_level_access_enabled = True
        bucket.iam_configuration.public_access_prevention = "enforced"
        bucket.labels = {
            "app": "heatsafe",
            "env": "demo",
            "component": "simulation-checkpoint",
            "managed_by": "scripts",
        }
        bucket = client.create_bucket(
            bucket, project=settings.project_id, location=settings.region
        )
    bucket.reload()
    if bucket.location.lower() != settings.region.lower():
        raise RuntimeError(
            "checkpoint bucket location conflicts with simulation region"
        )
    bucket.iam_configuration.uniform_bucket_level_access_enabled = True
    bucket.iam_configuration.public_access_prevention = "enforced"
    bucket.labels = {
        **dict(bucket.labels or {}),
        "app": "heatsafe",
        "env": "demo",
        "component": "simulation-checkpoint",
        "managed_by": "scripts",
    }
    rules = [
        rule
        for rule in bucket.lifecycle_rules
        if rule.get("action", {}).get("type") != "Delete"
    ]
    rules.append({"action": {"type": "Delete"}, "condition": {"age": 35}})
    bucket.lifecycle_rules = rules
    bucket.patch()
    bucket.reload()
    if not bucket.iam_configuration.uniform_bucket_level_access_enabled:
        raise RuntimeError("checkpoint bucket uniform access is not enabled")
    if bucket.iam_configuration.public_access_prevention != "enforced":
        raise RuntimeError("checkpoint bucket public access prevention is not enforced")
    if not any(
        rule.get("action", {}).get("type") == "Delete"
        and rule.get("condition", {}).get("age") == 35
        for rule in bucket.lifecycle_rules
    ):
        raise RuntimeError("checkpoint bucket 35-day lifecycle is missing")
    return bucket


def _table_exists(client: bigquery.Client, table_id: str) -> bool:
    try:
        client.get_table(table_id)
        return True
    except NotFound:
        return False


def _normalized_type(field_type: str) -> str:
    return {
        "INTEGER": "INT64",
        "FLOAT": "FLOAT64",
        "BOOLEAN": "BOOL",
        "RECORD": "STRUCT",
    }.get(field_type.upper(), field_type.upper())


def _partition_signature(table: bigquery.Table) -> tuple[str, str] | None:
    partitioning = table.time_partitioning
    if partitioning is None:
        return None
    return (partitioning.field or "", str(partitioning.type_ or "DAY").upper())


def _ensure_table(
    client: bigquery.Client,
    table_id: str,
    schema: list[bigquery.SchemaField],
    partition_field: str | None,
    clustering: list[str] | None,
    *,
    preserve_existing_layout: bool = False,
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
    existing = {field.name: field for field in table.schema}
    desired = {field.name: field for field in schema}
    if len(desired) != len(schema):
        raise ValueError(f"{table_id} desired schema contains duplicate field names")
    conflicts = []
    for name in existing.keys() & desired.keys():
        actual_field = existing[name]
        desired_field = desired[name]
        actual_signature = (
            _normalized_type(actual_field.field_type),
            actual_field.mode.upper(),
        )
        desired_signature = (
            _normalized_type(desired_field.field_type),
            desired_field.mode.upper(),
        )
        if actual_signature != desired_signature:
            conflicts.append(
                f"{name}: existing={actual_signature} desired={desired_signature}"
            )
    if conflicts:
        raise RuntimeError(f"{table_id} schema conflict: {'; '.join(conflicts)}")
    expected_partition = (partition_field, "DAY") if partition_field else None
    actual_partition = _partition_signature(table)
    if actual_partition != expected_partition and not preserve_existing_layout:
        raise RuntimeError(
            f"{table_id} partition conflict: "
            f"existing={actual_partition} desired={expected_partition}"
        )
    actual_clustering = list(table.clustering_fields or [])
    expected_clustering = list(clustering or [])
    if actual_clustering != expected_clustering and not preserve_existing_layout:
        raise RuntimeError(
            f"{table_id} clustering conflict: "
            f"existing={actual_clustering} desired={expected_clustering}"
        )
    missing = [field for field in schema if field.name not in existing]
    required_missing = [field.name for field in missing if field.mode == "REQUIRED"]
    if required_missing:
        raise RuntimeError(
            f"{table_id} cannot add REQUIRED fields to an existing table: "
            f"{required_missing}"
        )
    fields = []
    if missing:
        table.schema = [*table.schema, *missing]
        fields.append("schema")
    original_labels = dict(table.labels or {})
    labels = dict(original_labels)
    labels.update({"app": "heatsafe", "env": "demo", "managed_by": "scripts"})
    table.labels = labels
    if labels != original_labels:
        fields.append("labels")
    if fields:
        client.update_table(table, fields)


def ensure_bigquery(
    settings: Settings,
    *,
    include_views: bool = True,
    preserve_existing_layout: bool = False,
) -> bigquery.Client:
    client = bigquery.Client(project=settings.project_id)
    dataset_ref = settings.dataset_path
    dataset = bigquery.Dataset(dataset_ref)
    dataset.location = settings.region
    dataset.labels = {"app": "heatsafe", "env": "demo", "managed_by": "scripts"}
    client.create_dataset(dataset, exists_ok=True)
    current_dataset = client.get_dataset(dataset_ref)
    if current_dataset.location.lower() != settings.region.lower():
        raise RuntimeError(
            f"{dataset_ref} location conflict: existing={current_dataset.location!r} "
            f"desired={settings.region!r}"
        )
    current_dataset.labels = dataset.labels
    client.update_dataset(current_dataset, ["labels"])

    partitioned = {
        "weather_observations": "observed_at", "zone_operations": "observed_at",
        "demand_history": "interval_start", "intervention_proposals": "created_at",
        "intervention_events": "approved_at", "driver_state_history": "event_time",
        "driver_intervention_outcomes": "decision_at",
        "driver_risk_predictions": "generated_at",
        "zone_demand_forecasts": "generated_at", "model_evaluations": "evaluated_at",
        "simulation_runs": "created_at", "simulation_ticks": "simulation_time",
        "order_events": "event_time",
        "driver_intervention_events": "event_time",
        "simulation_control_events": "created_at",
        "simulation_control_consumptions": "recorded_at",
    }
    clustered = {
        "weather_observations": ["scenario_id", "zone_id"],
        "zone_operations": ["scenario_id", "zone_id"],
        "demand_history": ["scenario_id", "zone_id"],
        "driver_state_history": ["scenario_id", "zone_id", "driver_id_hash"],
        "driver_intervention_outcomes": ["scenario_id", "zone_id", "driver_id_hash"],
        "driver_current_features": ["scenario_id", "zone_id", "driver_id_hash"],
        "driver_risk_predictions": ["scenario_id", "zone_id", "prediction_run_id"],
        "zone_demand_forecasts": ["scenario_id", "zone_id"],
        "model_evaluations": ["model_name"],
        "intervention_proposals": ["zone_id"],
        "intervention_events": ["zone_id", "status"],
        "zone_snapshots_current": ["scenario_id", "zone_id"],
        "simulation_scenario_locks": ["scenario_id"],
        "simulation_runs": ["scenario_id", "status"],
        "simulation_ticks": ["scenario_id", "simulation_run_id", "status"],
        "driver_simulation_state": [
            "scenario_id", "simulation_run_id", "zone_id", "driver_id_hash"
        ],
        "order_events": [
            "scenario_id", "simulation_run_id", "zone_id", "order_id"
        ],
        "driver_intervention_events": [
            "scenario_id", "simulation_run_id", "intervention_id", "driver_id_hash"
        ],
        "simulation_control_events": [
            "scenario_id", "simulation_run_id", "status", "proposal_id"
        ],
        "simulation_control_consumptions": [
            "scenario_id", "simulation_run_id", "outcome", "control_event_id"
        ],
    }
    for name, schema in table_schemas().items():
        schema_names = {field.name for field in schema}
        missing_cluster_fields = set(clustered.get(name, [])) - schema_names
        if missing_cluster_fields:
            raise ValueError(
                f"{name} clustering fields are absent from schema: "
                f"{sorted(missing_cluster_fields)}"
            )
        partition_field = partitioned.get(name)
        if partition_field and partition_field not in schema_names:
            raise ValueError(
                f"{name} partition field is absent from schema: {partition_field}"
            )
        _ensure_table(
            client, f"{dataset_ref}.{name}", schema,
            partition_field, clustered.get(name),
            preserve_existing_layout=preserve_existing_layout,
        )

    view_specs = {
        "zone_snapshots": "SELECT * FROM `{table}`",
        "zone_snapshots_live": "SELECT * FROM `{table}` WHERE scenario_id = 'live'",
        "zone_snapshots_heatwave": "SELECT * FROM `{table}` WHERE scenario_id = 'heatwave'",
    }
    for view_name, query_template in view_specs.items() if include_views else ():
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


def print_schema_readback(
    client: bigquery.Client,
    settings: Settings,
) -> None:
    readback = {}
    for name in table_schemas():
        table = client.get_table(f"{settings.dataset_path}.{name}")
        readback[name] = {
            "schema": [
                {
                    "name": field.name,
                    "type": _normalized_type(field.field_type),
                    "mode": field.mode,
                }
                for field in table.schema
            ],
            "partition": _partition_signature(table),
            "clustering": list(table.clustering_fields or []),
            "rows": table.num_rows,
        }
    print(json.dumps(readback, ensure_ascii=False, indent=2, sort_keys=True))


def seed_demo(settings: Settings, bucket: storage.Bucket, client: bigquery.Client) -> None:
    snapshot_path = ROOT / "data" / "demo_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    replay_blob = bucket.blob("demo-replay/heatwave-hanoi-hoan-kiem-peak-v2.json")
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
        update_fields = None
        if name == settings.current_snapshot_table:
            update_fields = [
                field.name for field in schemas[schema_name]
                if field.name not in keys
            ]
        merge_rows(
            client, f"{dataset}.{name}", rows, schemas[schema_name], keys,
            update_fields=update_fields,
            target_predicate=target_predicate,
        )
        print(f"Merged {len(rows):,} demo rows into {dataset}.{name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed-demo", action="store_true",
        help="Explicitly upsert the heatwave replay and simulated demand history.",
    )
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Provision and read back tables only; never access Cloud Storage.",
    )
    parser.add_argument(
        "--schema-only-current",
        action="store_true",
        help=(
            "Explicitly provision the configured dataset additively; never access "
            "Cloud Storage or seed data."
        ),
    )
    parser.add_argument(
        "--bootstrap-checkpoints",
        action="store_true",
        help="Provision/read back only the dedicated checkpoint bucket.",
    )
    parser.add_argument(
        "--dataset",
        help="Explicit disposable dataset ID for --schema-only.",
    )
    parser.add_argument(
        "--cleanup-schema-only",
        action="store_true",
        help="Delete only the explicit disposable --dataset and its contents.",
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.bootstrap_checkpoints:
        if (
            args.seed_demo
            or args.schema_only
            or args.schema_only_current
            or args.dataset
            or args.cleanup_schema_only
        ):
            parser.error("--bootstrap-checkpoints must be used alone")
        bucket = ensure_checkpoint_bucket(settings)
        print(
            f"Prepared gs://{bucket.name} in {bucket.location} with "
            "uniform access, public access prevention, and 35-day lifecycle"
        )
        return
    if args.schema_only and args.schema_only_current:
        parser.error("--schema-only and --schema-only-current are mutually exclusive")
    if args.schema_only_current:
        if args.seed_demo or args.dataset or args.cleanup_schema_only:
            parser.error(
                "--schema-only-current cannot be combined with --seed-demo, "
                "--dataset, or --cleanup-schema-only"
            )
        ensure_bigquery(settings, preserve_existing_layout=True)
        print(
            f"Provisioned {len(table_schemas())} configured tables additively "
            f"in {settings.dataset_path}; existing physical layouts preserved"
        )
        return
    if args.schema_only:
        if args.seed_demo:
            parser.error("--schema-only cannot be combined with --seed-demo")
        if not args.dataset:
            parser.error("--schema-only requires an explicit --dataset")
        if not (
            args.dataset.startswith("heatsafe_phase1_")
            or args.dataset.startswith("heatsafe_p0_test_")
        ):
            parser.error(
                "--schema-only dataset must start with heatsafe_phase1_ "
                "or heatsafe_p0_test_"
            )
        if args.dataset == settings.dataset_id:
            parser.error("--schema-only refuses the configured production dataset")
        schema_settings = replace(settings, dataset_id=args.dataset)
        if args.cleanup_schema_only:
            client = bigquery.Client(project=schema_settings.project_id)
            client.delete_dataset(
                schema_settings.dataset_path,
                delete_contents=True,
                not_found_ok=True,
            )
            print(f"Deleted disposable dataset {schema_settings.dataset_path}")
            return
        client = ensure_bigquery(schema_settings, include_views=False)
        print_schema_readback(client, schema_settings)
        return
    if args.dataset or args.cleanup_schema_only:
        parser.error("--dataset and --cleanup-schema-only require --schema-only")
    bucket = ensure_bucket(settings)
    client = ensure_bigquery(settings)
    if args.seed_demo:
        seed_demo(settings, bucket, client)
    else:
        print("No data seeded. Re-run with --seed-demo for the hackathon replay.")


if __name__ == "__main__":
    main()
