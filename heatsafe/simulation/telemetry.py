"""Structured Phase 5R component timing without simulation payload logging."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
import json
import os
import time
from typing import Any, Callable, Iterator


SCHEMA_VERSION = "phase5r-component-v1"
EVENT_NAME = "simulation_tick_component"
COMPONENTS = frozenset(
    {
        "run_load",
        "lease_acquire",
        "checkpoint_restore",
        "checkpoint_replay_delta",
        "controls_load",
        "input_freeze",
        "advance_tick",
        "publication_projection",
        "staging_schema_lookup",
        "staging_load_driver",
        "staging_load_zone",
        "staging_load_order",
        "staging_load_weather",
        "staging_load_operation",
        "staging_load_demand",
        "staging_load_history",
        "staging_load_intervention",
        "staging_load_consumption",
        "checkpoint_encode",
        "checkpoint_upload",
        "checkpoint_readback",
        "publication_commit",
        "feature_projection",
        "timesfm_context_ensure",
        "ai_forecast",
        "ml_predict",
        "ml_explain_predict",
        "score_finalize",
        "tick_total",
    }
)
OUTCOMES = frozenset({"SUCCEEDED", "FAILED", "SKIPPED", "NO_OP"})
_ACTIVE: ContextVar[TickTelemetry | None] = ContextVar(
    "heatsafe_tick_telemetry", default=None
)


def component_telemetry_enabled() -> bool:
    return os.getenv("HEATSAFE_SIMULATION_COMPONENT_TELEMETRY", "0") == "1"


def _job_value(job: Any, name: str) -> object | None:
    value = getattr(job, name, None)
    if callable(value):
        try:
            value = value()
        except TypeError:
            return None
    return value


class TickTelemetry:
    """Per-attempt emitter whose active instance is propagated by ContextVar."""

    def __init__(
        self,
        *,
        sink: Callable[[str], None] = print,
        state_mode: str = "oracle",
        execution_mode: str = "FULL",
    ) -> None:
        execution = os.getenv("CLOUD_RUN_EXECUTION") or "local"
        task_index = os.getenv("CLOUD_RUN_TASK_INDEX") or "0"
        task_attempt = os.getenv("CLOUD_RUN_TASK_ATTEMPT") or "0"
        self._sink = sink
        self._started_ns = time.monotonic_ns()
        self._finished = False
        self._attempt_outcome = "SUCCEEDED"
        self._attempts: dict[str, int] = {}
        self._context: dict[str, object | None] = {
            "cloud_run_job": os.getenv("CLOUD_RUN_JOB"),
            "cloud_run_execution": os.getenv("CLOUD_RUN_EXECUTION"),
            "task_index": os.getenv("CLOUD_RUN_TASK_INDEX"),
            "task_attempt": os.getenv("CLOUD_RUN_TASK_ATTEMPT"),
            "attempt_id": f"{execution}:{task_index}:{task_attempt}",
            "simulation_run_id": None,
            "tick_id": None,
            "tick_index": None,
            "snapshot_id": None,
            "state_mode": state_mode,
            "execution_mode": execution_mode,
        }

    def bind(self, **fields: object | None) -> None:
        allowed = {
            "simulation_run_id",
            "tick_id",
            "tick_index",
            "snapshot_id",
            "state_mode",
            "execution_mode",
        }
        unexpected = set(fields) - allowed
        if unexpected:
            raise ValueError(f"unsupported telemetry context: {sorted(unexpected)}")
        self._context.update(fields)

    @property
    def attempt_outcome(self) -> str:
        return self._attempt_outcome

    def mark_attempt(self, outcome: str) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown telemetry outcome: {outcome}")
        self._attempt_outcome = outcome

    @contextmanager
    def activate(self) -> Iterator[TickTelemetry]:
        token: Token[TickTelemetry | None] = _ACTIVE.set(self)
        try:
            yield self
        finally:
            _ACTIVE.reset(token)

    def emit(
        self,
        component: str,
        *,
        elapsed_ms: int,
        outcome: str = "SUCCEEDED",
        job: Any | None = None,
        component_attempt: int | None = None,
        **fields: object | None,
    ) -> None:
        if component not in COMPONENTS:
            raise ValueError(f"unknown telemetry component: {component}")
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown telemetry outcome: {outcome}")
        if component_attempt is None:
            component_attempt = self._attempts.get(component, 0) + 1
        self._attempts[component] = component_attempt
        payload: dict[str, object | None] = {
            "severity": "ERROR" if outcome == "FAILED" else "INFO",
            "schema_version": SCHEMA_VERSION,
            "event": EVENT_NAME,
            "component": component,
            "outcome": outcome,
            "recorded_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "elapsed_ms": max(0, int(elapsed_ms)),
            **self._context,
            "component_attempt": component_attempt,
            "bigquery_job_id": None,
            "slot_millis": None,
            "total_bytes_processed": None,
            "total_bytes_billed": None,
            "row_count": None,
            "object_bytes": None,
            "error_code": None,
        }
        if job is not None:
            payload.update(
                {
                    "bigquery_job_id": _job_value(job, "job_id"),
                    "slot_millis": _job_value(job, "slot_millis"),
                    "total_bytes_processed": _job_value(
                        job, "total_bytes_processed"
                    ),
                    "total_bytes_billed": _job_value(job, "total_bytes_billed"),
                }
            )
        payload.update(fields)
        error_code = payload.get("error_code")
        if error_code is not None:
            payload["error_code"] = str(error_code)[:80]
        self._sink(json.dumps(payload, default=str, sort_keys=True))

    def finish(self, *, outcome: str, error_code: str | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        self.emit(
            "tick_total",
            elapsed_ms=(time.monotonic_ns() - self._started_ns) // 1_000_000,
            outcome=outcome,
            error_code=error_code,
        )


class ComponentSpan:
    def __init__(
        self, telemetry: TickTelemetry | None, component: str, fields: dict[str, object]
    ) -> None:
        self.telemetry = telemetry
        self.component = component
        self.fields = fields
        self.started_ns = 0
        self.job: Any | None = None
        self.outcome = "SUCCEEDED"

    def __enter__(self) -> ComponentSpan:
        self.started_ns = time.monotonic_ns()
        return self

    def attach_job(self, job: Any) -> Any:
        self.job = job
        return job

    def set(self, **fields: object) -> None:
        self.fields.update(fields)

    def mark(self, outcome: str) -> None:
        self.outcome = outcome

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        if self.telemetry is None:
            return False
        outcome = "FAILED" if exc_type is not None else self.outcome
        error_code = exc_type.__name__ if exc_type is not None else None
        self.telemetry.emit(
            self.component,
            elapsed_ms=(time.monotonic_ns() - self.started_ns) // 1_000_000,
            outcome=outcome,
            job=self.job,
            error_code=error_code,
            **self.fields,
        )
        return False


def active_telemetry() -> TickTelemetry | None:
    return _ACTIVE.get()


def bind_telemetry(**fields: object | None) -> None:
    telemetry = active_telemetry()
    if telemetry is not None:
        telemetry.bind(**fields)


def mark_attempt_outcome(outcome: str) -> None:
    telemetry = active_telemetry()
    if telemetry is not None:
        telemetry.mark_attempt(outcome)


def component_span(component: str, **fields: object) -> ComponentSpan:
    if component not in COMPONENTS:
        raise ValueError(f"unknown telemetry component: {component}")
    return ComponentSpan(active_telemetry(), component, fields)


def emit_component(
    component: str,
    *,
    elapsed_ms: int,
    outcome: str = "SUCCEEDED",
    job: Any | None = None,
    **fields: object | None,
) -> None:
    telemetry = active_telemetry()
    if telemetry is not None:
        telemetry.emit(
            component,
            elapsed_ms=elapsed_ms,
            outcome=outcome,
            job=job,
            **fields,
        )
