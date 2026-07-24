#!/usr/bin/env python3
"""Run exact-snapshot Phase 4 scoring in a disposable BigQuery dataset."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google.cloud import bigquery  # noqa: E402

from heatsafe.config import Settings  # noqa: E402
from heatsafe.simulation.control import BigQueryControlWriter  # noqa: E402
from heatsafe.simulation.repository import BigQuerySimulationRepository  # noqa: E402
from infra.ml_pipeline import (  # noqa: E402
    MAXIMUM_SCORING_QUERY_BYTES,
    score_snapshot,
)
from infra.provision_gcp import ensure_bigquery  # noqa: E402


PROBE_PREFIX = "heatsafe_phase4_probe_"
MAXIMUM_BYTES_BILLED = 250_000_000


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project", required=True)
    result.add_argument("--dataset", required=True)
    result.add_argument("--source-model-dataset", default="heatsafe_data")
    result.add_argument("--execute", action="store_true")
    return result


def _require_disposable(dataset: str) -> None:
    if not dataset.startswith(PROBE_PREFIX):
        raise ValueError(
            f"dataset must start with {PROBE_PREFIX!r}; refusing shared target"
        )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    _require_disposable(args.dataset)
    if not args.execute:
        print(json.dumps({
            "dry_run": True,
            "project": args.project,
            "dataset": args.dataset,
            "source_model": (
                f"{args.project}:{args.source_model_dataset}."
                "heat_risk_escalation_model"
            ),
            "shared_mutation": False,
            "maximum_bytes_billed": MAXIMUM_BYTES_BILLED,
            "scoring_maximum_bytes_billed": MAXIMUM_SCORING_QUERY_BYTES,
        }, sort_keys=True))
        return 0

    settings = Settings(project_id=args.project, dataset_id=args.dataset)
    client = ensure_bigquery(settings, include_views=False)
    destination_model = (
        f"{args.project}:{args.dataset}.heat_risk_escalation_model"
    )
    source_model = (
        f"{args.project}:{args.source_model_dataset}.heat_risk_escalation_model"
    )
    try:
        subprocess.run(
            [
                "bq",
                f"--project_id={args.project}",
                f"--location={settings.region}",
                "cp",
                "-f",
                source_model,
                destination_model,
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        client.query(
            f"""
INSERT INTO `{settings.dataset_path}.model_evaluations`
  (model_version, evaluated_at, model_name, precision, recall, accuracy,
   f1_score, log_loss, roc_auc, is_simulated)
VALUES (
  'phase4-provider-model', CURRENT_TIMESTAMP(),
  'heat_risk_escalation_model', NULL, NULL, NULL, NULL, NULL, NULL, TRUE
)
""",
            job_config=bigquery.QueryJobConfig(
                maximum_bytes_billed=50_000_000
            ),
        ).result()
        repository = BigQuerySimulationRepository(
            client, dataset=settings.dataset_path
        )
        run = repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )
        monitoring_ood_ticks = []
        source = None
        for tick_index in range(3):
            candidate_tick = next(
                item for item in repository.ticks.values()
                if item.run_id == run.run_id and item.tick_index == tick_index
            )
            lease = repository.acquire_tick_lease(
                run.run_id,
                candidate_tick.tick_id,
                f"phase4-scoring-probe-{tick_index}",
            )
            candidate_publication = repository.publish_tick(
                run.run_id, candidate_tick.tick_id, lease.fencing_token
            )
            candidate_prediction_run_id = score_snapshot(
                settings,
                client,
                scenario="heatwave",
                model_version="phase4-provider-model",
                feature_source="simulation",
                simulation_run_id=run.run_id,
                tick_id=candidate_tick.tick_id,
                snapshot_id=candidate_tick.snapshot_id,
                simulation_time=candidate_tick.simulation_time,
            )
            repository.mark_scored(run.run_id, candidate_tick.tick_id)
            repository.finalize_score(
                run.run_id, candidate_tick.tick_id, succeeded=True
            )
            if candidate_publication.result.model_input_ood:
                monitoring_ood_ticks.append(candidate_tick.tick_id)
                continue
            source = (
                candidate_tick,
                candidate_prediction_run_id,
            )
            break
        if source is None:
            raise RuntimeError("no non-OOD source tick was available for control")
        tick, prediction_run_id = source
        decision_rows = list(client.query(
            f"""
SELECT driver_id_hash, continuous_exposure_minutes,
       baseline_risk_probability, risk_probability
