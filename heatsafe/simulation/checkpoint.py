"""Lossless, bounded checkpoint codec and storage contract for Phase 5R.

The decoder is deliberately data-only: it accepts a closed set of dataclasses,
enums, primitive tags, and tuples.  It never imports or executes types named by
the payload.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
import base64
import gzip
import hashlib
import io
import json
import math
import types
from typing import Any, Protocol, get_args, get_origin, get_type_hints

from google.api_core import exceptions as google_exceptions
from google.api_core.retry import Retry, if_exception_type
import google_crc32c

from .telemetry import component_span
from .models import (
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
from .randomness import canonical_checksum


FORMAT_VERSION = "json-floathex-offset-gzip-v1"
COMPRESSION_LEVEL = 9
MAX_COMPRESSED_BYTES = 2_000_000
MAX_EXPANDED_BYTES = 24_000_000
MAX_DECOMPRESSION_RATIO = 40
MAX_DRIVERS = 10_000
MAX_ORDERS = 100_000
MAX_INTERVENTIONS = 20_000
MAX_EVENTS = 200_000
MAX_SEQUENCE_ITEMS = 250_000
MAX_NESTING_DEPTH = 12
GCS_RETRY = Retry(
    predicate=if_exception_type(
        google_exceptions.TooManyRequests,
        google_exceptions.InternalServerError,
        google_exceptions.ServiceUnavailable,
    ),
    initial=0.2,
    maximum=2.0,
    multiplier=2.0,
    deadline=10.0,
)


class CheckpointError(ValueError):
    """A checkpoint failed validation and must not be used."""


class CheckpointConflict(CheckpointError):
    """A deterministic object name already contains different bytes."""


@dataclass(frozen=True, slots=True)
class CheckpointMetadata:
    object_name: str
    format_version: str
    generation: int
    compressed_size: int
    expanded_size: int
    payload_sha256: str
    state_checksum: str


@dataclass(frozen=True, slots=True)
class EncodedCheckpoint:
    data: bytes
    expanded_size: int
    payload_sha256: str
    state_checksum: str


class CheckpointStore(Protocol):
    def put(
        self, object_name: str, checkpoint: EncodedCheckpoint
    ) -> CheckpointMetadata: ...

    def get(self, metadata: CheckpointMetadata) -> bytes: ...

    def list_names(self, prefix: str) -> tuple[str, ...]: ...


_DATACLASSES = {
    cls.__name__: cls
    for cls in (
        SimulationState,
        DriverState,
        OrderState,
        InterventionState,
        OrderEvent,
    )
}


def checkpoint_object_name(
    *, run_id: str, tick_index: int, input_checksum: str
) -> str:
    if not run_id or "/" in run_id or not input_checksum:
        raise CheckpointError("invalid checkpoint object identity")
    return (
        f"runs/{run_id}/ticks/{tick_index:03d}/"
        f"{input_checksum}.{FORMAT_VERSION}.json.gz"
    )


def _encode(value: object, *, depth: int = 0) -> object:
    if depth > MAX_NESTING_DEPTH:
        raise CheckpointError("checkpoint nesting is too deep")
    if value is None:
        return value
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.value}
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CheckpointError("checkpoint floats must be finite")
        return {"$float": value.hex()}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CheckpointError("checkpoint datetimes must be timezone-aware")
        offset_minutes = int(value.utcoffset().total_seconds() // 60)
        return {
            "$datetime_utc": value.astimezone(UTC).isoformat(
                timespec="microseconds"
            ),
            "$offset_minutes": str(offset_minutes),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": type(value).__name__,
            "fields": {
                field.name: _encode(getattr(value, field.name), depth=depth + 1)
                for field in fields(value)
            },
        }
    if isinstance(value, tuple):
        if len(value) > MAX_SEQUENCE_ITEMS:
            raise CheckpointError("checkpoint sequence exceeds item limit")
        return {
            "$tuple": [_encode(item, depth=depth + 1) for item in value]
        }
    raise CheckpointError(f"unsupported checkpoint value: {type(value).__name__}")


def _expect_exact(mapping: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(mapping, dict) or set(mapping) != keys:
        raise CheckpointError(f"{label} has missing or unknown fields")
    return mapping


@lru_cache(maxsize=None)
def _dataclass_schema(expected: type) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Resolve immutable codec schemas once, not once per decoded entity."""

    hints = get_type_hints(expected)
    names = tuple(field.name for field in fields(expected))
    return hints, names


