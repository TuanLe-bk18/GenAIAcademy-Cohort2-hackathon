#!/usr/bin/env python3
"""Train and materialize the AI decision layer in BigQuery."""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from heatsafe.config import Settings  # noqa: E402


def _query(client: bigquery.Client, sql: str, *, parameters: list | None = None) -> None:
    config = bigquery.QueryJobConfig(
        query_parameters=parameters or [],
        labels={"app": "heatsafe", "component": "ai-pipeline"},
    )
    client.query(sql, job_config=config).result()


def seed_driver_training_data(settings: Settings, client: bigquery.Client) -> None:
    dataset = settings.dataset_path
    sql = f"""
    DELETE FROM `{dataset}.driver_state_history` WHERE scenario_id = 'heatwave';
    DELETE FROM `{dataset}.driver_intervention_outcomes` WHERE scenario_id = 'heatwave';

    CREATE TEMP TABLE training_rows AS
    WITH zones AS (
      SELECT zone_id, heat_index_c, humidity_percent
      FROM `{dataset}.{settings.current_snapshot_table}`
      WHERE scenario_id = 'heatwave'
    ), grid AS (
      SELECT
        zone_id,
        heat_index_c,
        humidity_percent,
        driver_number,
        event_time,
        ABS(FARM_FINGERPRINT(CONCAT(zone_id, ':', CAST(driver_number AS STRING), ':', CAST(event_time AS STRING)))) AS seed
      FROM zones
      CROSS JOIN UNNEST(GENERATE_ARRAY(1, 50)) driver_number
      CROSS JOIN UNNEST(GENERATE_TIMESTAMP_ARRAY(
        TIMESTAMP_SUB(TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR), INTERVAL 21 DAY),
        TIMESTAMP_SUB(TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR), INTERVAL 15 MINUTE),
        INTERVAL 15 MINUTE
      )) event_time
    ), features AS (
      SELECT
        TO_HEX(MD5(CONCAT(zone_id, ':', CAST(driver_number AS STRING), ':', CAST(event_time AS STRING)))) state_id,
        event_time,
        TO_HEX(MD5(CONCAT('demo-driver:', zone_id, ':', CAST(driver_number AS STRING)))) driver_id_hash,
        zone_id,
        heat_index_c + (MOD(seed, 31) - 15) / 20.0 heat_index_c,
        LEAST(95.0, GREATEST(25.0, humidity_percent + MOD(seed, 17) - 8)) humidity_percent,
        30 + MOD(seed, 331) continuous_exposure_minutes,
        1 + MOD(DIV(seed, 7), 5) trips_60m,
        3.0 + MOD(DIV(seed, 11), 180) / 10.0 distance_km_60m,
        MOD(DIV(seed, 13), 46) rest_minutes_120m,
        15 + MOD(DIV(seed, 17), 166) hydration_gap_minutes,
        0.6 + MOD(DIV(seed, 19), 250) / 100.0 route_heat_load,
        0.5 + MOD(DIV(seed, 23), 220) / 100.0 workload_intensity,
        MOD(DIV(seed, 29), 9) action_index,
        seed
      FROM grid
    ), actions AS (
      SELECT
        *,
        IF(action_index = 0, 'NONE', 'SAFEPAUSE') action_type,
        IF(action_index = 0, 0, 15 * MOD(action_index - 1, 4)) pause_start_delay_minutes,
        IF(action_index = 0, 0, IF(action_index <= 4, 15, 30)) pause_duration_minutes
      FROM features
    ), labelled AS (
      SELECT
        *,
        1 / (1 + EXP(-(
          -6.0
          + 0.09 * (heat_index_c - 30)
          + 0.009 * (continuous_exposure_minutes - 60)
          + 0.28 * trips_60m
          + 0.012 * hydration_gap_minutes
          + 0.45 * route_heat_load
          + 0.35 * workload_intensity
          + IF(heat_index_c > 44 AND workload_intensity > 1.4, 1.1, 0)
          - 0.035 * pause_duration_minutes * EXP(-pause_start_delay_minutes / 60.0)
        ))) escalation_probability
      FROM actions
    )
    SELECT
      *,
      (MOD(ABS(FARM_FINGERPRINT(CONCAT(state_id, ':label'))), 10000) + 0.5) / 10000.0
        < escalation_probability AS heat_risk_escalation_60m
    FROM labelled;

    INSERT INTO `{dataset}.driver_state_history`
    SELECT
      state_id, 'heatwave', event_time, driver_id_hash, zone_id, heat_index_c,
      humidity_percent, continuous_exposure_minutes, trips_60m, distance_km_60m,
      rest_minutes_120m, hydration_gap_minutes, route_heat_load,
      workload_intensity, TRUE
    FROM training_rows;

    INSERT INTO `{dataset}.driver_intervention_outcomes`
    SELECT
      state_id, 'heatwave', event_time, driver_id_hash, zone_id, action_type,
      pause_start_delay_minutes, pause_duration_minutes,
      IF(action_type = 'SAFEPAUSE', pause_duration_minutes, 0),
      heat_risk_escalation_60m,
      -CAST(1200 * pause_duration_minutes AS INT64), TRUE
    FROM training_rows;
    """
    _query(client, sql)
    print("Seeded deterministic driver training history in BigQuery")


