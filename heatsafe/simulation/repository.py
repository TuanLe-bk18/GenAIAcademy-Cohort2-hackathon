"""Deterministic Phase 3 persistence boundary for the HeatSafe replay.

The in-memory implementation is the executable contract used by tests.  The
BigQuery adapter deliberately emits one fenced multi-statement query per tick;
the disposable-dataset probe remains the only proof of provider behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import hashlib
from typing import Callable, Protocol
from uuid import uuid4

from .engine import advance_tick, initialize_state, load_scenario, load_zone_priors
from .models import SimulationState, TickResult
from .randomness import canonical_checksum


MAXIMUM_BYTES_BILLED = 250_000_000
TICK_COUNT = 96


class SimulationRepositoryError(RuntimeError):
    """Base error for a fail-closed repository operation."""


class LeaseConflict(SimulationRepositoryError):
    """Raised when a caller did not win the conditional tick lease."""


class RunConflict(SimulationRepositoryError):
    """Raised when a scenario already has an active run."""


@dataclass(frozen=True, slots=True)
class SimulationRun:
    run_id: str
    scenario_id: str
    scenario_version: str
    seed: int
    status: str
    start_time: datetime
    last_published_tick_index: int | None = None
    last_completed_tick_index: int | None = None
    pending_score_tick_id: str | None = None


@dataclass(frozen=True, slots=True)
class PersistedTick:
    run_id: str
    scenario_id: str
    tick_id: str
    tick_index: int
    simulation_time: datetime
    snapshot_id: str
    status: str = "PENDING"
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    input_checksum: str | None = None
    output_checksum: str | None = None


@dataclass(frozen=True, slots=True)
class TickLease:
    tick_id: str
    owner: str
    fencing_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Publication:
    tick: PersistedTick
    result: TickResult
    driver_rows: tuple[dict[str, object], ...]
    zone_rows: tuple[dict[str, object], ...]
    order_rows: tuple[dict[str, object], ...]


class SimulationRepository(Protocol):
    def start(self, *, scenario_id: str, scenario_version: str, seed: int) -> SimulationRun: ...
    def status(self, scenario_id: str) -> SimulationRun | None: ...
    def pause(self, scenario_id: str) -> SimulationRun: ...
    def resume(self, scenario_id: str) -> SimulationRun: ...
    def acquire_tick_lease(self, run_id: str, tick_id: str, owner: str) -> TickLease: ...
    def publish_tick(self, run_id: str, tick_id: str, owner: str) -> Publication: ...
    def finalize_score(self, run_id: str, tick_id: str, *, succeeded: bool) -> SimulationRun: ...


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _tick_id(run_id: str, tick_index: int) -> str:
    payload = f"simulation-tick:{run_id}:{tick_index}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def _snapshot_id(run_id: str, tick_index: int) -> str:
    payload = f"simulation-snapshot:{run_id}:{tick_index}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def _event_time(state: SimulationState, minute: int | None) -> datetime | None:
    return None if minute is None else state.start_time + timedelta(minutes=minute)


def replay_to_tick(run: SimulationRun, tick_index: int) -> tuple[SimulationState, TickResult]:
    """Rebuild the deterministic Phase 2 state without an opaque state blob."""
    if not 0 <= tick_index < TICK_COUNT:
        raise SimulationRepositoryError("tick index is outside the 96-tick replay")
    fixture = load_scenario(run.scenario_version)
    zones = load_zone_priors()
    state = initialize_state(seed=run.seed, fixture=fixture, zones=zones)
    for _ in range(tick_index):
        state = advance_tick(state, fixture=fixture, zones=zones).state
    return state, advance_tick(state, fixture=fixture, zones=zones)


def publication_rows(run: SimulationRun, tick: PersistedTick, result: TickResult) -> Publication:
    """Map engine truth into lineage-complete BigQuery row dictionaries."""
    state = result.state
    priors = {zone.zone_id: zone for zone in load_zone_priors()}
    driver_rows = tuple(
        {
            "simulation_run_id": run.run_id,
            "scenario_id": run.scenario_id,
            "driver_id_hash": driver.driver_id_hash,
            "last_tick_id": tick.tick_id,
            "event_time": result.simulation_time,
            "zone_id": driver.zone_id,
            "latitude": driver.latitude,
            "longitude": driver.longitude,
            "status": driver.status.value,
            "shift_started_at": None,
            "shift_ends_at": None,
            "current_order_id": driver.current_order_id,
            "current_intervention_id": driver.current_intervention_id,
            "online_minutes_24h": int(driver.schedule_bits).bit_count(),
            "trips_60m": driver.trips_60m,
            "distance_km_60m": driver.distance_km_60m,
            "workload_intensity": 0.0,
            "continuous_exposure_minutes": driver.continuous_exposure_minutes,
            "heat_dose_120m": driver.heat_dose_120m,
            "rest_minutes_120m": driver.rest_minutes_120m,
            "hydration_gap_minutes": driver.hydration_gap_minutes,
            "route_heat_load": 0.0,
            "acclimatization_class": driver.acclimatization_class.value,
            "earnings_60m_vnd": driver.earnings_60m_vnd,
            "platform_contribution_60m_vnd": driver.platform_contribution_60m_vnd,
            "generator_version": state.generator_version,
            "is_simulated": True,
            "updated_at": _utc_now(),
        }
        for driver in state.drivers
    )
    zone_rows = tuple(
        {
            "scenario_id": run.scenario_id,
            "snapshot_id": tick.snapshot_id,
            "zone_id": zone.zone_id,
            "name": priors[zone.zone_id].name,
            "latitude": priors[zone.zone_id].latitude,
            "longitude": priors[zone.zone_id].longitude,
            "observed_at": result.simulation_time,
            "weather_observed_at": result.weather.event_time,
            "operations_observed_at": result.simulation_time,
            "refreshed_at": _utc_now(),
            "temperature_c": result.weather.temperature_c,
            "humidity_percent": result.weather.humidity_percent,
            "heat_index_c": result.weather.heat_index_c,
            "active_drivers": zone.active_drivers,
            "fresh_drivers": zone.fresh_drivers,
            "exposed_2h": zone.exposed_2h,
            "exposed_4h": zone.exposed_4h,
            "forecast_requests_30m": zone.requests_15m * 2,
            "avg_platform_contribution_vnd": priors[zone.zone_id].avg_platform_contribution_vnd,
            "avg_driver_earnings_vnd": priors[zone.zone_id].avg_driver_earnings_vnd,
            "coolstop_name": priors[zone.zone_id].coolstop_name,
            "coolstop_latitude": priors[zone.zone_id].coolstop_latitude,
            "coolstop_longitude": priors[zone.zone_id].coolstop_longitude,
            "online_drivers": zone.online_drivers,
            "idle_drivers": zone.idle_drivers,
            "to_pickup_drivers": zone.to_pickup_drivers,
            "on_trip_drivers": zone.on_trip_drivers,
            "to_coolstop_drivers": zone.to_coolstop_drivers,
            "paused_drivers": zone.paused_drivers,
            "exposed_2_to_4h": zone.exposed_2_to_4h,
            "requests_15m": zone.requests_15m,
            "matched_15m": zone.matched_15m,
            "completed_15m": zone.completed_15m,
            "cancelled_15m": zone.cancelled_15m,
            "unfulfilled_15m": zone.unfulfilled_15m,
            "fulfillment_rate": zone.fulfillment_rate,
            "median_wait_minutes": None,
            "p90_wait_minutes": None,
            "simulation_run_id": run.run_id,
            "tick_id": tick.tick_id,
            "generator_version": state.generator_version,
            "weather_is_simulated": True,
            "operations_is_simulated": True,
            "source": "Deterministic HeatSafe simulation",
        }
        for zone in result.zones
    )
    orders_by_id = {order.order_id: order for order in state.orders}
    order_rows = tuple(
        {
            "event_id": event.event_id,
            "simulation_run_id": run.run_id,
            "tick_id": tick.tick_id,
            "scenario_id": run.scenario_id,
            "order_id": event.order_id,
            "event_time": _event_time(state, event.event_minute),
            "event_type": event.event_type.value,
            "status": orders_by_id.get(event.order_id).status.value if event.order_id in orders_by_id else event.event_type.value,
            "driver_id_hash": event.driver_id_hash,
            "origin_zone_id": event.zone_id,
            "destination_zone_id": event.zone_id,
            "zone_id": event.zone_id,
            "requested_at": _event_time(state, event.event_minute),
            "accepted_at": None,
            "pickup_at": None,
            "dropoff_at": None,
            "cancelled_at": None,
            "distance_km": None,
            "estimated_duration_minutes": None,
            "actual_duration_minutes": None,
            "wait_minutes": None,
            "fare_vnd": None,
            "driver_pay_vnd": None,
            "platform_contribution_vnd": None,
            "generator_version": state.generator_version,
            "is_simulated": True,
        }
        for event in state.events
    )
    return Publication(tick=tick, result=result, driver_rows=driver_rows, zone_rows=zone_rows, order_rows=order_rows)


@dataclass
class InMemorySimulationRepository:
    """Reference implementation used by CLI tests and crash/retry simulations."""

    now: Callable[[], datetime] = _utc_now
    lease_seconds: int = 360
    runs: dict[str, SimulationRun] = field(default_factory=dict)
    scenario_runs: dict[str, str] = field(default_factory=dict)
    ticks: dict[str, PersistedTick] = field(default_factory=dict)
    published: dict[str, Publication] = field(default_factory=dict)

    def start(self, *, scenario_id: str, scenario_version: str, seed: int) -> SimulationRun:
        active = self.status(scenario_id)
        if active and active.status in {"RUNNING", "PAUSED"}:
            raise RunConflict(f"scenario {scenario_id} already has an active run")
        run_id = uuid4().hex
        run = SimulationRun(run_id, scenario_id, scenario_version, seed, "RUNNING", self.now())
        self.runs[run_id] = run
        self.scenario_runs[scenario_id] = run_id
        for index in range(TICK_COUNT):
            tick = PersistedTick(
                run_id, scenario_id, _tick_id(run_id, index), index,
                run.start_time + timedelta(minutes=index * 15), _snapshot_id(run_id, index),
            )
            self.ticks[tick.tick_id] = tick
        return run

    def status(self, scenario_id: str) -> SimulationRun | None:
        run_id = self.scenario_runs.get(scenario_id)
        return self.runs.get(run_id) if run_id else None

    def pause(self, scenario_id: str) -> SimulationRun:
        run = self._require_run_for_scenario(scenario_id)
        if run.status == "COMPLETED":
            raise SimulationRepositoryError("a completed run cannot be paused")
        run = self._replace_run(run, status="PAUSED")
        return run

    def resume(self, scenario_id: str) -> SimulationRun:
        run = self._require_run_for_scenario(scenario_id)
        if run.status != "PAUSED":
            raise SimulationRepositoryError("only a paused run can be resumed")
        return self._replace_run(run, status="RUNNING")

    def acquire_tick_lease(self, run_id: str, tick_id: str, owner: str) -> TickLease:
        run = self._require_run(run_id)
        if run.status != "RUNNING":
            raise SimulationRepositoryError("only a running simulation can acquire a tick")
        tick = self._require_tick(run_id, tick_id)
        if run.pending_score_tick_id and run.pending_score_tick_id != tick_id:
            raise SimulationRepositoryError(
                "the pending score must be finalized before a later tick can run"
            )
        if tick.status in {"SNAPSHOT_READY", "SUCCEEDED"}:
            return TickLease(tick_id, owner, "already-published", self.now())
        now = self.now()
        if tick.lease_expires_at and tick.lease_expires_at > now and tick.lease_owner != owner:
            raise LeaseConflict("a fresh lease is already owned by another caller")
        token = uuid4().hex
        expires = now + timedelta(seconds=self.lease_seconds)
        self.ticks[tick_id] = replace(
            tick, status="LEASED", lease_owner=token, lease_expires_at=expires
        )
        return TickLease(tick_id, owner, token, expires)

    def publish_tick(self, run_id: str, tick_id: str, owner: str) -> Publication:
        run = self._require_run(run_id)
        tick = self._require_tick(run_id, tick_id)
        if tick.status in {"SNAPSHOT_READY", "SUCCEEDED"}:
            return self.published[tick_id]
        if tick.status != "LEASED" or not tick.lease_owner or tick.lease_expires_at is None:
            raise LeaseConflict("tick must have a current lease before publication")
        if tick.lease_expires_at <= self.now():
            raise LeaseConflict("tick lease expired before publication")
        if owner != tick.lease_owner:
            raise LeaseConflict("publication requires the exact fencing token")
        state, result = replay_to_tick(run, tick.tick_index)
        updated = replace(
            tick,
            status="SNAPSHOT_READY",
            input_checksum=canonical_checksum((run.run_id, tick.tick_index, state.minute_index)),
            output_checksum=result.checksum,
        )
        publication = publication_rows(run, updated, result)
        self.ticks[tick_id] = updated
        self.published[tick_id] = publication
        self._replace_run(
            run,
            last_published_tick_index=tick.tick_index,
            pending_score_tick_id=tick.tick_id,
        )
        return publication

    def finalize_score(self, run_id: str, tick_id: str, *, succeeded: bool) -> SimulationRun:
        run = self._require_run(run_id)
        tick = self._require_tick(run_id, tick_id)
        if tick.status == "SUCCEEDED":
            return run
        if tick.status != "SNAPSHOT_READY" or run.pending_score_tick_id != tick_id:
            raise SimulationRepositoryError("only the pending SNAPSHOT_READY tick can be finalized")
        if not succeeded:
            self.ticks[tick_id] = replace(tick, status="SCORE_FAILED")
            return run
        self.ticks[tick_id] = replace(tick, status="SUCCEEDED")
        return self._replace_run(
            run,
            status="COMPLETED" if tick.tick_index == TICK_COUNT - 1 else "RUNNING",
            last_completed_tick_index=tick.tick_index,
            pending_score_tick_id=None,
        )

    def _require_run(self, run_id: str) -> SimulationRun:
        if run_id not in self.runs:
            raise SimulationRepositoryError("unknown simulation run")
        return self.runs[run_id]

    def _require_run_for_scenario(self, scenario_id: str) -> SimulationRun:
        run = self.status(scenario_id)
        if run is None:
            raise SimulationRepositoryError("scenario has no simulation run")
        return run

    def _require_tick(self, run_id: str, tick_id: str) -> PersistedTick:
        tick = self.ticks.get(tick_id)
        if tick is None or tick.run_id != run_id:
            raise SimulationRepositoryError("tick does not belong to this run")
        return tick

    def _replace_run(self, run: SimulationRun, **changes: object) -> SimulationRun:
        updated = replace(run, **changes)
        self.runs[run.run_id] = updated
        return updated


class BigQuerySimulationRepository(InMemorySimulationRepository):
    """BigQuery adapter with an in-memory semantic mirror for deterministic tests.

    Production callers pass an authenticated BigQuery client.  Unit tests pass a
    recording fake and verify the fenced script/byte cap without provider writes.
    """

    def __init__(self, client, *, dataset: str, **kwargs: object):
        super().__init__(**kwargs)
        self.client = client
        self.dataset = dataset

    def start(self, *, scenario_id: str, scenario_version: str, seed: int) -> SimulationRun:
        run = super().start(
            scenario_id=scenario_id, scenario_version=scenario_version, seed=seed
        )
        self._query(
            f"""