def _decode(value: object, expected: Any, *, depth: int = 0) -> object:
    if depth > MAX_NESTING_DEPTH:
        raise CheckpointError("checkpoint nesting is too deep")
    if expected is str:
        if not isinstance(value, str):
            raise CheckpointError("checkpoint string field has wrong type")
        return value
    if expected is bool:
        if not isinstance(value, bool):
            raise CheckpointError("checkpoint boolean field has wrong type")
        return value
    if expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise CheckpointError("checkpoint integer is not decimal")
        return value
    if expected is float:
        mapping = _expect_exact(value, {"$float"}, "float")
        raw = mapping["$float"]
        if not isinstance(raw, str):
            raise CheckpointError("checkpoint float has wrong type")
        try:
            decoded = float.fromhex(raw)
        except ValueError as exc:
            raise CheckpointError("checkpoint float is invalid") from exc
        if not math.isfinite(decoded):
            raise CheckpointError("checkpoint floats must be finite")
        return decoded
    if expected is datetime:
        mapping = _expect_exact(
            value, {"$datetime_utc", "$offset_minutes"}, "datetime"
        )
        try:
            instant = datetime.fromisoformat(mapping["$datetime_utc"])
            offset_minutes = int(mapping["$offset_minutes"])
        except (TypeError, ValueError) as exc:
            raise CheckpointError("checkpoint datetime is invalid") from exc
        if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
            raise CheckpointError("checkpoint datetime instant must be UTC")
        if not -14 * 60 <= offset_minutes <= 14 * 60:
            raise CheckpointError("checkpoint datetime offset is invalid")
        return instant.astimezone(timezone(timedelta(minutes=offset_minutes)))
    origin = get_origin(expected)
    args = get_args(expected)
    if origin in {types.UnionType, getattr(types, "UnionType", object)}:
        if value is None and type(None) in args:
            return None
        choices = tuple(item for item in args if item is not type(None))
        if len(choices) != 1:
            raise CheckpointError("unsupported checkpoint union")
        return _decode(value, choices[0], depth=depth)
    if value is None:
        raise CheckpointError("unexpected null checkpoint field")
    if origin is tuple:
        mapping = _expect_exact(value, {"$tuple"}, "tuple")
        items = mapping["$tuple"]
        if not isinstance(items, list) or len(items) > MAX_SEQUENCE_ITEMS:
            raise CheckpointError("checkpoint tuple exceeds item limit")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(
                _decode(item, args[0], depth=depth + 1) for item in items
            )
        if len(items) != len(args):
            raise CheckpointError("checkpoint tuple has wrong length")
        return tuple(
            _decode(item, item_type, depth=depth + 1)
            for item, item_type in zip(items, args, strict=True)
        )
    if isinstance(expected, type) and issubclass(expected, Enum):
        mapping = _expect_exact(value, {"$enum", "value"}, "enum")
        if mapping["$enum"] != expected.__name__:
            raise CheckpointError("checkpoint enum type is invalid")
        try:
            return expected(mapping["value"])
        except (TypeError, ValueError) as exc:
            raise CheckpointError("checkpoint enum value is invalid") from exc
    if isinstance(expected, type) and expected.__name__ in _DATACLASSES:
        mapping = _expect_exact(value, {"$type", "fields"}, "dataclass")
        if mapping["$type"] != expected.__name__:
            raise CheckpointError("checkpoint dataclass type is invalid")
        raw_fields = mapping["fields"]
        hints, expected_names = _dataclass_schema(expected)
        if not isinstance(raw_fields, dict) or set(raw_fields) != set(expected_names):
            raise CheckpointError("checkpoint dataclass has missing or unknown fields")
        return expected(
            **{
                name: _decode(
                    raw_fields[name], hints[name], depth=depth + 1
                )
                for name in expected_names
            }
        )
    raise CheckpointError(f"unsupported checkpoint schema: {expected!r}")


