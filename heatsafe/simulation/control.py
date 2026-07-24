"""Trusted SafePause control contracts for the stateful replay.

Public intervention audit rows are inputs to validation only.  A simulator
control exists only after the authenticated control writer records an immutable
event with exact run/tick/snapshot lineage and a canonical payload checksum.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any, Mapping

from .models import PauseControl
from .randomness import canonical_checksum


CONTROL_AUTHORIZATION_MINUTES = 10
CONTROL_SIMULATION_WINDOW_MINUTES = 60
DEFAULT_MAX_SELECTED_DRIVERS = 250
MAXIMUM_CONTROL_QUERY_BYTES = 100_000_000
TRUSTED_ACTOR_TYPE = "TRUSTED_OPERATOR"
TRUSTED_REQUESTED_BY = "heatsafe-simulation-control"


class ControlValidationError(ValueError):
    """A proposal is not eligible to become authoritative simulation input."""


@dataclass(frozen=True, slots=True)
class QueuedControl:
    control_event_id: str
    proposal_id: str
    payload_checksum: str
    selected_driver_count: int
    authorization_expires_at: datetime
    valid_from_simulation_at: datetime
    valid_until_simulation_at: datetime
    pause_controls: tuple[PauseControl, ...]


def _aware_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ControlValidationError(f"{field} must be an ISO timestamp")
    if parsed.tzinfo is None:
        raise ControlValidationError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def canonical_proposal_checksum(payload: Mapping[str, Any]) -> str:
    """Checksum the immutable JSON representation stored with the proposal."""
    return canonical_checksum(dict(payload))


def validate_control_payload(
    payload: Mapping[str, Any],
    *,
    scenario_id: str,
    run_id: str,
    source_tick_id: str,
    source_snapshot_id: str,
    source_tick_index: int,
    now: datetime,
    simulation_time: datetime,
    max_selected_drivers: int = DEFAULT_MAX_SELECTED_DRIVERS,
) -> QueuedControl:
    if now.tzinfo is None or simulation_time.tzinfo is None:
        raise ControlValidationError("control clocks must be timezone-aware")
    exact_lineage = {
        "scenario_id": scenario_id,
        "simulation_run_id": run_id,
        "source_tick_id": source_tick_id,
        "source_snapshot_id": source_snapshot_id,
    }
    for field, expected in exact_lineage.items():
        if payload.get(field) != expected:
            raise ControlValidationError(f"proposal {field} does not match control lineage")
    if not payload.get("within_guardrails"):
        raise ControlValidationError("proposal is outside operational guardrails")
    proposal_id = str(payload.get("proposal_id") or "")
    if not proposal_id:
        raise ControlValidationError("proposal_id is required")
    expires_at = _aware_datetime(payload.get("expires_at"), "expires_at")
    if expires_at <= now.astimezone(UTC):
        raise ControlValidationError("proposal authorization has expired")
    decisions = payload.get("driver_decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ControlValidationError("proposal has no driver decisions")
    selected = int(payload.get("selected_drivers", -1))
    if selected != len(decisions):
        raise ControlValidationError("selected driver count does not match payload")
    if not 1 <= selected <= max_selected_drivers:
        raise ControlValidationError("selected driver count exceeds the control cap")

    by_wave: dict[tuple[int, int], list[Mapping[str, Any]]] = {}
    driver_ids: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, Mapping):
            raise ControlValidationError("driver decision must be an object")
        driver_id = str(decision.get("driver_id_hash") or "")
        delay = int(decision.get("pause_start_delay_minutes", -1))
        duration = int(decision.get("pause_duration_minutes", -1))
        if not driver_id or driver_id in driver_ids:
            raise ControlValidationError("driver decisions must have unique identities")
        if delay not in {0, 15, 30, 45} or duration not in {15, 30}:
            raise ControlValidationError("driver decision violates the P0 pause policy")
        driver_ids.add(driver_id)
        by_wave.setdefault((delay, duration), []).append(decision)

    checksum = canonical_proposal_checksum(payload)
    event_id = canonical_checksum(
        (proposal_id, run_id, source_tick_id, source_snapshot_id, checksum)
    )[:32]
    controls = []
    for (delay, duration), wave in sorted(by_wave.items()):
        controls.append(
            PauseControl(
                control_id=f"{event_id}:{delay}:{duration}",
                control_event_id=event_id,
                proposal_id=proposal_id,
                driver_ids=tuple(sorted(str(item["driver_id_hash"]) for item in wave)),
                requested_minute=(source_tick_index + 1) * 15 + delay,
                pause_duration_minutes=duration,
                max_start_delay_minutes=45,
                pause_start_delay_minutes=delay,
                baseline_risk_by_driver=tuple(sorted(
                    (str(item["driver_id_hash"]), float(item["baseline_risk"]))
                    for item in wave
                )),
                action_risk_by_driver=tuple(sorted(
                    (str(item["driver_id_hash"]), float(item["action_risk"]))
                    for item in wave
                )),
            )
        )
    valid_from = simulation_time.astimezone(UTC) + timedelta(minutes=15)
    return QueuedControl(
        control_event_id=event_id,
        proposal_id=proposal_id,
        payload_checksum=checksum,
        selected_driver_count=selected,
        authorization_expires_at=min(
            expires_at, now.astimezone(UTC) + timedelta(minutes=CONTROL_AUTHORIZATION_MINUTES)
        ),
        valid_from_simulation_at=valid_from,
        valid_until_simulation_at=valid_from
        + timedelta(minutes=CONTROL_SIMULATION_WINDOW_MINUTES),
        pause_controls=tuple(controls),
    )


class BigQueryControlWriter:
    """Write one immutable trusted control event after strict proposal validation."""

    def __init__(self, client: Any, *, dataset: str, now=lambda: datetime.now(UTC)):
        self.client = client
        self.dataset = dataset
        self.now = now

    def queue(
        self,
        *,
        proposal_id: str,
        run_id: str,
        source_tick_id: str,
        source_snapshot_id: str,
        request_execution_id: str,
        max_selected_drivers: int = DEFAULT_MAX_SELECTED_DRIVERS,
    ) -> QueuedControl:
        if not request_execution_id:
            raise ControlValidationError(
                "queue-control requires a trusted job execution identity"
            )
        rows = self._query(
            f"""
