#!/usr/bin/env python3
"""Local Phase 6 probe for exact history reads and transport-only batching."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any
from unittest import mock
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from heatsafe.repository import _parse_zone  # noqa: E402
from heatsafe.simulation.repository import (  # noqa: E402
    InMemorySimulationRepository,
    Publication,
    load_zone_priors,
)


FIXED_RUN_UUID = UUID("12345678-1234-5678-1234-567812345678")
FIXED_WALL_TIME = datetime(2026, 5, 25, 17, 0, tzinfo=UTC)
HEAVY_TABLES = (
    "order_rows",
    "driver_history_rows",
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _row_bytes(row: Mapping[str, Any]) -> bytes:
    return json.dumps(
        _jsonable(row),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()


class BatchManifest:
    """Buffer rows by transport batch while preserving their tick order."""

    def __init__(self, batch_size: int):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.batch_size = batch_size
        self.pending: list[Publication] = []
        self.hashes = {name: hashlib.sha256() for name in HEAVY_TABLES}
        self.counts = {name: 0 for name in HEAVY_TABLES}
        self.flushes = 0

    def add(self, publication: Publication) -> None:
        self.pending.append(publication)
        if len(self.pending) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        for publication in self.pending:
            for name in HEAVY_TABLES:
                rows: Iterable[Mapping[str, Any]] = getattr(publication, name)
                for row in rows:
                    self.hashes[name].update(_row_bytes(row))
                    self.hashes[name].update(b"\n")
                    self.counts[name] += 1
        self.pending.clear()
        self.flushes += 1

    def finish(self) -> dict[str, object]:
        self.flush()
        return {
            "batch_size": self.batch_size,
            "flushes": self.flushes,
            "tables": {
                name: {
                    "rows": self.counts[name],
                    "sha256": self.hashes[name].hexdigest(),
                }
                for name in HEAVY_TABLES
            },
        }


def reconstruct_public_zones(publication: Publication) -> list[dict[str, Any]]:
    """Rebuild the public ZoneSnapshot contract from existing history rows."""

    priors = {zone.zone_id: zone for zone in load_zone_priors()}
    weather = {
        (row["simulation_run_id"], row["tick_id"], row["zone_id"]): row
        for row in publication.weather_rows
    }
    reconstructed: list[dict[str, Any]] = []
    for operation in publication.operation_rows:
        key = (
            operation["simulation_run_id"],
            operation["tick_id"],
            operation["zone_id"],
        )
        weather_row = weather[key]
        prior = priors[str(operation["zone_id"])]
        reconstructed.append(
            {
                "scenario_id": operation["scenario_id"],
                "snapshot_id": operation["snapshot_id"],
                "zone_id": operation["zone_id"],
                "name": weather_row["name"],
                "latitude": weather_row["latitude"],
                "longitude": weather_row["longitude"],
                "temperature_c": weather_row["temperature_c"],
                "humidity_percent": weather_row["humidity_percent"],
                "heat_index_c": weather_row["heat_index_c"],
                "observed_at": operation["observed_at"],
                "weather_observed_at": weather_row["observed_at"],
                "operations_observed_at": operation["observed_at"],
                "active_drivers": operation["active_drivers"],
                "fresh_drivers": operation["fresh_drivers"],
                "exposed_2h": operation["exposed_2h"],
                "exposed_4h": operation["exposed_4h"],
                "forecast_requests_30m": operation["forecast_requests_30m"],
                "avg_platform_contribution_vnd": operation[
                    "avg_platform_contribution_vnd"
                ],
                "avg_driver_earnings_vnd": operation[
                    "avg_driver_earnings_vnd"
                ],
                "coolstop_name": prior.coolstop_name,
                "coolstop_latitude": prior.coolstop_latitude,
                "coolstop_longitude": prior.coolstop_longitude,
                "weather_is_simulated": weather_row["is_simulated"],
                "operations_is_simulated": operation["is_simulated"],
                "simulation_run_id": operation["simulation_run_id"],
                "tick_id": operation["tick_id"],
                "generator_version": operation["generator_version"],
            }
        )
    return reconstructed


def public_zone_contract_matches(publication: Publication) -> bool:
    source = "Deterministic HeatSafe simulation"
    current = sorted(
        (asdict(_parse_zone(dict(row), source=source)) for row in publication.zone_rows),
        key=lambda row: row["zone_id"],
    )
    historical = sorted(
        (
            asdict(_parse_zone(row, source=source))
            for row in reconstruct_public_zones(publication)
        ),
        key=lambda row: row["zone_id"],
    )
    return current == historical


def run_probe(tick_count: int = 8) -> dict[str, object]:
    if not 1 <= tick_count <= 96:
        raise ValueError("tick_count must be between 1 and 96")
    started = perf_counter()
    collectors = [BatchManifest(size) for size in (1, 4, 8)]
    tick_checksums: list[str] = []
    reconstruction_failures: list[int] = []

    with (
        mock.patch(
            "heatsafe.simulation.repository.uuid4",
            return_value=FIXED_RUN_UUID,
        ),
        mock.patch(
            "heatsafe.simulation.repository._utc_now",
            return_value=FIXED_WALL_TIME,
        ),
    ):
        repository = InMemorySimulationRepository(
            now=lambda: FIXED_WALL_TIME,
            lease_seconds=360,
        )
        run = repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )
        for tick_index in range(tick_count):
            tick = next(
                item
                for item in repository.ticks.values()
                if item.run_id == run.run_id and item.tick_index == tick_index
            )
            lease = repository.acquire_tick_lease(
                run.run_id,
                tick.tick_id,
                f"phase6-local-{tick_index}",
            )
            publication = repository.publish_tick(
                run.run_id,
                tick.tick_id,
                lease.fencing_token,
            )
            if not public_zone_contract_matches(publication):
                reconstruction_failures.append(tick_index)
            tick_checksums.append(publication.result.checksum)
            for collector in collectors:
                collector.add(publication)
            repository.finalize_score(
                run.run_id,
                tick.tick_id,
                succeeded=True,
            )

    manifests = [collector.finish() for collector in collectors]
    canonical_tables = manifests[0]["tables"]
    equivalent = all(
        manifest["tables"] == canonical_tables for manifest in manifests[1:]
    )
    return {
        "probe": "phase6-fast-replay-local-equivalence",
        "tick_count": tick_count,
        "run_id": FIXED_RUN_UUID.hex,
        "history_reconstruction": {
            "passed": not reconstruction_failures,
            "failed_ticks": reconstruction_failures,
        },
        "transport_batch_equivalent": equivalent,
        "tick_checksums": tick_checksums,
        "manifests": manifests,
        "elapsed_seconds": round(perf_counter() - started, 3),
        "provider_runtime_proven": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=8)
    args = parser.parse_args()
    result = run_probe(args.ticks)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if (
        result["history_reconstruction"]["passed"]
        and result["transport_batch_equivalent"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