BEGIN TRANSACTION;
ASSERT (SELECT COUNT(*) FROM `{self.dataset}.simulation_runs`
  WHERE scenario_id = @scenario_id AND status IN ('RUNNING', 'PAUSED')) = 0;
MERGE `{self.dataset}.simulation_scenario_locks` target
USING (SELECT @scenario_id scenario_id) source ON target.scenario_id = source.scenario_id
WHEN MATCHED THEN UPDATE SET active_simulation_run_id = @run_id,
  generation = target.generation + 1, updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (scenario_id, active_simulation_run_id, generation, updated_at)
  VALUES (@scenario_id, @run_id, 1, CURRENT_TIMESTAMP());
INSERT INTO `{self.dataset}.simulation_runs`
  (simulation_run_id, scenario_id, scenario_version, seed, status,
   simulation_start_at, simulation_end_at, next_simulation_at, tick_minutes,
   speed_multiplier, config_json, created_at, updated_at, is_simulated)
VALUES (@run_id, @scenario_id, @scenario_version, @seed, 'RUNNING', @start_time,
  TIMESTAMP_ADD(@start_time, INTERVAL 24 HOUR), @start_time, 15, 1.0,
  JSON '{{}}', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), TRUE);
INSERT INTO `{self.dataset}.simulation_ticks`
  (simulation_run_id, scenario_id, tick_id, tick_index, simulation_time,
   snapshot_id, status, generator_version, is_simulated)
