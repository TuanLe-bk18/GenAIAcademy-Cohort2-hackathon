#!/usr/bin/env python3
"""Local Stage 0E lossless checkpoint codec benchmark.

This is deliberately a probe, not the production checkpoint implementation.
It serializes only the frozen v1 envelope and never reads or writes GCS.
"""

from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import UTC, datetime
from enum import Enum
import gzip
import gc
import hashlib
import importlib.metadata
import io
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from heatsafe.simulation.engine import (  # noqa: E402
    advance_tick,
    initialize_state,
    load_zone_priors,
)
from heatsafe.simulation.models import (  # noqa: E402
    AcclimatizationClass,
    DriverState,
    DriverStatus,
    InterventionState,
    InterventionStatus,
    OrderEvent,
    OrderEventType,
    OrderState,
    OrderStatus,
    SimulationState,
)
from heatsafe.simulation.randomness import canonical_checksum  # noqa: E402
from heatsafe.simulation.scenario import load_scenario  # noqa: E402


FORMAT_VERSION = "heatsafe-checkpoint-v1"
CODEC_VERSION = "json-floathex-gzip-v1"
OFFSET_CODEC_VERSION = "json-floathex-offset-gzip-v1"
MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_RATIO = 100
TAGGED_INT_FIELDS = {"schedule_bits", "rest_minute_bits", "trips_minute_bits"}


def runtime_contract(
    *, image_digest: str, base_image_digest: str, codec_version: str
) -> dict[str, str]:
    values = {
        "image_digest": image_digest,
        "base_image_digest": base_image_digest,
        "python": sys.version,
        "machine": platform.machine(),
        "zlib": zlib.ZLIB_VERSION,
        "google_cloud_storage": importlib.metadata.version("google-cloud-storage"),
        "google_cloud_bigquery": importlib.metadata.version("google-cloud-bigquery"),
        "codec": codec_version,
    }
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return {**values, "runtime_contract_id": hashlib.sha256(canonical).hexdigest()}