SELECT p.proposal_json, t.scenario_id, t.tick_index, t.simulation_time
FROM `{self.dataset}.intervention_proposals` p
JOIN `{self.dataset}.simulation_ticks` t
  ON t.simulation_run_id = @run_id AND t.tick_id = @source_tick_id
WHERE p.proposal_id = @proposal_id
  AND p.simulation_run_id = @run_id
  AND p.source_tick_id = @source_tick_id
  AND p.source_snapshot_id = @source_snapshot_id
  AND t.snapshot_id = @source_snapshot_id
  AND t.status = 'SUCCEEDED'
  AND COALESCE(t.error_code, '') != 'MODEL_INPUT_OOD'
ORDER BY p.created_at DESC
LIMIT 1
""",
            {
                "proposal_id": proposal_id,
                "run_id": run_id,
                "source_tick_id": source_tick_id,
                "source_snapshot_id": source_snapshot_id,
            },
        )
        if len(rows) != 1:
            raise ControlValidationError(
                "no unique scored proposal matches the requested lineage"
            )
        row = dict(rows[0])
        payload = row["proposal_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        queued = validate_control_payload(
            payload,
            scenario_id=str(row["scenario_id"]),
            run_id=run_id,
            source_tick_id=source_tick_id,
            source_snapshot_id=source_snapshot_id,
            source_tick_index=int(row["tick_index"]),
            now=self.now(),
            simulation_time=row["simulation_time"],
            max_selected_drivers=max_selected_drivers,
        )
        self._query(
            f"""
MERGE `{self.dataset}.simulation_control_events` target
USING (SELECT @control_event_id control_event_id) source
ON target.control_event_id = source.control_event_id
WHEN NOT MATCHED THEN INSERT (
  control_event_id, scenario_id, simulation_run_id, source_tick_id,
  source_snapshot_id, proposal_id, proposal_payload_checksum, status,
  selected_driver_count, requested_by, actor_type, request_execution_id,
  created_at, authorization_expires_at, valid_from_simulation_at,
  valid_until_simulation_at, max_selected_drivers, is_simulated,
  generator_version
) VALUES (
  @control_event_id, @scenario_id, @run_id, @source_tick_id,
  @source_snapshot_id, @proposal_id, @payload_checksum, 'QUEUED',
  @selected_driver_count, @requested_by, @actor_type, @request_execution_id,
  @created_at, @authorization_expires_at, @valid_from_simulation_at,
  @valid_until_simulation_at, @max_selected_drivers, TRUE,
  'stateful-replay-v1'
)
""",
            {
                "control_event_id": queued.control_event_id,
                "scenario_id": str(row["scenario_id"]),
                "run_id": run_id,
                "source_tick_id": source_tick_id,
                "source_snapshot_id": source_snapshot_id,
                "proposal_id": proposal_id,
                "payload_checksum": queued.payload_checksum,
                "selected_driver_count": queued.selected_driver_count,
                "requested_by": TRUSTED_REQUESTED_BY,
                "actor_type": TRUSTED_ACTOR_TYPE,
                "request_execution_id": request_execution_id,
                "created_at": self.now(),
                "authorization_expires_at": queued.authorization_expires_at,
                "valid_from_simulation_at": queued.valid_from_simulation_at,
                "valid_until_simulation_at": queued.valid_until_simulation_at,
                "max_selected_drivers": max_selected_drivers,
            },
        )
        return queued

    def _query(self, sql: str, params: Mapping[str, Any]) -> list[Any]:
        from google.cloud import bigquery

        def parameter(name: str, value: Any):
            kind = (
                "BOOL" if isinstance(value, bool)
                else "INT64" if isinstance(value, int)
                else "TIMESTAMP" if isinstance(value, datetime)
                else "STRING"
            )
            return bigquery.ScalarQueryParameter(name, kind, value)

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                parameter(name, value) for name, value in params.items()
            ],
            maximum_bytes_billed=MAXIMUM_CONTROL_QUERY_BYTES,
            labels={"app": "heatsafe", "component": "simulation-control"},
        )
        result = self.client.query(sql, job_config=job_config).result()
        try:
            return list(result)
        except TypeError:
            return []
