#!/usr/bin/env python3
"""Train and materialize the AI decision layer in BigQuery."""

from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from heatsafe.config import Settings  # noqa: E402


MAXIMUM_SCORING_QUERY_BYTES = 300_000_000


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
    feature_source: str = "legacy",
    simulation_run_id: str | None = None,
    tick_id: str | None = None,
    snapshot_id: str | None = None,
    simulation_time: datetime | None = None,
) -> str:
    if feature_source not in {"legacy", "simulation"}:
        raise ValueError("feature_source must be 'legacy' or 'simulation'")
    lineage = (simulation_run_id, tick_id, snapshot_id, simulation_time)
    if feature_source == "simulation":
        if scenario != "heatwave" or any(value is None for value in lineage):
            raise ValueError(
                "simulation scoring requires heatwave run/tick/snapshot/time lineage"
            )
        assert simulation_run_id and tick_id and snapshot_id and simulation_time
        if simulation_time.tzinfo is None:
            raise ValueError("simulation_time must be timezone-aware")
    elif any(value is not None for value in lineage):
        raise ValueError("legacy scoring does not accept simulation lineage")

    dataset = settings.dataset_path
    prediction_run_id = (
        ""
        if feature_source == "simulation"
        else f"ai-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    )
    if model_version is None:
        lookup_config = bigquery.QueryJobConfig(maximum_bytes_billed=50_000_000)
        rows = list(
            client.query(
                f"""
                SELECT model_version FROM `{dataset}.model_evaluations`
                WHERE model_name = 'heat_risk_escalation_model'
                ORDER BY evaluated_at DESC LIMIT 1
                """,
                job_config=lookup_config,
            ).result()
        )
        if not rows:
            raise RuntimeError("No evaluated heat-risk model is available for scoring")
        model_version = str(rows[0].model_version)
    if feature_source == "simulation":
        prediction_run_id = "sim-" + hashlib.sha256(
            (
                f"{simulation_run_id}:{tick_id}:{snapshot_id}:"
                f"{model_version}"
            ).encode()
        ).hexdigest()[:24]

    if feature_source == "simulation":
        feature_sql = f"""
    ASSERT (
      SELECT COUNT(*) = 10
        AND COUNT(DISTINCT snapshot_id) = 1
        AND ANY_VALUE(snapshot_id) = @snapshot_id
        AND COUNT(DISTINCT simulation_run_id) = 1
        AND ANY_VALUE(simulation_run_id) = @simulation_run_id
        AND COUNT(DISTINCT tick_id) = 1
        AND ANY_VALUE(tick_id) = @tick_id
      FROM `{dataset}.{settings.current_snapshot_table}`
      WHERE scenario_id = @scenario_id
    ) AS 'simulation snapshot must contain ten coherent zones';
    DELETE FROM `{dataset}.driver_current_features`
    WHERE scenario_id = @scenario_id
      AND simulation_run_id = @simulation_run_id;
    INSERT INTO `{dataset}.driver_current_features`
      (scenario_id, snapshot_id, observed_at, driver_id_hash, zone_id,
       heat_index_c, humidity_percent, continuous_exposure_minutes, trips_60m,
       distance_km_60m, rest_minutes_120m, hydration_gap_minutes,
       route_heat_load, workload_intensity, is_simulated, simulation_run_id,
       tick_id, driver_status, heat_dose_120m, acclimatization_class,
       generator_version, raw_features_json, clipped_fields_json,
       ood_reasons_json, feature_ood)
    SELECT
      @scenario_id, @snapshot_id, @simulation_time, driver.driver_id_hash,
      driver.zone_id,
      LEAST(50.55, GREATEST(33.05, zone.heat_index_c)),
      LEAST(68.0, GREATEST(46.0, zone.humidity_percent)),
      LEAST(360, GREATEST(30, driver.continuous_exposure_minutes)),
      LEAST(5, GREATEST(1, driver.trips_60m)),
      LEAST(20.9, GREATEST(3.0, driver.distance_km_60m)),
      LEAST(45, GREATEST(0, driver.rest_minutes_120m)),
      LEAST(180, GREATEST(15, driver.hydration_gap_minutes)),
      LEAST(3.09, GREATEST(0.60, driver.route_heat_load)),
      LEAST(2.69, GREATEST(0.50, driver.workload_intensity)),
      TRUE, @simulation_run_id, @tick_id, driver.status,
      driver.heat_dose_120m, driver.acclimatization_class,
      driver.generator_version,
      TO_JSON(STRUCT(
        zone.heat_index_c AS heat_index_c,
        zone.humidity_percent AS humidity_percent,
        driver.continuous_exposure_minutes AS continuous_exposure_minutes,
        driver.trips_60m AS trips_60m,
        driver.distance_km_60m AS distance_km_60m,
        driver.rest_minutes_120m AS rest_minutes_120m,
        driver.hydration_gap_minutes AS hydration_gap_minutes,
        driver.route_heat_load AS route_heat_load,
        driver.workload_intensity AS workload_intensity
      )),
      TO_JSON(ARRAY(
        SELECT field FROM UNNEST([
          IF(zone.heat_index_c NOT BETWEEN 33.05 AND 50.55,
             'heat_index_c', NULL),
          IF(zone.humidity_percent NOT BETWEEN 46 AND 68,
             'humidity_percent', NULL),
          IF(driver.continuous_exposure_minutes NOT BETWEEN 30 AND 360,
             'continuous_exposure_minutes', NULL),
          IF(driver.trips_60m NOT BETWEEN 1 AND 5, 'trips_60m', NULL),
          IF(driver.distance_km_60m NOT BETWEEN 3.0 AND 20.9,
             'distance_km_60m', NULL),
          IF(driver.rest_minutes_120m NOT BETWEEN 0 AND 45,
             'rest_minutes_120m', NULL),
          IF(driver.hydration_gap_minutes NOT BETWEEN 15 AND 180,
             'hydration_gap_minutes', NULL),
          IF(driver.route_heat_load NOT BETWEEN 0.60 AND 3.09,
             'route_heat_load', NULL),
          IF(driver.workload_intensity NOT BETWEEN 0.50 AND 2.69,
             'workload_intensity', NULL)
        ]) field WHERE field IS NOT NULL
      )),
      TO_JSON(ARRAY(
        SELECT reason FROM UNNEST([
          IF(zone.heat_index_c < 33.05, 'heat_index_c:LOW',
             IF(zone.heat_index_c > 50.55, 'heat_index_c:HIGH', NULL)),
          IF(zone.humidity_percent < 46, 'humidity_percent:LOW',
             IF(zone.humidity_percent > 68, 'humidity_percent:HIGH', NULL)),
          IF(driver.continuous_exposure_minutes < 30,
             'continuous_exposure_minutes:LOW',
             IF(driver.continuous_exposure_minutes > 360,
                'continuous_exposure_minutes:HIGH', NULL)),
          IF(driver.trips_60m < 1, 'trips_60m:LOW',
             IF(driver.trips_60m > 5, 'trips_60m:HIGH', NULL)),
          IF(driver.distance_km_60m < 3.0, 'distance_km_60m:LOW',
             IF(driver.distance_km_60m > 20.9, 'distance_km_60m:HIGH', NULL)),
          IF(driver.rest_minutes_120m < 0, 'rest_minutes_120m:LOW',
             IF(driver.rest_minutes_120m > 45, 'rest_minutes_120m:HIGH', NULL)),
          IF(driver.hydration_gap_minutes < 15, 'hydration_gap_minutes:LOW',
             IF(driver.hydration_gap_minutes > 180,
                'hydration_gap_minutes:HIGH', NULL)),
          IF(driver.route_heat_load < 0.60, 'route_heat_load:LOW',
             IF(driver.route_heat_load > 3.09, 'route_heat_load:HIGH', NULL)),
          IF(driver.workload_intensity < 0.50, 'workload_intensity:LOW',
             IF(driver.workload_intensity > 2.69,
                'workload_intensity:HIGH', NULL))
        ]) reason WHERE reason IS NOT NULL
      )),
      EXISTS (
        SELECT 1
        FROM `{dataset}.simulation_ticks` scoring_tick
        WHERE scoring_tick.simulation_run_id = @simulation_run_id
          AND scoring_tick.tick_id = @tick_id
          AND scoring_tick.error_code = 'MODEL_INPUT_OOD'
      )
    FROM `{dataset}.driver_simulation_state` driver
    JOIN `{dataset}.{settings.current_snapshot_table}` zone
      ON zone.scenario_id = driver.scenario_id
     AND zone.zone_id = driver.zone_id
     AND zone.simulation_run_id = driver.simulation_run_id
     AND zone.tick_id = driver.last_tick_id
    WHERE driver.scenario_id = @scenario_id
      AND driver.simulation_run_id = @simulation_run_id
      AND driver.last_tick_id = @tick_id
      AND driver.status IN ('IDLE', 'TO_PICKUP', 'ON_TRIP');

    MERGE `{dataset}.demand_history` target
    USING (
      WITH points AS (
        SELECT interval_start
        FROM UNNEST(GENERATE_TIMESTAMP_ARRAY(
          TIMESTAMP_SUB(@simulation_time, INTERVAL 30225 MINUTE),
          @simulation_time,
          INTERVAL 15 MINUTE
        )) interval_start
      )
      SELECT @scenario_id scenario_id, zone.zone_id, points.interval_start,
        CAST(GREATEST(1, ROUND(
          zone.forecast_requests_30m / 2.0
          * (0.58
             + 0.38 * EXP(-POW(EXTRACT(HOUR FROM points.interval_start AT TIME ZONE 'Asia/Ho_Chi_Minh') - 8, 2) / 7.0)
             + 0.22 * EXP(-POW(EXTRACT(HOUR FROM points.interval_start AT TIME ZONE 'Asia/Ho_Chi_Minh') - 12, 2) / 5.0)
             + 0.52 * EXP(-POW(EXTRACT(HOUR FROM points.interval_start AT TIME ZONE 'Asia/Ho_Chi_Minh') - 19, 2) / 8.0)
             + MOD(ABS(FARM_FINGERPRINT(CONCAT(zone.zone_id, ':', CAST(points.interval_start AS STRING)))), 9) / 100.0
            )
        )) AS INT64) requests,
        TRUE is_simulated, @simulation_run_id simulation_run_id,
        'forecast-context-seed' tick_id, 'timesfm-seed-v1' generator_version
      FROM `{dataset}.{settings.current_snapshot_table}` zone
      CROSS JOIN points
      WHERE zone.scenario_id = @scenario_id
        AND zone.simulation_run_id = @simulation_run_id
        AND zone.tick_id = @tick_id
    ) source
    ON target.simulation_run_id = source.simulation_run_id
      AND target.tick_id = source.tick_id
      AND target.zone_id = source.zone_id
      AND target.interval_start = source.interval_start
    WHEN NOT MATCHED THEN INSERT ROW;
        """
        forecast_anchor = "@simulation_time"
        forecast_filter = """
          AND simulation_run_id = @simulation_run_id
          AND interval_start BETWEEN TIMESTAMP_SUB(@simulation_time, INTERVAL 30225 MINUTE)
                                 AND @simulation_time
        """
        feature_cleanup = ""
        prediction_cleanup = ""
        tick_status_sql = f"""
    UPDATE `{dataset}.simulation_ticks`
    SET status = 'SCORED',
        error_code = IF(
          (SELECT LOGICAL_OR(feature_ood)
           FROM `{dataset}.driver_current_features`
           WHERE simulation_run_id = @simulation_run_id
             AND tick_id = @tick_id),
          'MODEL_INPUT_OOD',
          NULL
        )
    WHERE simulation_run_id = @simulation_run_id AND tick_id = @tick_id
      AND status IN ('SNAPSHOT_READY', 'SCORE_FAILED');
        """
    else:
        feature_sql = f"""
    DELETE FROM `{dataset}.driver_current_features` WHERE scenario_id = @scenario_id;
    INSERT INTO `{dataset}.driver_current_features`
      (scenario_id, snapshot_id, observed_at, driver_id_hash, zone_id,
       heat_index_c, humidity_percent, continuous_exposure_minutes, trips_60m,
       distance_km_60m, rest_minutes_120m, hydration_gap_minutes,
       route_heat_load, workload_intensity, is_simulated)
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
        """
        forecast_anchor = "CURRENT_TIMESTAMP()"
        forecast_filter = """
          AND interval_start >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 21 DAY)
        """
        feature_cleanup = (
            f"DELETE FROM `{dataset}.zone_demand_forecasts` "
            "WHERE scenario_id = @scenario_id;"
        )
        prediction_cleanup = (
            f"DELETE FROM `{dataset}.driver_risk_predictions` "
            "WHERE scenario_id = @scenario_id;"
        )
        tick_status_sql = ""

    sql = f"""
    {feature_sql}

    {feature_cleanup}
    CREATE TEMP TABLE forecast_rows AS
    SELECT
      @prediction_run_id AS prediction_run_id,
      CURRENT_TIMESTAMP() AS generated_at,
      @scenario_id AS scenario_id,
      COALESCE(@snapshot_id,
        (SELECT ANY_VALUE(snapshot_id)
         FROM `{dataset}.{settings.current_snapshot_table}`
         WHERE scenario_id = @scenario_id)) AS snapshot_id,
      zone_id AS zone_id,
      forecast_timestamp AS forecast_at,
      CAST(ROUND(forecast_value) AS INT64) AS predicted_requests,
      CAST(ROUND(prediction_interval_lower_bound) AS INT64) AS lower_bound,
      CAST(ROUND(prediction_interval_upper_bound) AS INT64) AS upper_bound,
      'TimesFM 2.5' AS model_version,
      COALESCE(ai_forecast_status, '') AS status,
      @simulation_run_id AS simulation_run_id,
      @tick_id AS tick_id,
      @generator_version AS generator_version
    FROM AI.FORECAST(
      (SELECT zone_id, interval_start,
              COALESCE(
                MAX(IF(tick_id != 'forecast-context-seed', requests, NULL)),
                MAX(requests)
              ) requests
       FROM `{dataset}.demand_history`
       WHERE scenario_id = @scenario_id
         {forecast_filter}
       GROUP BY zone_id, interval_start),
      data_col => 'requests', timestamp_col => 'interval_start', id_cols => ['zone_id'],
      horizon => 16, confidence_level => 0.9, context_window => 2048
    );
    ASSERT (
      SELECT COUNT(DISTINCT zone_id) = 10
        AND COUNTIF(status != '') = 0
        AND MIN(forecast_at) > {forecast_anchor}
      FROM forecast_rows
    ) AS 'TimesFM must return ten successful future zone series';
    MERGE `{dataset}.zone_demand_forecasts` target
    USING forecast_rows source
    ON target.prediction_run_id = source.prediction_run_id
      AND target.zone_id = source.zone_id
      AND target.forecast_at = source.forecast_at
    WHEN NOT MATCHED THEN INSERT ROW;

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
    ]) action
    WHERE features.scenario_id = @scenario_id
      AND (@simulation_run_id IS NULL
           OR (features.simulation_run_id = @simulation_run_id
               AND features.tick_id = @tick_id));

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

    {prediction_cleanup}
    CREATE TEMP TABLE prediction_rows AS
    SELECT
      @prediction_run_id AS prediction_run_id,
      CURRENT_TIMESTAMP() AS generated_at,
      @model_version AS model_version,
      scored.scenario_id AS scenario_id,
      scored.snapshot_id AS snapshot_id,
      scored.driver_id_hash AS driver_id_hash,
      scored.zone_id AS zone_id,
      scored.continuous_exposure_minutes AS continuous_exposure_minutes,
      scored.action_type AS action_type,
      scored.pause_start_delay_minutes AS pause_start_delay_minutes,
      scored.pause_duration_minutes AS pause_duration_minutes,
      scored.risk_probability AS risk_probability,
      baseline.risk_probability AS baseline_risk_probability,
      explained.top_factors_json AS top_factors_json,
      scored.is_simulated AS is_simulated,
      @simulation_run_id AS simulation_run_id,
      @tick_id AS tick_id,
      @generator_version AS generator_version
    FROM scored
    JOIN scored baseline
      ON baseline.driver_id_hash = scored.driver_id_hash
     AND baseline.action_type = 'NONE'
    LEFT JOIN explained
      ON explained.driver_id_hash = scored.driver_id_hash;
    ASSERT (
      SELECT COUNT(*) > 0
        AND COUNT(DISTINCT snapshot_id) = 1
        AND (@snapshot_id IS NULL OR ANY_VALUE(snapshot_id) = @snapshot_id)
      FROM prediction_rows
    ) AS 'predictions must match exactly one requested snapshot';
    MERGE `{dataset}.driver_risk_predictions` target
    USING prediction_rows source
    ON target.prediction_run_id = source.prediction_run_id
      AND target.driver_id_hash = source.driver_id_hash
      AND target.action_type = source.action_type
      AND target.pause_start_delay_minutes = source.pause_start_delay_minutes
      AND target.pause_duration_minutes = source.pause_duration_minutes
    WHEN NOT MATCHED THEN INSERT ROW;
    {tick_status_sql}
    """
    parameters = [
        bigquery.ScalarQueryParameter("scenario_id", "STRING", scenario),
        bigquery.ScalarQueryParameter("prediction_run_id", "STRING", prediction_run_id),
        bigquery.ScalarQueryParameter("model_version", "STRING", model_version),
        bigquery.ScalarQueryParameter("simulation_run_id", "STRING", simulation_run_id),
        bigquery.ScalarQueryParameter("tick_id", "STRING", tick_id),
        bigquery.ScalarQueryParameter("snapshot_id", "STRING", snapshot_id),
        bigquery.ScalarQueryParameter("simulation_time", "TIMESTAMP", simulation_time),
        bigquery.ScalarQueryParameter(
            "generator_version", "STRING",
            settings.simulation_generator_version if feature_source == "simulation" else None,
        ),
    ]
    try:
        config = bigquery.QueryJobConfig(
            query_parameters=parameters,
            maximum_bytes_billed=MAXIMUM_SCORING_QUERY_BYTES,
            labels={"app": "heatsafe", "component": "simulation-scoring"},
        )
        client.query(sql, job_config=config).result()
    except Exception:
        if feature_source == "simulation":
            failure_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "simulation_run_id", "STRING", simulation_run_id
                    ),
                    bigquery.ScalarQueryParameter("tick_id", "STRING", tick_id),
                ],
                maximum_bytes_billed=50_000_000,
            )
            client.query(
                f"""
UPDATE `{dataset}.simulation_ticks`
SET status = 'SCORE_FAILED', error_code = 'SNAPSHOT_SCORING_FAILED'
WHERE simulation_run_id = @simulation_run_id AND tick_id = @tick_id
  AND status IN ('SNAPSHOT_READY', 'SCORED', 'SCORE_FAILED')
""",
                job_config=failure_config,
            ).result()
        raise
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