FROM `{settings.dataset_path}.driver_risk_predictions`
WHERE simulation_run_id = @run_id AND tick_id = @tick_id
  AND snapshot_id = @snapshot_id
  AND action_type = 'SAFEPAUSE'
  AND pause_start_delay_minutes = 0
  AND pause_duration_minutes = 15
ORDER BY baseline_risk_probability DESC, driver_id_hash
LIMIT 2
""",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "run_id", "STRING", run.run_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "tick_id", "STRING", tick.tick_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "snapshot_id", "STRING", tick.snapshot_id
                    ),
                ],
                maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
            ),
        ).result())
        if len(decision_rows) != 2:
            raise RuntimeError("provider scoring did not yield two control candidates")
        selected_driver_ids = [
            str(row["driver_id_hash"]) for row in decision_rows
        ]
        proposal_id = f"phase4-provider-{tick.snapshot_id}"
        created_at = datetime.now(UTC).replace(microsecond=0)
        expires_at = created_at + timedelta(minutes=10)
        proposal_payload = {
            "proposal_id": proposal_id,
            "scenario_id": "heatwave",
            "simulation_run_id": run.run_id,
            "source_tick_id": tick.tick_id,
            "source_snapshot_id": tick.snapshot_id,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "within_guardrails": True,
            "selected_drivers": 2,
            "driver_decisions": [
                {
                    "driver_id_hash": str(row["driver_id_hash"]),
                    "exposure_minutes": int(row["continuous_exposure_minutes"]),
                    "baseline_risk": float(row["baseline_risk_probability"]),
                    "action_risk": float(row["risk_probability"]),
                    "pause_start_delay_minutes": 0,
                    "pause_duration_minutes": 15,
                }
                for row in decision_rows
            ],
        }
        client.query(
            f"""
INSERT INTO `{settings.dataset_path}.intervention_proposals`
  (proposal_id, created_at, zone_id, eligible_drivers, selected_drivers,
   exposure_minutes_avoided, net_platform_cost_vnd,
   projected_fulfillment_rate, within_guardrails, proposal_json,
   scenario_id, source_snapshot_id, simulation_run_id, source_tick_id,
   expires_at)
VALUES (
  @proposal_id, @created_at, 'provider-zone', 2, 2, 30, 0, 1.0, TRUE,
  PARSE_JSON(@proposal_json), 'heatwave', @snapshot_id, @run_id, @tick_id,
  @expires_at
)
""",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "proposal_id", "STRING", proposal_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "created_at", "TIMESTAMP", created_at
                    ),
                    bigquery.ScalarQueryParameter(
                        "proposal_json", "STRING",
                        json.dumps(proposal_payload, sort_keys=True),
                    ),
                    bigquery.ScalarQueryParameter(
                        "snapshot_id", "STRING", tick.snapshot_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "run_id", "STRING", run.run_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "tick_id", "STRING", tick.tick_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "expires_at", "TIMESTAMP", expires_at
                    ),
                ],
                maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
            ),
        ).result()
        queued = BigQueryControlWriter(
            client, dataset=settings.dataset_path
        ).queue(
            proposal_id=proposal_id,
            run_id=run.run_id,
            source_tick_id=tick.tick_id,
            source_snapshot_id=tick.snapshot_id,
            request_execution_id="phase4-provider-probe",
        )
        next_repository = BigQuerySimulationRepository(
            bigquery.Client(project=args.project), dataset=settings.dataset_path
        )
        durable_run = next_repository.status("heatwave")
        assert durable_run is not None
        next_tick = next(
            item for item in next_repository.ticks.values()
            if item.run_id == run.run_id
            and item.tick_index == tick.tick_index + 1
        )
        next_lease = next_repository.acquire_tick_lease(
            run.run_id, next_tick.tick_id, "phase4-control-probe"
        )
        next_publication = next_repository.publish_tick(
            run.run_id, next_tick.tick_id, next_lease.fencing_token
        )
        second_prediction_run_id = score_snapshot(
            settings,
            client,
            scenario="heatwave",
            model_version="phase4-provider-model",
            feature_source="simulation",
            simulation_run_id=run.run_id,
            tick_id=next_tick.tick_id,
            snapshot_id=next_tick.snapshot_id,
            simulation_time=next_tick.simulation_time,
        )
        next_repository.mark_scored(run.run_id, next_tick.tick_id)
        finalized = next_repository.finalize_score(
            run.run_id, next_tick.tick_id, succeeded=True
        )
        rows = list(client.query(
            f"""