SELECT @run_id, @scenario_id,
  TO_HEX(SHA256(CONCAT('simulation-tick:', @run_id, ':', CAST(index AS STRING)))),
  index, TIMESTAMP_ADD(@start_time, INTERVAL 15 * index MINUTE),
  TO_HEX(SHA256(CONCAT('simulation-snapshot:', @run_id, ':', CAST(index AS STRING)))),
  'PENDING', @generator_version, TRUE
FROM UNNEST(GENERATE_ARRAY(0, 95)) index;
COMMIT TRANSACTION;
""",
            {
                "run_id": run.run_id,
                "scenario_id": scenario_id,
                "scenario_version": scenario_version,
                "seed": seed,
                "start_time": run.start_time,
                "generator_version": "stateful-replay-v1",
            },
        )
        return run

    def status(self, scenario_id: str) -> SimulationRun | None:
        cached = super().status(scenario_id)
        if cached is not None:
            return cached
        rows = self._query(
            f"""SELECT simulation_run_id, scenario_id, scenario_version, seed, status,
                       simulation_start_at, last_published_tick_index,
                       last_completed_tick_index, pending_score_tick_id
                FROM `{self.dataset}.simulation_runs`
                WHERE scenario_id = @scenario_id
                ORDER BY created_at DESC LIMIT 1""",
            {"scenario_id": scenario_id},
        )
        if not rows:
            return None
        row = dict(rows[0])
        run = SimulationRun(
            row["simulation_run_id"], row["scenario_id"], row["scenario_version"],
            int(row["seed"]), row["status"], row["simulation_start_at"],
            row.get("last_published_tick_index"), row.get("last_completed_tick_index"),
            row.get("pending_score_tick_id"),
        )
        self.runs[run.run_id] = run
        self.scenario_runs[scenario_id] = run.run_id
        self._load_ticks(run)
        return run

    def pause(self, scenario_id: str) -> SimulationRun:
        run = self._require_run_for_scenario(scenario_id)
        self._query(
            f"""UPDATE `{self.dataset}.simulation_runs` SET status = 'PAUSED',
                   updated_at = CURRENT_TIMESTAMP()
                WHERE simulation_run_id = @run_id AND status = 'RUNNING'""",
            {"run_id": run.run_id},
        )
        return super().pause(scenario_id)

    def resume(self, scenario_id: str) -> SimulationRun:
        run = self._require_run_for_scenario(scenario_id)
        self._query(
            f"""UPDATE `{self.dataset}.simulation_runs` SET status = 'RUNNING',
                   updated_at = CURRENT_TIMESTAMP()
                WHERE simulation_run_id = @run_id AND status = 'PAUSED'""",
            {"run_id": run.run_id},
        )
        return super().resume(scenario_id)

    def acquire_tick_lease(self, run_id: str, tick_id: str, owner: str) -> TickLease:
        lease = super().acquire_tick_lease(run_id, tick_id, owner)
        if lease.fencing_token == "already-published":
            return lease
        self._query(
            f"""