def _validate_counts(state: SimulationState) -> None:
    limits = (
        ("drivers", len(state.drivers), MAX_DRIVERS),
        ("orders", len(state.orders), MAX_ORDERS),
        ("interventions", len(state.interventions), MAX_INTERVENTIONS),
        ("events", len(state.events), MAX_EVENTS),
    )
    for name, count, maximum in limits:
        if count > maximum:
            raise CheckpointError(f"checkpoint {name} exceeds count limit")


def encode_checkpoint(state: SimulationState) -> EncodedCheckpoint:
    _validate_counts(state)
    encoded_state = _encode(state)
    document = {
        "format_version": FORMAT_VERSION,
        "state_checksum": canonical_checksum(state),
        "state": encoded_state,
    }
    expanded = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(expanded) > MAX_EXPANDED_BYTES:
        raise CheckpointError("expanded checkpoint exceeds byte limit")
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        fileobj=output,
        compresslevel=COMPRESSION_LEVEL,
        mtime=0,
    ) as compressed:
        compressed.write(expanded)
    data = output.getvalue()
    if len(data) > MAX_COMPRESSED_BYTES:
        raise CheckpointError("compressed checkpoint exceeds byte limit")
    return EncodedCheckpoint(
        data=data,
        expanded_size=len(expanded),
        payload_sha256=hashlib.sha256(data).hexdigest(),
        state_checksum=document["state_checksum"],
    )


def _bounded_decompress(data: bytes) -> bytes:
    if not data or len(data) > MAX_COMPRESSED_BYTES:
        raise CheckpointError("compressed checkpoint exceeds byte limit")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as source:
            expanded = source.read(MAX_EXPANDED_BYTES + 1)
    except (OSError, EOFError) as exc:
        raise CheckpointError("checkpoint gzip is corrupt") from exc
    if len(expanded) > MAX_EXPANDED_BYTES:
        raise CheckpointError("expanded checkpoint exceeds byte limit")
    if len(expanded) / len(data) > MAX_DECOMPRESSION_RATIO:
        raise CheckpointError("checkpoint decompression ratio exceeds limit")
    return expanded


def decode_checkpoint(
    data: bytes,
    *,
    expected_payload_sha256: str | None = None,
    expected_state_checksum: str | None = None,
) -> SimulationState:
    payload_sha256 = hashlib.sha256(data).hexdigest()
    if (
        expected_payload_sha256 is not None
        and payload_sha256 != expected_payload_sha256
    ):
        raise CheckpointError("checkpoint payload hash mismatch")
    expanded = _bounded_decompress(data)
    try:
        document = json.loads(expanded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckpointError("checkpoint JSON is invalid") from exc
    mapping = _expect_exact(
        document, {"format_version", "state_checksum", "state"}, "checkpoint"
    )
    if mapping["format_version"] != FORMAT_VERSION:
        raise CheckpointError("checkpoint format version is unsupported")
    if not isinstance(mapping["state_checksum"], str):
        raise CheckpointError("checkpoint state checksum is invalid")
    if (
        expected_state_checksum is not None
        and mapping["state_checksum"] != expected_state_checksum
    ):
        raise CheckpointError("checkpoint state checksum metadata mismatch")
    state = _decode(mapping["state"], SimulationState)
    assert isinstance(state, SimulationState)
    _validate_counts(state)
    # A normal GCS restore supplies both values from the transactionally
    # committed tick metadata.  In that path the payload SHA-256 authenticates
    # every serialized state byte and the embedded checksum must match the
    # committed logical checksum, so re-canonicalizing the million-node state
    # would duplicate integrity work on every hot tick.  Offline/sentinel
    # callers omit either trusted value and retain the full logical recompute.
    trusted_metadata = (
        expected_payload_sha256 is not None
        and expected_state_checksum is not None
    )
    if (
        not trusted_metadata
        and canonical_checksum(state) != mapping["state_checksum"]
    ):
        raise CheckpointError("checkpoint logical state checksum mismatch")
    return state


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, int]] = {}

    def put(
        self, object_name: str, checkpoint: EncodedCheckpoint
    ) -> CheckpointMetadata:
        existing = self.objects.get(object_name)
        if existing is not None:
            if existing[0] != checkpoint.data:
                raise CheckpointConflict(
                    "checkpoint object precondition conflict has different bytes"
                )
            data, generation = existing
        else:
            data, generation = checkpoint.data, 1
            self.objects[object_name] = (data, generation)
        metadata = CheckpointMetadata(
            object_name=object_name,
            format_version=FORMAT_VERSION,
            generation=generation,
            compressed_size=len(data),
            expanded_size=checkpoint.expanded_size,
            payload_sha256=checkpoint.payload_sha256,
            state_checksum=checkpoint.state_checksum,
        )
        decode_checkpoint(
            self.get(metadata),
            expected_payload_sha256=metadata.payload_sha256,
            expected_state_checksum=metadata.state_checksum,
        )
        return metadata

    def get(self, metadata: CheckpointMetadata) -> bytes:
        stored = self.objects.get(metadata.object_name)
        if stored is None:
            raise CheckpointError("checkpoint object is missing")
        data, generation = stored
        if generation != metadata.generation or len(data) != metadata.compressed_size:
            raise CheckpointError("checkpoint object metadata mismatch")
        return data

    def list_names(self, prefix: str) -> tuple[str, ...]:
        return tuple(sorted(name for name in self.objects if name.startswith(prefix)))


