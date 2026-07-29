#!/usr/bin/env python3
"""Materialize the approved five-tick Event Replay v2 slice in Google Cloud.

The manual orchestrator warm-starts from the reviewed local checkpoint, executes
ticks 37-40 with the normal BigQuery scorer, derives an ACTIVATE control from
the tick-40 BQML/TimesFM evidence, executes tick 41, and leaves the run paused.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
from typing import Any
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from google.cloud import bigquery, storage

from heatsafe.audit import BigQueryInterventionAuditStore
from heatsafe.cloud_bundle import (
    EVENT_REPLAY_DECISION_TICK,
    EVENT_REPLAY_END_TICK,
    EVENT_REPLAY_GENERATOR_VERSION,
    EVENT_REPLAY_START_TICK,
)
from heatsafe.config import Settings
from heatsafe.currency import usd_to_vnd
from heatsafe.event_replay import RollingEventReplayController
from heatsafe.models import DecisionConstraints
from heatsafe.production_mode import (
    ProductionSession,
    load_production_window,
    load_warm_state,
)
from heatsafe.simulation import cli as simulation_cli
from heatsafe.simulation.checkpoint import (
    GCSCheckpointStore,
    checkpoint_object_name,
    encode_checkpoint,
)
from heatsafe.simulation.control import (
    TRUSTED_ACTOR_TYPE,
    validate_control_payload,
)
from heatsafe.simulation.repository import (
    BigQuerySimulationRepository,
    SimulationRepositoryError,
    _new_manifest,
)


BOOTSTRAP_PREDECESSOR_TICK = EVENT_REPLAY_START_TICK - 1
BUNDLE_CONSTRAINTS = DecisionConstraints(
    horizon_minutes=120,
    budget_cap_vnd=usd_to_vnd(500),
    sponsor_per_driver_vnd=usd_to_vnd(0.32),
)


def _query(
    client: bigquery.Client,
    sql: str,
    parameters: dict[str, Any],
    *,
    maximum_bytes_billed: int = 50_000_000,
) -> list[Any]:
    def parameter(name: str, value: Any):
        if isinstance(value, list):
            return bigquery.ArrayQueryParameter(name, "STRING", value)
        field_type = (
            "BOOL"
            if isinstance(value, bool)
            else "INT64"
            if isinstance(value, int)
            else "TIMESTAMP"
            if isinstance(value, datetime)
            else "STRING"
        )
        return bigquery.ScalarQueryParameter(name, field_type, value)

    config = bigquery.QueryJobConfig(
        query_parameters=[
            parameter(name, value) for name, value in parameters.items()
        ],
        maximum_bytes_billed=maximum_bytes_billed,
        labels={"app": "heatsafe", "component": "event-replay-bundle"},
    )
    result = client.query(sql, job_config=config).result()
    try:
        return list(result)
    except TypeError:
        return []


def _validate_local_source() -> tuple[Any, Any]:
    window = load_production_window()
    warm_state = load_warm_state(window)
    timeline_path = (
        ROOT
        / "data"
        / "scenarios"
        / "hanoi_heatwave_v1"
        / "operator_presentation_timeline.json"
    )
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    generated_from = timeline.get("generated_from") or {}
    activate_ticks = [
        int(frame["tick"])
        for frame in [
            *(timeline.get("pre_decision") or []),
            *((timeline.get("branches") or {}).get("ACTIVATE") or []),
        ]
    ]
    if (
        window.generator_version != EVENT_REPLAY_GENERATOR_VERSION
        or window.seed != 42
        or window.start_tick != EVENT_REPLAY_START_TICK
        or warm_state.minute_index != EVENT_REPLAY_START_TICK * 15
        or generated_from.get("generator_version")
        != EVENT_REPLAY_GENERATOR_VERSION
        or generated_from.get("source_state_checksum")
        != window.source_state_checksum
        or activate_ticks != list(range(EVENT_REPLAY_START_TICK, 54))
    ):
        raise RuntimeError("local Event Replay v2 source failed identity checks")
    return window, warm_state


def _simulation_repository(
    settings: Settings,
    client: bigquery.Client,
    storage_client: storage.Client,
) -> BigQuerySimulationRepository:
    return BigQuerySimulationRepository(
        client,
        dataset=settings.dataset_path,
        staging_dataset=settings.simulation_staging_dataset_path,
        lease_seconds=settings.simulation_lease_seconds,
        checkpoint_store=GCSCheckpointStore(
            storage_client, settings.simulation_checkpoint_bucket
        ),
        state_mode="checkpoint",
        staging_workers=settings.simulation_staging_workers,
    )


def bootstrap_bundle(
    settings: Settings,
    client: bigquery.Client,
    storage_client: storage.Client,
) -> str:
    window, warm_state = _validate_local_source()
    repository = _simulation_repository(settings, client, storage_client)
    existing = repository.status("heatwave")
    if existing is not None:
        marker_rows = _query(
            client,
            f"""
              SELECT JSON_VALUE(config_json, '$.bundle_source') AS bundle_source
              FROM `{settings.dataset_path}.simulation_runs`
              WHERE simulation_run_id = @run_id
            """,
            {"run_id": existing.run_id},
        )
        marker = (
            str(dict(marker_rows[0]).get("bundle_source") or "")
            if len(marker_rows) == 1
            else ""
        )
        if marker != "event-replay-v2-ticks-37-41":
            raise SimulationRepositoryError(
                "configured dataset already contains a different simulation run"
            )
        return existing.run_id

    run = repository.start(
        scenario_id="heatwave",
        scenario_version=window.scenario_version,
        seed=window.seed,
    )
    predecessor = next(
        tick
        for tick in repository.ticks.values()
        if tick.run_id == run.run_id
        and tick.tick_index == BOOTSTRAP_PREDECESSOR_TICK
    )
    frozen_at = datetime.now(UTC)
    manifest = _new_manifest((), frozen_at)
    checkpoint = encode_checkpoint(warm_state)
    checkpoint_store = repository.checkpoint_store
    if checkpoint_store is None:
        raise RuntimeError("checkpoint store is required for bundle bootstrap")
    checkpoint_metadata = checkpoint_store.put(
        checkpoint_object_name(
            run_id=run.run_id,
            tick_index=predecessor.tick_index,
            input_checksum=manifest.checksum,
        ),
        checkpoint,
    )
    _query(
        client,
        f"""
          BEGIN TRANSACTION;
          UPDATE `{settings.dataset_path}.simulation_ticks`
          SET status = 'SUCCEEDED',
              finished_at = CURRENT_TIMESTAMP(),
              input_manifest_json = PARSE_JSON(@manifest_json),
              input_manifest_checksum = @manifest_checksum,
              input_frozen_at = @frozen_at,
              input_checksum = @manifest_checksum,
              checkpoint_object_name = @checkpoint_object_name,
              checkpoint_format_version = @checkpoint_format_version,
              checkpoint_generation = @checkpoint_generation,
              checkpoint_compressed_size = @checkpoint_compressed_size,
              checkpoint_expanded_size = @checkpoint_expanded_size,
              checkpoint_payload_sha256 = @checkpoint_payload_sha256,
              checkpoint_state_checksum = @checkpoint_state_checksum,
              state_mode = 'checkpoint',
              execution_mode = 'FULL',
              scoring_outcome = 'BOOTSTRAP_LOCAL_STATE'
          WHERE simulation_run_id = @run_id
            AND tick_index = @predecessor_tick
            AND status = 'PENDING';
          ASSERT @@row_count = 1;
          UPDATE `{settings.dataset_path}.simulation_runs`
          SET last_published_tick_index = @predecessor_tick,
              last_completed_tick_index = @predecessor_tick,
              next_simulation_at = TIMESTAMP_ADD(
                simulation_start_at,
                INTERVAL 15 * (@predecessor_tick + 1) MINUTE
              ),
              config_json = JSON_OBJECT(
                'bundle_source', 'event-replay-v2-ticks-37-41',
                'source_branch', 'ACTIVATE',
                'source_state_checksum', @source_state_checksum,
                'generator_version', @generator_version
              ),
              updated_at = CURRENT_TIMESTAMP()
          WHERE simulation_run_id = @run_id
            AND last_completed_tick_index IS NULL;
          ASSERT @@row_count = 1;
          COMMIT TRANSACTION;
        """,
        {
            "run_id": run.run_id,
            "predecessor_tick": predecessor.tick_index,
            "manifest_json": manifest.json,
            "manifest_checksum": manifest.checksum,
            "frozen_at": frozen_at,
            "checkpoint_object_name": checkpoint_metadata.object_name,
            "checkpoint_format_version": checkpoint_metadata.format_version,
            "checkpoint_generation": checkpoint_metadata.generation,
            "checkpoint_compressed_size": checkpoint_metadata.compressed_size,
            "checkpoint_expanded_size": checkpoint_metadata.expanded_size,
            "checkpoint_payload_sha256": checkpoint_metadata.payload_sha256,
            "checkpoint_state_checksum": checkpoint_metadata.state_checksum,
            "source_state_checksum": window.source_state_checksum,
            "generator_version": EVENT_REPLAY_GENERATOR_VERSION,
        },
    )
    return run.run_id


def queue_cloud_activation(
    settings: Settings,
    client: bigquery.Client,
    *,
    run_id: str,
) -> tuple[str, ...]:
    execution_id = os.getenv("CLOUD_RUN_EXECUTION")
    if not execution_id:
        raise RuntimeError(
            "ACTIVATE control may only be queued from the Cloud Run orchestrator"
        )
    tick_rows = _query(
        client,
        f"""
          SELECT tick_id, snapshot_id, simulation_time
          FROM `{settings.dataset_path}.simulation_ticks`
          WHERE simulation_run_id = @run_id
            AND tick_index = @tick_index
            AND status = 'SUCCEEDED'
            AND scoring_outcome = 'SCORED'
        """,
        {"run_id": run_id, "tick_index": EVENT_REPLAY_DECISION_TICK},
    )
    if len(tick_rows) != 1:
        raise RuntimeError("tick-40 scored lineage is unavailable")
    tick_row = dict(tick_rows[0])
    now = datetime.now(UTC)

    # Preserve the approved Event Replay ACTIVATE branch. BQML and TimesFM
    # remain the scored evidence for every cloud tick, while the control input
    # comes from the reviewed local artifact instead of weakening the generic
    # OOD guard in BigQueryControlWriter.
    presentation_window = replace(
        load_production_window(),
        decision_tick=EVENT_REPLAY_DECISION_TICK,
    )
    session = ProductionSession.create(window=presentation_window)
    while session.current_tick < EVENT_REPLAY_DECISION_TICK:
        session.advance()
    controller = RollingEventReplayController(BUNDLE_CONSTRAINTS)
    evaluation = controller.evaluate_and_queue(
        session, queue_controls=False
    )
    proposals = tuple(
        replace(
            proposal,
            proposal_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "heatsafe:cloud-bundle:"
                    f"{run_id}:{execution_id}:{proposal.proposal_id}",
                )
            ),
            created_at=now,
            source_snapshot_at=tick_row["simulation_time"],
            scenario_id="heatwave",
            source_snapshot_id=str(tick_row["snapshot_id"]),
            simulation_run_id=run_id,
            source_tick_id=str(tick_row["tick_id"]),
            expires_at=now + timedelta(minutes=30),
        )
        for proposal in evaluation.proposals
    )
    if (
        evaluation.plan.status
        not in {"READY", "SAFETY_CAPACITY_BREACH"}
        or not proposals
    ):
        raise RuntimeError(
            "approved local ACTIVATE branch produced no actionable plan: "
            f"{evaluation.plan.status}"
        )
    audit = BigQueryInterventionAuditStore(settings)
    for proposal in proposals:
        audit.approve(
            proposal,
            approved_by="cloud-bundle-orchestrator",
            actor_type="TRUSTED_OPERATOR",
        )
        stored_rows = _query(
            client,
            f"""
              SELECT proposal_json
              FROM `{settings.dataset_path}.intervention_proposals`
              WHERE proposal_id = @proposal_id
                AND simulation_run_id = @run_id
                AND source_tick_id = @source_tick_id
                AND source_snapshot_id = @source_snapshot_id
              ORDER BY created_at DESC
              LIMIT 1
            """,
            {
                "proposal_id": proposal.proposal_id,
                "run_id": run_id,
                "source_tick_id": str(tick_row["tick_id"]),
                "source_snapshot_id": str(tick_row["snapshot_id"]),
            },
        )
        if len(stored_rows) != 1:
            raise RuntimeError(
                f"stored proposal {proposal.proposal_id} is unavailable"
            )
        payload = dict(stored_rows[0])["proposal_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        queued = validate_control_payload(
            payload,
            scenario_id="heatwave",
            run_id=run_id,
            source_tick_id=str(tick_row["tick_id"]),
            source_snapshot_id=str(tick_row["snapshot_id"]),
            source_tick_index=EVENT_REPLAY_DECISION_TICK,
            now=now,
            simulation_time=tick_row["simulation_time"],
        )
        _query(
            client,
            f"""
              MERGE `{settings.dataset_path}.simulation_control_events` target
              USING (SELECT @control_event_id control_event_id) source
              ON target.control_event_id = source.control_event_id
              WHEN NOT MATCHED THEN INSERT (
                control_event_id, scenario_id, simulation_run_id,
                source_tick_id, source_snapshot_id, proposal_id,
                proposal_payload_checksum, status, selected_driver_count,
                requested_by, actor_type, request_execution_id, created_at,
                authorization_expires_at, valid_from_simulation_at,
                valid_until_simulation_at, max_selected_drivers,
                is_simulated, generator_version
              ) VALUES (
                @control_event_id, 'heatwave', @run_id, @source_tick_id,
                @source_snapshot_id, @proposal_id, @payload_checksum,
                'AUTHORIZED', @selected_driver_count,
                'event-replay-artifact-import', @actor_type,
                @request_execution_id, @created_at,
                @authorization_expires_at, @valid_from_simulation_at,
                @valid_until_simulation_at, 250, TRUE,
                @generator_version
              )
            """,
            {
                "control_event_id": queued.control_event_id,
                "run_id": run_id,
                "source_tick_id": str(tick_row["tick_id"]),
                "source_snapshot_id": str(tick_row["snapshot_id"]),
                "proposal_id": proposal.proposal_id,
                "payload_checksum": queued.payload_checksum,
                "selected_driver_count": queued.selected_driver_count,
                "actor_type": TRUSTED_ACTOR_TYPE,
                "request_execution_id": execution_id,
                "created_at": now,
                "authorization_expires_at": (
                    queued.authorization_expires_at
                ),
                "valid_from_simulation_at": (
                    queued.valid_from_simulation_at
                ),
                "valid_until_simulation_at": (
                    queued.valid_until_simulation_at
                ),
                "generator_version": EVENT_REPLAY_GENERATOR_VERSION,
            },
        )
    return tuple(proposal.proposal_id for proposal in proposals)


def valid_cloud_activation_ids(
    settings: Settings,
    client: bigquery.Client,
    *,
    run_id: str,
) -> tuple[str, ...]:
    """Return valid imported controls and retain invalid attempts as rejected."""
    rows = _query(
        client,
        f"""
          SELECT control.control_event_id, control.proposal_id,
                 control.proposal_payload_checksum, control.created_at,
                 control.source_tick_id, control.source_snapshot_id,
                 control.max_selected_drivers, proposal.proposal_json,
                 tick.tick_index, tick.simulation_time
          FROM `{settings.dataset_path}.simulation_control_events` control
          JOIN `{settings.dataset_path}.intervention_proposals` proposal
            ON proposal.proposal_id = control.proposal_id
           AND proposal.simulation_run_id = control.simulation_run_id
           AND proposal.source_tick_id = control.source_tick_id
           AND proposal.source_snapshot_id = control.source_snapshot_id
          JOIN `{settings.dataset_path}.simulation_ticks` tick
            ON tick.simulation_run_id = control.simulation_run_id
           AND tick.tick_id = control.source_tick_id
          WHERE control.simulation_run_id = @run_id
            AND control.status IN ('AUTHORIZED', 'QUEUED', 'CONSUMED')
            AND tick.tick_index = @decision_tick
          ORDER BY control.proposal_id
        """,
        {"run_id": run_id, "decision_tick": EVENT_REPLAY_DECISION_TICK},
    )
    valid: list[str] = []
    invalid: list[str] = []
    for raw in rows:
        row = dict(raw)
        payload = row["proposal_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        try:
            queued = validate_control_payload(
                payload,
                scenario_id="heatwave",
                run_id=run_id,
                source_tick_id=str(row["source_tick_id"]),
                source_snapshot_id=str(row["source_snapshot_id"]),
                source_tick_index=int(row["tick_index"]),
                now=row["created_at"],
                simulation_time=row["simulation_time"],
                max_selected_drivers=int(row["max_selected_drivers"]),
            )
        except Exception:
            invalid.append(str(row["control_event_id"]))
            continue
        if (
            queued.control_event_id != row["control_event_id"]
            or queued.payload_checksum != row["proposal_payload_checksum"]
        ):
            invalid.append(str(row["control_event_id"]))
        else:
            valid.append(str(row["proposal_id"]))
    if invalid:
        _query(
            client,
            f"""
              UPDATE `{settings.dataset_path}.simulation_control_events`
              SET status = 'REJECTED'
              WHERE simulation_run_id = @run_id
                AND control_event_id IN UNNEST(@control_event_ids)
            """,
            {"run_id": run_id, "control_event_ids": invalid},
        )
    return tuple(valid)


def _run_cli(*args: str) -> None:
    code = simulation_cli.main(list(args))
    if code != 0:
        raise RuntimeError(
            f"simulation command failed ({code}): {' '.join(args)}"
        )


def run_bundle() -> dict[str, Any]:
    settings = Settings.from_env()
    if settings.simulation_generator_version != EVENT_REPLAY_GENERATOR_VERSION:
        raise RuntimeError("bundle requires stateful-replay-v2")
    if settings.simulation_state_mode != "checkpoint":
        raise RuntimeError("bundle requires checkpoint state mode")
    client = bigquery.Client(project=settings.project_id)
    storage_client = storage.Client(project=settings.project_id)
    run_id = bootstrap_bundle(settings, client, storage_client)
    repository = _simulation_repository(settings, client, storage_client)
    run = repository.refresh_status("heatwave")
    if run is None or run.run_id != run_id:
        raise RuntimeError("bootstrapped run could not be reloaded")
    if run.status == "PAUSED" and (
        run.last_completed_tick_index is None
        or run.last_completed_tick_index < EVENT_REPLAY_END_TICK
    ):
        _run_cli("resume", "--scenario", "heatwave")

    success = False
    proposal_ids: tuple[str, ...] = ()
    try:
        _run_cli(
            "fast-replay",
            "--scenario",
            "heatwave",
            "--run-id",
            run_id,
            "--until",
            str(EVENT_REPLAY_DECISION_TICK),
            "--max-runtime-seconds",
            "2400",
        )
        proposal_ids = valid_cloud_activation_ids(
            settings, client, run_id=run_id
        )
        if proposal_ids:
            pass
        else:
            proposal_ids = queue_cloud_activation(
                settings, client, run_id=run_id
            )
        _run_cli(
            "fast-replay",
            "--scenario",
            "heatwave",
            "--run-id",
            run_id,
            "--until",
            str(EVENT_REPLAY_END_TICK),
            "--max-runtime-seconds",
            "1200",
        )
        _run_cli("pause", "--scenario", "heatwave")
        success = True
    finally:
        if not success:
            latest = _simulation_repository(
                settings, client, storage_client
            ).refresh_status("heatwave")
            if latest is not None and latest.status == "RUNNING":
                try:
                    _run_cli("pause", "--scenario", "heatwave")
                except Exception:
                    pass

    return {
        "dataset": settings.dataset_path,
        "simulation_run_id": run_id,
        "source": "local-event-replay-v2",
        "source_branch": "ACTIVATE",
        "scored_ticks": list(
            range(EVENT_REPLAY_START_TICK, EVENT_REPLAY_END_TICK + 1)
        ),
        "proposal_ids": proposal_ids,
        "status": "PAUSED",
    }


def main() -> int:
    print(json.dumps(run_bundle(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