UPDATE `{self.dataset}.simulation_ticks`
SET status = 'LEASED', lease_owner = @lease_owner,
    lease_expires_at = @lease_expires_at, started_at = CURRENT_TIMESTAMP()
WHERE simulation_run_id = @run_id AND tick_id = @tick_id
  AND (status = 'PENDING' OR lease_expires_at <= CURRENT_TIMESTAMP());
ASSERT (SELECT COUNT(*) FROM `{self.dataset}.simulation_ticks`
  WHERE simulation_run_id = @run_id AND tick_id = @tick_id
    AND status = 'LEASED' AND lease_owner = @lease_owner
    AND lease_expires_at > CURRENT_TIMESTAMP()) = 1;
""",
            {
                "run_id": run_id,
                "tick_id": tick_id,
                "lease_owner": lease.fencing_token,
                "lease_expires_at": lease.expires_at,
            },
        )
        return lease

    def finalize_score(self, run_id: str, tick_id: str, *, succeeded: bool) -> SimulationRun:
        run = self._require_run(run_id)
        tick = self._require_tick(run_id, tick_id)
        if tick.status != "SUCCEEDED":
            self._query(
                f"""
BEGIN TRANSACTION;
UPDATE `{self.dataset}.simulation_ticks`
SET status = IF(@succeeded, 'SUCCEEDED', 'SCORE_FAILED'),
    finished_at = CURRENT_TIMESTAMP()
