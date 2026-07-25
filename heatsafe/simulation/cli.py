"""Command-line adapter for the Phase 3 repository contract."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, cast

from heatsafe.config import Settings

from .repository import (
    BigQuerySimulationRepository,
    InMemorySimulationRepository,
    LeaseConflict,
    SimulationRepositoryError,
)
from .telemetry import (
    TickTelemetry,
    bind_telemetry,
    component_span,
    component_telemetry_enabled,
    mark_attempt_outcome,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m heatsafe.simulation.cli")
    parser.add_argument(
        "command",
        choices=(
            "validate-scenario", "start", "tick", "status", "pause", "resume",
            "queue-control", "checkpoint-verify",
        ),
    )
    parser.add_argument("--scenario", default="heatwave")
    parser.add_argument("--scenario-version", default="hanoi_heatwave_v1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tick-id")
    parser.add_argument("--proposal-id")
    parser.add_argument("--run-id")
    parser.add_argument("--source-tick-id")
    parser.add_argument("--source-snapshot-id")
    parser.add_argument("--memory", action="store_true", help="use the deterministic local repository")
    return parser


def create_repository(settings: Settings, *, memory: bool):
    if memory:
        return InMemorySimulationRepository(lease_seconds=settings.simulation_lease_seconds)
    from google.cloud import bigquery
    from google.cloud import storage

    from .checkpoint import GCSCheckpointStore

    return BigQuerySimulationRepository(
        bigquery.Client(project=settings.project_id), dataset=settings.dataset_path,
        staging_dataset=settings.simulation_staging_dataset_path,
        lease_seconds=settings.simulation_lease_seconds,
        checkpoint_store=GCSCheckpointStore(
            storage.Client(project=settings.project_id),
            settings.simulation_checkpoint_bucket,
        ),
        state_mode=settings.simulation_state_mode,
        staging_workers=settings.simulation_staging_workers,
    )


def create_scorer(settings: Settings, *, memory: bool):
    if memory:
        from .scoring import DeterministicSnapshotScorer

        return DeterministicSnapshotScorer()
    from google.cloud import bigquery

    from .scoring import BigQuerySnapshotScorer

    return BigQuerySnapshotScorer(
        bigquery.Client(project=settings.project_id), settings=settings
    )


def _json(value: object) -> str:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(cast(Any, value))
    return json.dumps(value, default=str, sort_keys=True)


def _execution_context() -> dict[str, object]:
    return {
        "cloud_run_job": os.getenv("CLOUD_RUN_JOB"),
        "cloud_run_execution": os.getenv("CLOUD_RUN_EXECUTION"),
        "task_index": os.getenv("CLOUD_RUN_TASK_INDEX"),
        "task_attempt": os.getenv("CLOUD_RUN_TASK_ATTEMPT"),
    }


def _emit(event: str, *, started: float | None = None, **fields: object) -> None:
    payload = {
        "severity": "INFO",
        "event": event,
        **_execution_context(),
        **fields,
    }
    if started is not None:
        payload["duration_ms"] = round((time.monotonic() - started) * 1_000)
    print(_json(payload))


def main(
    argv: list[str] | None = None,
    *,
    repository_factory: Callable | None = None,
    scorer_factory: Callable | None = None,
    control_writer_factory: Callable | None = None,
) -> int:
    started = time.monotonic()
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    if args.command == "tick" and component_telemetry_enabled():
        telemetry = TickTelemetry(state_mode=settings.simulation_state_mode)
        with telemetry.activate():
            try:
                result = _run(
                    args,
                    settings,
                    started=started,
                    repository_factory=repository_factory,
                    scorer_factory=scorer_factory,
                    control_writer_factory=control_writer_factory,
                )
            except BaseException as exc:
                telemetry.finish(outcome="FAILED", error_code=type(exc).__name__)
                raise
            telemetry.finish(
                outcome=telemetry.attempt_outcome if result == 0 else "FAILED",
                error_code=None if result == 0 else "TICK_COMMAND_FAILED",
            )
            return result
    return _run(
        args,
        settings,
        started=started,
        repository_factory=repository_factory,
        scorer_factory=scorer_factory,
        control_writer_factory=control_writer_factory,
    )


def _run(
    args: argparse.Namespace,
    settings: Settings,
    *,
    started: float,
    repository_factory: Callable | None,
    scorer_factory: Callable | None,
    control_writer_factory: Callable | None,
) -> int:
    if args.command == "validate-scenario":
        from .scenario import load_scenario
        fixture = load_scenario(args.scenario_version)
        print(_json({"scenario_version": args.scenario_version, "weather_points": len(fixture.weather)}))
        return 0
    if args.command == "queue-control":
        required = {
            "--proposal-id": args.proposal_id,
            "--run-id": args.run_id,
            "--source-tick-id": args.source_tick_id,
            "--source-snapshot-id": args.source_snapshot_id,
        }
        missing = [flag for flag, value in required.items() if not value]
        if missing:
            print(_json({"error": f"missing required arguments: {', '.join(missing)}"}))
            return 2
        execution_id = (
            os.getenv("CLOUD_RUN_EXECUTION")
            or os.getenv("HEATSAFE_CONTROL_EXECUTION_ID")
        )
        if not execution_id:
            print(_json({
                "error": "queue-control requires a trusted job execution identity"
            }))
            return 2
        if args.memory and control_writer_factory is None:
            print(_json({"error": "queue-control has no public/local authority"}))
            return 2
        if control_writer_factory is not None:
            writer = control_writer_factory(settings)
        else:
            from google.cloud import bigquery

            from .control import BigQueryControlWriter

            writer = BigQueryControlWriter(
                bigquery.Client(project=settings.project_id),
                dataset=settings.dataset_path,
            )
        try:
            queued = writer.queue(
                proposal_id=args.proposal_id,
                run_id=args.run_id,
                source_tick_id=args.source_tick_id,
                source_snapshot_id=args.source_snapshot_id,
                request_execution_id=execution_id,
            )
        except ValueError as exc:
            print(_json({"error": str(exc)}))
            return 2
        print(_json(queued))
        return 0
    factory = repository_factory or create_repository
    repository = factory(settings, memory=args.memory)
    try:
        if args.command == "start":
            run = repository.start(scenario_id=args.scenario, scenario_version=args.scenario_version, seed=args.seed)
            print(_json(run))
            return 0
        if args.command == "status":
            print(_json(repository.status(args.scenario)))
            return 0
        if args.command == "pause":
            print(_json(repository.pause(args.scenario)))
            return 0
        if args.command == "resume":
            print(_json(repository.resume(args.scenario)))
            return 0
        if args.command == "checkpoint-verify":
            run = repository.status(args.scenario)
            if run is None:
                raise SimulationRepositoryError(
                    "start a simulation before verifying checkpoints"
                )
            print(_json(repository.verify_checkpoints(run.run_id)))
            return 0
        with component_span("run_load"):
            run = repository.status(args.scenario)
        if run is None:
            raise SimulationRepositoryError("start a simulation before requesting a tick")
        bind_telemetry(simulation_run_id=run.run_id)
        if run.status == "COMPLETED":
            mark_attempt_outcome("NO_OP")
            _emit(
                "simulation_tick_terminal",
                started=started,
                simulation_run_id=run.run_id,
                status="COMPLETED",
                outcome="NO_OP_TERMINAL",
                terminal_signal=True,
            )
            return 0
        tick_id = args.tick_id
        if tick_id is None:
            if run.pending_score_tick_id is not None:
                tick_id = run.pending_score_tick_id
            else:
                index = (
                    -1 if run.last_published_tick_index is None
                    else run.last_published_tick_index
                ) + 1
                tick_id = next(
                    tick.tick_id
                    for tick in repository.ticks.values()
                    if tick.run_id == run.run_id and tick.tick_index == index
                )
        tick = repository.ticks[tick_id]
        bind_telemetry(
            tick_id=tick.tick_id,
            tick_index=tick.tick_index,
            snapshot_id=tick.snapshot_id,
        )
        lease_owner = (
            os.getenv("CLOUD_RUN_EXECUTION")
            or os.getenv("HEATSAFE_SIMULATION_EXECUTION_ID")
            or "cli"
        )
        lease_conflict = False
        with component_span("lease_acquire") as lease_span:
            try:
                lease = repository.acquire_tick_lease(
                    run.run_id, tick_id, lease_owner
                )
            except LeaseConflict:
                lease_span.mark("NO_OP")
                lease_conflict = True
        if lease_conflict:
            mark_attempt_outcome("NO_OP")
            _emit(
                "simulation_tick_overlap",
                started=started,
                simulation_run_id=run.run_id,
                tick_id=tick_id,
                status="RUNNING",
                outcome="NO_OP_LEASE_HELD",
            )
            return 0
        publication = repository.publish_tick(run.run_id, tick_id, lease.fencing_token)
        bind_telemetry(
            execution_mode=repository.ticks[tick_id].execution_mode or "FULL"
        )
        if repository.ticks[tick_id].status == "SUCCEEDED":
            mark_attempt_outcome("NO_OP")
            _emit(
                "simulation_tick_noop",
                started=started,
                simulation_run_id=run.run_id,
                tick_id=tick_id,
                snapshot_id=publication.tick.snapshot_id,
                status="SUCCEEDED",
                outcome="NO_OP_ALREADY_SUCCEEDED",
            )
            return 0
        scorer_builder = scorer_factory or create_scorer
        scorer = scorer_builder(settings, memory=args.memory)
        try:
            scoring = scorer.score(run, publication)
        except Exception as exc:
            repository.finalize_score(run.run_id, tick_id, succeeded=False)
            print(_json({
                "tick_id": publication.tick.tick_id,
                "status": "SCORE_FAILED",
                "error": type(exc).__name__,
            }))
            return 2
        if scoring.durably_finalized:
            completed = repository.acknowledge_scoring_commit(
                run.run_id, tick_id, scoring.prediction_run_id
            )
        else:
            repository.record_scoring_lineage(
                run.run_id, tick_id, scoring.prediction_run_id
            )
            repository.mark_scored(run.run_id, tick_id)
            completed = repository.finalize_score(
                run.run_id, tick_id, succeeded=True
            )
        _emit(
            "simulation_tick_completed",
            started=started,
            simulation_run_id=run.run_id,
            tick_id=publication.tick.tick_id,
            snapshot_id=publication.tick.snapshot_id,
            prediction_run_id=scoring.prediction_run_id,
            status=repository.ticks[tick_id].status,
            checksum=publication.result.checksum,
            last_completed_tick_index=completed.last_completed_tick_index,
            terminal_signal=completed.status == "COMPLETED",
        )
        return 0
    except SimulationRepositoryError as exc:
        parser_error = {"error": str(exc)}
        print(_json(parser_error))
        return 2
    except Exception as exc:
        print(_json({
            "error": type(exc).__name__,
            "message": str(exc)[:240],
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
