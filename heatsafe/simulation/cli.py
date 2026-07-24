"""Command-line adapter for the Phase 3 repository contract."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, cast

from heatsafe.config import Settings

from .repository import BigQuerySimulationRepository, InMemorySimulationRepository, SimulationRepositoryError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m heatsafe.simulation.cli")
    parser.add_argument("command", choices=("validate-scenario", "start", "tick", "status", "pause", "resume"))
    parser.add_argument("--scenario", default="heatwave")
    parser.add_argument("--scenario-version", default="hanoi_heatwave_v1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tick-id")
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


def _json(value: object) -> str:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(cast(Any, value))
    return json.dumps(value, default=str, sort_keys=True)


def main(argv: list[str] | None = None, *, repository_factory: Callable | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    if args.command == "validate-scenario":
        from .scenario import load_scenario
        fixture = load_scenario(args.scenario_version)
        print(_json({"scenario_version": args.scenario_version, "weather_points": len(fixture.weather)}))
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
            index = (
                -1 if run.last_published_tick_index is None
                else run.last_published_tick_index
            ) + 1
            tick_id = next(tick.tick_id for tick in repository.ticks.values() if tick.run_id == run.run_id and tick.tick_index == index)
        lease = repository.acquire_tick_lease(run.run_id, tick_id, "cli")
        publication = repository.publish_tick(run.run_id, tick_id, lease.fencing_token)
        print(_json({"tick_id": publication.tick.tick_id, "status": publication.tick.status, "checksum": publication.result.checksum}))
        return 0
    except SimulationRepositoryError as exc:
        parser_error = {"error": str(exc)}
        print(_json(parser_error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