WHERE simulation_run_id = @run_id AND tick_id = @tick_id
  AND status = 'SNAPSHOT_READY';
UPDATE `{self.dataset}.simulation_runs`
SET last_completed_tick_index = IF(@succeeded, @tick_index, last_completed_tick_index),
    pending_score_tick_id = IF(@succeeded, NULL, pending_score_tick_id),
    status = IF(@succeeded AND @tick_index = 95, 'COMPLETED', status),
    updated_at = CURRENT_TIMESTAMP()
WHERE simulation_run_id = @run_id AND pending_score_tick_id = @tick_id;
COMMIT TRANSACTION;
""",
                {"run_id": run_id, "tick_id": tick_id, "tick_index": tick.tick_index, "succeeded": succeeded},
            )
        return super().finalize_score(run_id, tick_id, succeeded=succeeded)

    def publish_tick(self, run_id: str, tick_id: str, owner: str) -> Publication:
        publication = super().publish_tick(run_id, tick_id, owner)
        tick = publication.tick
        driver_stage = self._stage_rows("driver", tick.tick_id, publication.driver_rows)
        zone_stage = self._stage_rows("zone", tick.tick_id, publication.zone_rows)
        order_stage = self._stage_rows("order", tick.tick_id, publication.order_rows)
        script = f"""
