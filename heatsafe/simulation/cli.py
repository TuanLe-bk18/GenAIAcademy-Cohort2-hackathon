"""Command-line adapter for the Phase 3 repository contract."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, cast

from heatsafe.config import Settings

from .repository import BigQuerySimulationRepository, InMemorySimulationRepository, SimulationRepositoryError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m heatsafe.simulation.cli")
    parser.add_argument(
        "command",
        choices=(
            "validate-scenario", "start", "tick", "status", "pause", "resume",
            "queue-control",
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
    return BigQuerySimulationRepository(
        bigquery.Client(project=settings.project_id), dataset=settings.dataset_path,
        lease_seconds=settings.simulation_lease_seconds,
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


def main(
    argv: list[str] | None = None,
    *,
    repository_factory: Callable | None = None,
    scorer_factory: Callable | None = None,
    control_writer_factory: Callable | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
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
        run = repository.status(args.scenario)
        if run is None:
            raise SimulationRepositoryError("start a simulation before requesting a tick")
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
        lease = repository.acquire_tick_lease(run.run_id, tick_id, "cli")
        publication = repository.publish_tick(run.run_id, tick_id, lease.fencing_token)
        if repository.ticks[tick_id].status == "SUCCEEDED":
            print(_json({
                "tick_id": tick_id,
                "snapshot_id": publication.tick.snapshot_id,
                "status": "SUCCEEDED",
                "outcome": "NO_OP_ALREADY_SUCCEEDED",
            }))
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
        repository.mark_scored(run.run_id, tick_id)
        completed = repository.finalize_score(run.run_id, tick_id, succeeded=True)
        print(_json({
            "tick_id": publication.tick.tick_id,
            "snapshot_id": publication.tick.snapshot_id,
            "prediction_run_id": scoring.prediction_run_id,
            "status": repository.ticks[tick_id].status,
            "checksum": publication.result.checksum,
            "last_completed_tick_index": completed.last_completed_tick_index,
        }))
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