SELECT
  (SELECT COUNT(*) FROM `{settings.dataset_path}.driver_current_features`
   WHERE simulation_run_id = @run_id AND tick_id = @next_tick_id) feature_rows,
  (SELECT COUNT(*) FROM `{settings.dataset_path}.driver_risk_predictions`
   WHERE simulation_run_id = @run_id) prediction_rows,
  (SELECT COUNT(DISTINCT snapshot_id)
   FROM `{settings.dataset_path}.driver_risk_predictions`
   WHERE simulation_run_id = @run_id) prediction_snapshots,
  (SELECT COUNT(*) FROM `{settings.dataset_path}.zone_demand_forecasts`
   WHERE simulation_run_id = @run_id) forecast_rows,
  (SELECT COUNT(DISTINCT zone_id)
   FROM `{settings.dataset_path}.zone_demand_forecasts`
   WHERE simulation_run_id = @run_id AND status = '') forecast_zones,
  (SELECT COUNT(*) FROM `{settings.dataset_path}.simulation_ticks`
   WHERE simulation_run_id = @run_id
     AND error_code = 'MODEL_INPUT_OOD') monitoring_ood_ticks,
  (SELECT ANY_VALUE(error_code) FROM `{settings.dataset_path}.simulation_ticks`
   WHERE simulation_run_id = @run_id
     AND tick_id = @source_tick_id) source_tick_error_code,
  (SELECT COUNT(*) FROM `{settings.dataset_path}.driver_intervention_events`
   WHERE simulation_run_id = @run_id
     AND tick_id = @next_tick_id) intervention_rows,
  (SELECT COUNT(*) FROM `{settings.dataset_path}.simulation_control_consumptions`
   WHERE simulation_run_id = @run_id AND outcome = 'APPLIED') control_receipts,
  (SELECT ANY_VALUE(status)
   FROM `{settings.dataset_path}.simulation_control_events`
   WHERE control_event_id = @control_event_id) control_status,
  (SELECT COUNT(*) FROM `{settings.dataset_path}.driver_simulation_state`
   WHERE simulation_run_id = @run_id
     AND driver_id_hash IN UNNEST(@selected_driver_ids)
     AND current_intervention_id IS NOT NULL) selected_mutated,
  (SELECT ANY_VALUE(status) FROM `{settings.dataset_path}.simulation_ticks`
   WHERE simulation_run_id = @run_id AND tick_id = @next_tick_id) tick_status
""",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "run_id", "STRING", run.run_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "next_tick_id", "STRING", next_tick.tick_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "source_tick_id", "STRING", tick.tick_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "control_event_id", "STRING", queued.control_event_id
                    ),
                    bigquery.ArrayQueryParameter(
                        "selected_driver_ids", "STRING", selected_driver_ids
                    ),
                ],
                maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
            ),
        ).result())
        evidence = dict(rows[0])
        expected_snapshots = tick.tick_index + 2
        expected_forecasts = expected_snapshots * 160
        if (
            int(evidence["feature_rows"]) <= 0
            or int(evidence["prediction_rows"]) <= 0
            or int(evidence["prediction_snapshots"]) != expected_snapshots
            or int(evidence["forecast_rows"]) != expected_forecasts
            or int(evidence["forecast_zones"]) != 10
            or int(evidence["monitoring_ood_ticks"]) != len(monitoring_ood_ticks)
            or evidence["source_tick_error_code"] is not None
            or int(evidence["intervention_rows"]) < 2
            or int(evidence["control_receipts"]) != 1
            or evidence["control_status"] != "CONSUMED"
            or int(evidence["selected_mutated"]) != 2
            or evidence["tick_status"] != "SUCCEEDED"
        ):
            raise RuntimeError(f"provider evidence mismatch: {evidence!r}")
        print(json.dumps({
            **evidence,
            "run": asdict(finalized),
            "prediction_run_id": prediction_run_id,
            "second_prediction_run_id": second_prediction_run_id,
            "monitoring_ood_tick_ids": monitoring_ood_ticks,
            "source_tick_index": tick.tick_index,
            "snapshot_id": tick.snapshot_id,
            "second_snapshot_id": next_tick.snapshot_id,
            "cleanup_dataset": settings.dataset_path,
        }, default=str, sort_keys=True))
        return 0
    finally:
        client.delete_dataset(
            settings.dataset_path, delete_contents=True, not_found_ok=True
        )


if __name__ == "__main__":
    raise SystemExit(main())