class GCSCheckpointStore:
    def __init__(self, client: Any, bucket_name: str) -> None:
        self.bucket = client.bucket(bucket_name)

    def put(
        self, object_name: str, checkpoint: EncodedCheckpoint
    ) -> CheckpointMetadata:
        blob = self.bucket.blob(object_name)
        expected_crc32c = base64.b64encode(
            google_crc32c.Checksum(checkpoint.data).digest()
        ).decode("ascii")
        try:
            blob.upload_from_string(
                checkpoint.data,
                content_type="application/gzip",
                if_generation_match=0,
                checksum="crc32c",
                timeout=15,
                retry=GCS_RETRY,
            )
            if (
                blob.generation is None
                or blob.size is None
                or blob.crc32c is None
            ):
                blob.reload(timeout=10, retry=GCS_RETRY)
        except google_exceptions.PreconditionFailed:
            blob.reload(timeout=10, retry=GCS_RETRY)
            existing = blob.download_as_bytes(
                if_generation_match=blob.generation,
                timeout=15,
                retry=GCS_RETRY,
            )
            if existing != checkpoint.data:
                raise CheckpointConflict(
                    "checkpoint object precondition conflict has different bytes"
                )
        with component_span("checkpoint_readback") as readback_span:
            readback_span.set(object_bytes=len(checkpoint.data))
            if int(blob.size or -1) != len(checkpoint.data):
                raise CheckpointError("checkpoint object size metadata differs")
            if blob.crc32c != expected_crc32c:
                raise CheckpointError("checkpoint object CRC32C metadata differs")
        metadata = CheckpointMetadata(
            object_name=object_name,
            format_version=FORMAT_VERSION,
            generation=int(blob.generation),
            compressed_size=len(checkpoint.data),
            expanded_size=checkpoint.expanded_size,
            payload_sha256=checkpoint.payload_sha256,
            state_checksum=checkpoint.state_checksum,
        )
        return metadata

    def get(self, metadata: CheckpointMetadata) -> bytes:
        blob = self.bucket.blob(metadata.object_name, generation=metadata.generation)
        try:
            data = blob.download_as_bytes(
                if_generation_match=metadata.generation,
                timeout=15,
                retry=GCS_RETRY,
            )
        except google_exceptions.NotFound as exc:
            raise CheckpointError("checkpoint object is missing") from exc
        if len(data) != metadata.compressed_size:
            raise CheckpointError("checkpoint object size mismatch")
        return data

    def list_names(self, prefix: str) -> tuple[str, ...]:
        return tuple(
            sorted(blob.name for blob in self.bucket.list_blobs(prefix=prefix))
        )