def _datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("checkpoint datetimes must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _encoded_datetime(value: datetime, *, codec_version: str) -> object:
    normalized = _datetime(value)
    if codec_version == CODEC_VERSION:
        return normalized
    if codec_version != OFFSET_CODEC_VERSION:
        raise ValueError(f"unsupported probe codec: {codec_version}")
    offset = value.utcoffset()
    if offset is None:
        raise ValueError("checkpoint datetime has no UTC offset")
    return {
        "$datetime_utc": normalized,
        "$offset_minutes": int(offset.total_seconds() // 60),
    }


def _encode(
    value: object,
    *,
    field_name: str | None = None,
    codec_version: str,
) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _encoded_datetime(value, codec_version=codec_version)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("checkpoint floats must be finite")
        return {"$float64_hex": value.hex()}
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        if field_name in TAGGED_INT_FIELDS:
            if value < 0:
                raise ValueError(f"{field_name} must be unsigned")
            return {"$int10": str(value)}
        return value
    if isinstance(value, tuple):
        return [_encode(item, codec_version=codec_version) for item in value]
    raise TypeError(f"unsupported checkpoint scalar: {type(value).__name__}")


def _mapping(value: object, *, codec_version: str) -> dict[str, object]:
    return {
        field.name: _encode(
            getattr(value, field.name),
            field_name=field.name,
            codec_version=codec_version,
        )
        for field in fields(value)
    }


def _state_mapping(
    state: SimulationState, *, codec_version: str
) -> dict[str, object]:
    return {
        "scenario_version": state.scenario_version,
        "generator_version": state.generator_version,
        "run_id": state.run_id,
        "seed": state.seed,
        "start_time": _encoded_datetime(
            state.start_time, codec_version=codec_version
        ),
        "minute_index": state.minute_index,
        "drivers": [
            _mapping(item, codec_version=codec_version) for item in state.drivers
        ],
        "orders": [
            _mapping(item, codec_version=codec_version) for item in state.orders
        ],
        "interventions": [
            _mapping(item, codec_version=codec_version)
            for item in state.interventions
        ],
        "events": [
            _mapping(item, codec_version=codec_version) for item in state.events
        ],
        "city_shock": _encode(state.city_shock, codec_version=codec_version),
        "zone_shocks": [
            [zone_id, _encode(shock, codec_version=codec_version)]
            for zone_id, shock in state.zone_shocks
        ],
    }


def _required(value: dict[str, object], cls: type) -> None:
    expected = {field.name for field in fields(cls)}
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{cls.__name__} fields mismatch: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _decode(value: object) -> object:
    if isinstance(value, list):
        return tuple(_decode(item) for item in value)
    if isinstance(value, dict):
        if set(value) == {"$float64_hex"}:
            result = float.fromhex(str(value["$float64_hex"]))
            if not math.isfinite(result):
                raise ValueError("decoded checkpoint float must be finite")
            return result
        if set(value) == {"$int10"}:
            raw = value["$int10"]
            if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
                raise ValueError("tagged integer is not unsigned base-10")
            return int(raw)
        raise ValueError("unexpected object tag in checkpoint scalar")
    return value


def _decode_datetime(value: object, *, codec_version: str) -> datetime:
    from datetime import timedelta, timezone

    if codec_version == CODEC_VERSION:
        normalized = value
        offset_minutes = 0
    elif codec_version == OFFSET_CODEC_VERSION:
        if not isinstance(value, dict) or set(value) != {
            "$datetime_utc",
            "$offset_minutes",
        }:
            raise ValueError("offset checkpoint datetime tag is invalid")
        normalized = value["$datetime_utc"]
        offset_minutes = value["$offset_minutes"]
        if isinstance(offset_minutes, bool) or not isinstance(offset_minutes, int):
            raise ValueError("checkpoint datetime offset must be an integer")
        if not -840 <= offset_minutes <= 840:
            raise ValueError("checkpoint datetime offset is out of bounds")
    else:
        raise ValueError(f"unsupported checkpoint codec: {codec_version}")
    if not isinstance(normalized, str) or not normalized.endswith("Z"):
        raise ValueError("checkpoint datetime must include a UTC Z instant")
    result = datetime.fromisoformat(normalized[:-1] + "+00:00")
    if _datetime(result) != normalized:
        raise ValueError("checkpoint datetime is not canonical")
    return result.astimezone(timezone(timedelta(minutes=offset_minutes)))


def _construct(cls: type, value: object, *, enums: dict[str, type[Enum]]) -> object:
    if not isinstance(value, dict):
        raise ValueError(f"{cls.__name__} must be an object")
    _required(value, cls)
    decoded = {name: _decode(item) for name, item in value.items()}
    for name, enum_cls in enums.items():
        item = decoded[name]
        decoded[name] = None if item is None else enum_cls(item)
    return cls(**decoded)


def _decode_state(value: object, *, codec_version: str) -> SimulationState:
    if not isinstance(value, dict):
        raise ValueError("state must be an object")
    _required(value, SimulationState)
    drivers_raw = value["drivers"]
    orders_raw = value["orders"]
    interventions_raw = value["interventions"]
    events_raw = value["events"]
    zone_shocks_raw = value["zone_shocks"]
    if not all(
        isinstance(items, list)
        for items in (
            drivers_raw,
            orders_raw,
            interventions_raw,
            events_raw,
            zone_shocks_raw,
        )
    ):
        raise ValueError("checkpoint state collections must be arrays")
    if len(drivers_raw) > 10_000 or len(orders_raw) > 20_000:
        raise ValueError("checkpoint state count ceiling exceeded")
    if len(events_raw) > 50_000 or len(interventions_raw) > 2_000:
        raise ValueError("checkpoint state count ceiling exceeded")
    drivers = tuple(
        _construct(
            DriverState,
            item,
            enums={
                "status": DriverStatus,
                "acclimatization_class": AcclimatizationClass,
            },
        )
        for item in drivers_raw
    )
    if any(
        len(driver.distance_by_minute) > 60
        or len(driver.earnings_by_minute) > 60
        or len(driver.contribution_by_minute) > 60
        for driver in drivers
    ):
        raise ValueError("per-driver rolling array ceiling exceeded")
    orders = tuple(
        _construct(OrderState, item, enums={"status": OrderStatus})
        for item in orders_raw
    )
    interventions = tuple(
        _construct(
            InterventionState, item, enums={"status": InterventionStatus}
        )
        for item in interventions_raw
    )
    events = tuple(
        _construct(
            OrderEvent,
            item,
            enums={
                "event_type": OrderEventType,
                "prior_status": OrderStatus,
            },
        )
        for item in events_raw
    )
    zone_shocks = tuple(
        (str(pair[0]), float(_decode(pair[1])))
        for pair in zone_shocks_raw
        if isinstance(pair, list) and len(pair) == 2
    )
    if len(zone_shocks) != len(zone_shocks_raw) or len(zone_shocks) > 100:
        raise ValueError("zone shock shape/count invalid")
    return SimulationState(
        scenario_version=str(value["scenario_version"]),
        generator_version=str(value["generator_version"]),
        run_id=str(value["run_id"]),
        seed=int(value["seed"]),
        start_time=_decode_datetime(
            value["start_time"], codec_version=codec_version
        ),
        minute_index=int(value["minute_index"]),
        drivers=drivers,
        orders=orders,
        interventions=interventions,
        events=events,
        city_shock=float(_decode(value["city_shock"])),
        zone_shocks=zone_shocks,
    )


def encode_checkpoint(
    state: SimulationState,
    *,
    runtime_contract_id: str,
    tick_index: int,
    codec_version: str,
) -> tuple[bytes, bytes]:
    envelope = {
        "format_version": FORMAT_VERSION,
        "codec": codec_version,
        "runtime_contract_id": runtime_contract_id,
        "scenario_version": state.scenario_version,
        "generator_version": state.generator_version,
        "run_id": state.run_id,
        "tick_id": f"probe-{tick_index:02d}",
        "tick_index": tick_index,
        "input_checksum": canonical_checksum(
            (state.run_id, tick_index, state.minute_index)
        ),
        "state_checksum": canonical_checksum(state),
        "state": _state_mapping(state, codec_version=codec_version),
    }
    expanded = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=output, compresslevel=6, mtime=0
    ) as handle:
        handle.write(expanded)
    compressed = output.getvalue()
    if len(expanded) > MAX_EXPANDED_BYTES or len(compressed) > MAX_COMPRESSED_BYTES:
        raise ValueError("checkpoint byte ceiling exceeded")
    if len(expanded) > max(1, len(compressed)) * MAX_RATIO:
        raise ValueError("checkpoint decompression ratio ceiling exceeded")
    return expanded, compressed


def decode_checkpoint(
    compressed: bytes,
    *,
    runtime_contract_id: str,
    codec_version: str,
) -> tuple[dict[str, object], SimulationState]:
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise ValueError("compressed checkpoint ceiling exceeded")
    with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as handle:
        expanded = handle.read(MAX_EXPANDED_BYTES + 1)
    if len(expanded) > MAX_EXPANDED_BYTES:
        raise ValueError("expanded checkpoint ceiling exceeded")
    if len(expanded) > max(1, len(compressed)) * MAX_RATIO:
        raise ValueError("checkpoint decompression ratio ceiling exceeded")
    envelope = json.loads(expanded)
    required = {
        "format_version",
        "codec",
        "runtime_contract_id",
        "scenario_version",
        "generator_version",
        "run_id",
        "tick_id",
        "tick_index",
        "input_checksum",
        "state_checksum",
        "state",
    }
    if not isinstance(envelope, dict) or set(envelope) != required:
        raise ValueError("checkpoint envelope fields mismatch")
    if envelope["format_version"] != FORMAT_VERSION:
        raise ValueError("unsupported checkpoint format")
    if envelope["codec"] != codec_version:
        raise ValueError("unsupported checkpoint codec")
    if envelope["runtime_contract_id"] != runtime_contract_id:
        raise ValueError("runtime contract mismatch")
    state = _decode_state(envelope["state"], codec_version=codec_version)
    if canonical_checksum(state) != envelope["state_checksum"]:
        raise ValueError("checkpoint state checksum mismatch")
    metadata = {
        name: value for name, value in envelope.items() if name != "state"
    }
    del envelope
    return metadata, state


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def run_probe(args: argparse.Namespace) -> dict[str, object]:
    fixture = load_scenario(args.scenario_version)
    zones = load_zone_priors()
    state = initialize_state(seed=args.seed, fixture=fixture, zones=zones)
    requested = set(args.ticks)
    codecs = (args.codec,)
    results: dict[str, list[dict[str, object]]] = {
        codec_version: [] for codec_version in codecs
    }
    for tick_index in range(max(requested) + 1):
        tick_result = advance_tick(state, fixture=fixture, zones=zones)
        state = tick_result.state
        if tick_index not in requested:
            continue
        for codec_version in codecs:
            runtime = runtime_contract(
                image_digest=args.image_digest,
                base_image_digest=args.base_image_digest,
                codec_version=codec_version,
            )
            encode_ms: list[float] = []
            decode_ms: list[float] = []
            compressed = b""
            expanded_size = 0
            expanded_hash = ""
            restored = state
            for _ in range(args.repeats):
                started = time.monotonic_ns()
                expanded, compressed = encode_checkpoint(
                    state,
                    runtime_contract_id=runtime["runtime_contract_id"],
                    tick_index=tick_index,
                    codec_version=codec_version,
                )
                encode_ms.append((time.monotonic_ns() - started) / 1_000_000)
                expanded_size = len(expanded)
                expanded_hash = hashlib.sha256(expanded).hexdigest()
                del expanded
                started = time.monotonic_ns()
                decoded_envelope, restored = decode_checkpoint(
                    compressed,
                    runtime_contract_id=runtime["runtime_contract_id"],
                    codec_version=codec_version,
                )
                del decoded_envelope
                decode_ms.append((time.monotonic_ns() - started) / 1_000_000)
            typed_equal = state == restored
            next_tick_equal: bool | None = None
            if tick_index < 95:
                next_original = advance_tick(
                    state, fixture=fixture, zones=zones
                ).checksum
                next_restored = advance_tick(
                    restored, fixture=fixture, zones=zones
                ).checksum
                next_tick_equal = next_original == next_restored
            compressed_size = len(compressed)
            compressed_hash = hashlib.sha256(compressed).hexdigest()
            del restored, compressed
            gc.collect()
            second_expanded, second_compressed = encode_checkpoint(
                state,
                runtime_contract_id=runtime["runtime_contract_id"],
                tick_index=tick_index,
                codec_version=codec_version,
            )
            deterministic_bytes = (
                hashlib.sha256(second_expanded).hexdigest() == expanded_hash
                and hashlib.sha256(second_compressed).hexdigest()
                == compressed_hash
            )
            if codec_version == OFFSET_CODEC_VERSION and not (
                typed_equal and deterministic_bytes and next_tick_equal is not False
            ):
                raise AssertionError(
                    f"tick {tick_index} offset codec failed exact equivalence"
                )
            results[codec_version].append(
                {
                    "tick_index": tick_index,
                    "drivers": len(state.drivers),
                    "orders": len(state.orders),
                    "events": len(state.events),
                    "interventions": len(state.interventions),
                    "expanded_bytes": expanded_size,
                    "compressed_bytes": compressed_size,
                    "compression_ratio": round(
                        expanded_size / compressed_size, 3
                    ),
                    "expanded_sha256": expanded_hash,
                    "compressed_sha256": compressed_hash,
                    "encode_p50_ms": round(statistics.median(encode_ms), 3),
                    "encode_p95_ms": round(_percentile(encode_ms, 0.95), 3),
                    "decode_p50_ms": round(statistics.median(decode_ms), 3),
                    "decode_p95_ms": round(_percentile(decode_ms, 0.95), 3),
                    "typed_roundtrip_equal": typed_equal,
                    "deterministic_bytes": deterministic_bytes,
                    "next_tick_equal": next_tick_equal,
                    "gzip_savings_pct": round(
                        (1 - compressed_size / expanded_size) * 100, 3
                    ),
                }
            )
            del second_expanded, second_compressed
            gc.collect()
    selected_runtime = runtime_contract(
        image_digest=args.image_digest,
        base_image_digest=args.base_image_digest,
        codec_version=args.codec,
    )
    return {
        "probe": "phase5r-checkpoint-codec",
        "outcome": (
            "PASS_WITH_CONTRACT_REVISION"
            if args.codec == OFFSET_CODEC_VERSION
            else "REJECTED_NEXT_TICK_DIVERGENCE"
        ),
        "provider_mutation": False,
        "runtime": selected_runtime,
        "codec": {
            "format_version": FORMAT_VERSION,
            "frozen_candidate": CODEC_VERSION,
            "frozen_candidate_outcome": "REJECTED_NEXT_TICK_DIVERGENCE",
            "selected_alternative": OFFSET_CODEC_VERSION,
            "selected_reason": (
                "UTC normalization loses the local offset used by demand "
                "generation; the alternative stores UTC instant plus offset"
            ),
            "compressed_limit": MAX_COMPRESSED_BYTES,
            "expanded_limit": MAX_EXPANDED_BYTES,
            "ratio_limit": MAX_RATIO,
        },
        "results_by_codec": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-version", default="hanoi_heatwave_v1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ticks", default="0,24,48,95")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--codec",
        choices=(CODEC_VERSION, OFFSET_CODEC_VERSION),
        default=OFFSET_CODEC_VERSION,
    )
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--base-image-digest", required=True)
    args = parser.parse_args()
    try:
        args.ticks = tuple(sorted({int(item) for item in args.ticks.split(",")}))
    except ValueError as exc:
        parser.error(f"--ticks must be comma-separated integers: {exc}")
    if not args.ticks or args.ticks[0] < 0 or args.ticks[-1] > 95:
        parser.error("--ticks must stay within 0..95")
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be within 1..20")
    for name in ("image_digest", "base_image_digest"):
        value = getattr(args, name)
        if not value.startswith("sha256:") and value != "UNRESOLVED":
            parser.error(f"--{name.replace('_', '-')} must be sha256:... or UNRESOLVED")
    return args


def main() -> None:
    print(json.dumps(run_probe(parse_args()), sort_keys=True))


if __name__ == "__main__":
    main()