BEGIN TRANSACTION;
-- Fence publication with the exact current token and unexpired lease.
ASSERT (SELECT COUNT(*) FROM `{self.dataset}.simulation_ticks`
  WHERE simulation_run_id = @run_id AND tick_id = @tick_id
    AND status = 'LEASED' AND lease_owner = @lease_owner
    AND lease_expires_at > CURRENT_TIMESTAMP()) = 1;
-- Staging rows are loaded before this query and expire automatically.
MERGE `{self.dataset}.driver_simulation_state` target
USING `{driver_stage}` source
ON target.simulation_run_id = source.simulation_run_id
 AND target.driver_id_hash = source.driver_id_hash
WHEN MATCHED THEN UPDATE SET last_tick_id = source.last_tick_id, updated_at = source.updated_at
WHEN NOT MATCHED THEN INSERT ROW;
MERGE `{self.dataset}.zone_snapshots_current` target
USING `{zone_stage}` source
ON target.scenario_id = source.scenario_id AND target.zone_id = source.zone_id
WHEN MATCHED THEN UPDATE SET snapshot_id = source.snapshot_id, tick_id = source.tick_id,
  simulation_run_id = source.simulation_run_id, observed_at = source.observed_at
WHEN NOT MATCHED THEN INSERT ROW;
MERGE `{self.dataset}.order_events` target
USING `{order_stage}` source ON target.event_id = source.event_id
WHEN NOT MATCHED THEN INSERT ROW;
UPDATE `{self.dataset}.simulation_ticks`
SET status = 'SNAPSHOT_READY', output_checksum = @output_checksum
WHERE simulation_run_id = @run_id AND tick_id = @tick_id;
COMMIT TRANSACTION;
"""
        self._query(script, {"run_id": run_id, "tick_id": tick_id, "lease_owner": tick.lease_owner, "output_checksum": tick.output_checksum})
        return publication

    def _stage_rows(
        self, kind: str, tick_id: str, rows: tuple[dict[str, object], ...]
    ) -> str:
        """Load a per-tick staging table with one-hour expiry before publication."""
        table_id = f"{self.dataset}.__simulation_stage_{kind}_{tick_id}"
        if not rows:
            return table_id
        from google.cloud import bigquery

        config = bigquery.LoadJobConfig(
            autodetect=True,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            labels={"app": "heatsafe", "component": "simulation_staging"},
        )
        self.client.load_table_from_json(list(rows), table_id, job_config=config).result()
        table = self.client.get_table(table_id)
        table.expires = self.now() + timedelta(hours=1)
        self.client.update_table(table, ["expires"])
        return table_id

    def _load_ticks(self, run: SimulationRun) -> None:
        rows = self._query(
            f"""SELECT simulation_run_id, scenario_id, tick_id, tick_index,
                       simulation_time, snapshot_id, status, lease_owner,
                       lease_expires_at, input_checksum, output_checksum
                FROM `{self.dataset}.simulation_ticks`
                WHERE simulation_run_id = @run_id ORDER BY tick_index""",
            {"run_id": run.run_id},
        )
        for raw in rows:
            row = dict(raw)
            tick = PersistedTick(**row)
            self.ticks[tick.tick_id] = tick

    def _query(self, sql: str, params: dict[str, object]) -> list[object]:
        from google.cloud import bigquery

        def parameter(name: str, value: object):
            kind = "BOOL" if isinstance(value, bool) else "INT64" if isinstance(value, int) else "TIMESTAMP" if isinstance(value, datetime) else "STRING"
            return bigquery.ScalarQueryParameter(name, kind, value)

        configuration = bigquery.QueryJobConfig(
            query_parameters=[parameter(name, value) for name, value in params.items()],
            maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
            labels={"app": "heatsafe", "component": "simulation_publisher"},
        )
        result = self.client.query(sql, job_config=configuration).result()
        if result is None:
            return []
        try:
            return list(result)
        except TypeError:
            return []