def train_model(settings: Settings, client: bigquery.Client) -> str:
    dataset = settings.dataset_path
    model_version = f"heat-risk-bqml-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    sql = f"""
    CREATE OR REPLACE MODEL `{dataset}.heat_risk_escalation_model`
    OPTIONS(
      MODEL_TYPE = 'BOOSTED_TREE_CLASSIFIER',
      INPUT_LABEL_COLS = ['heat_risk_escalation_60m'],
      DATA_SPLIT_METHOD = 'CUSTOM',
      DATA_SPLIT_COL = 'is_evaluation',
      ENABLE_GLOBAL_EXPLAIN = TRUE,
      MAX_ITERATIONS = 20,
      EARLY_STOP = TRUE
    ) AS
    SELECT
      state.heat_index_c,
      state.humidity_percent,
      state.continuous_exposure_minutes,
      state.trips_60m,
      state.distance_km_60m,
      state.rest_minutes_120m,
      state.hydration_gap_minutes,
      state.route_heat_load,
      state.workload_intensity,
      outcome.action_type,
      outcome.pause_start_delay_minutes,
      outcome.pause_duration_minutes,
      outcome.heat_risk_escalation_60m,
      state.event_time >= TIMESTAMP_SUB(TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR), INTERVAL 4 DAY)
        AS is_evaluation
    FROM `{dataset}.driver_state_history` state
    JOIN `{dataset}.driver_intervention_outcomes` outcome USING (state_id)
    WHERE state.scenario_id = 'heatwave';

    INSERT INTO `{dataset}.model_evaluations`
    SELECT
      @model_version, CURRENT_TIMESTAMP(), 'heat_risk_escalation_model',
      precision, recall, accuracy, f1_score, log_loss, roc_auc, TRUE
    FROM ML.EVALUATE(MODEL `{dataset}.heat_risk_escalation_model`);
    """
    _query(
        client,
        sql,
        parameters=[bigquery.ScalarQueryParameter("model_version", "STRING", model_version)],
    )
    print(f"Trained BigQuery ML model {model_version}")
    return model_version


def score_snapshot(
    settings: Settings,
    client: bigquery.Client,
    *,
    scenario: str,
    model_version: str | None = None,
) -> str:
    dataset = settings.dataset_path
    prediction_run_id = f"ai-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    if model_version is None:
        rows = list(
            client.query(
                f"""
                SELECT model_version FROM `{dataset}.model_evaluations`
                WHERE model_name = 'heat_risk_escalation_model'
                ORDER BY evaluated_at DESC LIMIT 1
                """
            ).result()
        )
        if not rows:
            raise RuntimeError("No evaluated heat-risk model is available for scoring")
        model_version = str(rows[0].model_version)
    sql = f"""
    DELETE FROM `{dataset}.driver_current_features` WHERE scenario_id = @scenario_id;
    INSERT INTO `{dataset}.driver_current_features`
    WITH zones AS (
      SELECT * FROM `{dataset}.{settings.current_snapshot_table}`
      WHERE scenario_id = @scenario_id
    ), drivers AS (
      SELECT
        zone.*,
        driver_number,
        ABS(FARM_FINGERPRINT(CONCAT(zone.zone_id, ':current:', CAST(driver_number AS STRING)))) seed
      FROM zones zone
      CROSS JOIN UNNEST(GENERATE_ARRAY(1, active_drivers)) driver_number
    )
    SELECT
      scenario_id, snapshot_id, observed_at,
      TO_HEX(MD5(CONCAT('current-driver:', zone_id, ':', CAST(driver_number AS STRING)))) driver_id_hash,
      zone_id,
      heat_index_c + (MOD(seed, 21) - 10) / 25.0,
      LEAST(95.0, GREATEST(25.0, humidity_percent + MOD(seed, 11) - 5)),
      CASE
        WHEN driver_number <= exposed_4h THEN 240 + MOD(seed, 151)
        WHEN driver_number <= exposed_2h THEN 120 + MOD(seed, 120)
        ELSE 20 + MOD(seed, 100)
      END,
      1 + MOD(DIV(seed, 7), 5),
      3.0 + MOD(DIV(seed, 11), 180) / 10.0,
      MOD(DIV(seed, 13), 46),
      15 + MOD(DIV(seed, 17), 166),
      0.6 + MOD(DIV(seed, 19), 250) / 100.0,
      0.5 + MOD(DIV(seed, 23), 220) / 100.0,
      TRUE
    FROM drivers;

    DELETE FROM `{dataset}.zone_demand_forecasts` WHERE scenario_id = @scenario_id;
    INSERT INTO `{dataset}.zone_demand_forecasts`
    SELECT
      @prediction_run_id, CURRENT_TIMESTAMP(), @scenario_id,
      (SELECT ANY_VALUE(snapshot_id) FROM `{dataset}.{settings.current_snapshot_table}`
       WHERE scenario_id = @scenario_id),
      zone_id,
      forecast_timestamp, CAST(ROUND(forecast_value) AS INT64),
      CAST(ROUND(prediction_interval_lower_bound) AS INT64),
      CAST(ROUND(prediction_interval_upper_bound) AS INT64),
      'TimesFM 2.0', COALESCE(ai_forecast_status, '')
    FROM AI.FORECAST(
      (SELECT zone_id, interval_start, requests
       FROM `{dataset}.demand_history`
       WHERE scenario_id = @scenario_id
         AND interval_start >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 21 DAY)),
      data_col => 'requests', timestamp_col => 'interval_start', id_cols => ['zone_id'],
      horizon => 16, confidence_level => 0.9, context_window => 2048
    );

    CREATE TEMP TABLE action_features AS
    SELECT
      features.scenario_id,
      features.snapshot_id,
      features.driver_id_hash,
      features.zone_id,
      features.continuous_exposure_minutes,
      features.is_simulated,
      features.heat_index_c,
      features.humidity_percent,
      features.trips_60m,
      features.distance_km_60m,
      features.rest_minutes_120m,
      features.hydration_gap_minutes,
      features.route_heat_load,
      features.workload_intensity,
      action.action_type,
      action.pause_start_delay_minutes,
      action.pause_duration_minutes
    FROM `{dataset}.driver_current_features` features
    CROSS JOIN UNNEST([
      STRUCT('NONE' AS action_type, 0 AS pause_start_delay_minutes, 0 AS pause_duration_minutes),
      STRUCT('SAFEPAUSE', 0, 15), STRUCT('SAFEPAUSE', 15, 15),
      STRUCT('SAFEPAUSE', 30, 15), STRUCT('SAFEPAUSE', 45, 15),
      STRUCT('SAFEPAUSE', 0, 30), STRUCT('SAFEPAUSE', 15, 30),
      STRUCT('SAFEPAUSE', 30, 30), STRUCT('SAFEPAUSE', 45, 30)
    ]) action;

    CREATE TEMP TABLE scored AS
    SELECT
      * EXCEPT(predicted_heat_risk_escalation_60m),
      (SELECT prob
       FROM UNNEST(predicted_heat_risk_escalation_60m_probs)
       WHERE label = TRUE) risk_probability
    FROM ML.PREDICT(MODEL `{dataset}.heat_risk_escalation_model`, TABLE action_features);

    CREATE TEMP TABLE explained AS
    SELECT
      driver_id_hash,
      TO_JSON(top_feature_attributions) top_factors_json
    FROM ML.EXPLAIN_PREDICT(
      MODEL `{dataset}.heat_risk_escalation_model`,
      (SELECT * FROM action_features WHERE action_type = 'NONE'),
      STRUCT(3 AS top_k_features, TRUE AS approx_feature_contrib)
    );

    DELETE FROM `{dataset}.driver_risk_predictions` WHERE scenario_id = @scenario_id;
    INSERT INTO `{dataset}.driver_risk_predictions`
    SELECT
      @prediction_run_id, CURRENT_TIMESTAMP(), @model_version,
      scored.scenario_id, scored.snapshot_id, scored.driver_id_hash, scored.zone_id,
      scored.continuous_exposure_minutes, scored.action_type,
      scored.pause_start_delay_minutes, scored.pause_duration_minutes,
      scored.risk_probability,
      baseline.risk_probability,
      explained.top_factors_json,
      scored.is_simulated
    FROM scored
    JOIN scored baseline
      ON baseline.driver_id_hash = scored.driver_id_hash
     AND baseline.action_type = 'NONE'
    LEFT JOIN explained
      ON explained.driver_id_hash = scored.driver_id_hash;
    """
    _query(
        client,
        sql,
        parameters=[
            bigquery.ScalarQueryParameter("scenario_id", "STRING", scenario),
            bigquery.ScalarQueryParameter("prediction_run_id", "STRING", prediction_run_id),
            bigquery.ScalarQueryParameter("model_version", "STRING", model_version),
        ],
    )
    print(f"Materialized AI predictions {prediction_run_id} for {scenario}")
    return prediction_run_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-training", action="store_true")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--score", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--scenario", default="heatwave", choices=("heatwave", "live"))
    args = parser.parse_args()
    settings = Settings.from_env()
    client = bigquery.Client(project=settings.project_id)
    run_seed = args.seed_training or args.all
    run_train = args.train or args.all
    run_score = args.score or args.all
    if not (run_seed or run_train or run_score):
        parser.error("Choose --seed-training, --train, --score, or --all")
    if run_seed:
        seed_driver_training_data(settings, client)
    model_version = train_model(settings, client) if run_train else None
    if run_score:
        score_snapshot(settings, client, scenario=args.scenario, model_version=model_version)


if __name__ == "__main__":
    main()
