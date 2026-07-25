# HeatSafe P0 Stateful Accelerated Replay Implementation Plan

**Date**: 23-07-26
**Status**: 🔨 PHASE 5R STAGES 1–4 CODE COMPLETE/LOCAL GREEN · 🧪 STAGE 5 BOUNDED PROVIDER GREEN · ⏳ PHASE 6 FAST REPLAY/DYNAMIC UI PLANNED
**Complexity**: COMPLEX — standard complex, one authoritative execution stream
**Execution model**: Sequential phase gates; no phase advances until its proof boundary is green

> **TL;DR:** Replace HeatSafe's regenerated static aggregates with a deterministic, stateful 24-hour Hanoi replay that advances 15 simulated minutes per tick, preserves driver/order identity, applies only authenticated, exact-snapshot SafePause control events to later state, materializes the existing snapshot contract, and scores that exact snapshot. Phase 6 adds a bounded fast-run command and dynamic timeline/playback UI over the existing per-tick history; Cloud Run Service/RAM cache remains out of scope.

## Quick Links

- [1. Context and Goals](#1-context-and-goals)
- [2. Scope](#2-scope)
- [3. Architecture Decisions](#3-architecture-decisions)
- [4. Public Data Contract](#4-public-data-contract)
- [5. Data Flow](#5-data-flow)
- [6. Acceptance Criteria](#6-acceptance-criteria)
- [7. Phase Completion Rules](#7-phase-completion-rules)
- [8. Execution Brief](#8-execution-brief)
- [9. Phased Delivery Plan](#9-phased-delivery-plan)
- [10. Implementation Checklist](#10-implementation-checklist)
- [Touchpoints](#touchpoints)
- [Public Contracts](#public-contracts)
- [Blast Radius](#blast-radius)
- [Verification Evidence](#verification-evidence)
- [Resume and Execution Handoff](#resume-and-execution-handoff)
- [Validate Contract](#validate-contract)

## 1. Context and Goals

HeatSafe currently has a coherent snapshot boundary and snapshot-matched BigQuery ML decision path, but its operational state does not evolve:

- `data/demo_snapshot.json` is one fixed ten-zone Hanoi heatwave snapshot.
- `heatsafe/ingestion.py` refreshes real weather but copies fleet operations from the static snapshot.
- `infra/ml_pipeline.py::score_snapshot` deletes and regenerates the same deterministic drivers from zone aggregates.
- `infra/provision_gcp.py::seed_demo` synthesizes 21 days of demand with a fixed formula, but this history is not driven by a continuing order/driver process.
- Approved actions are auditable and explicitly `SIMULATED`, but they do not change the driver's state at the next snapshot.

P0 creates a simulation substrate under the existing application contract. It must make the demo behave like a coherent operational system without claiming that synthetic risk outcomes are medical evidence or real platform telemetry.

### Goals

1. Advance one configured Hanoi scenario through a full 24-hour replay.
2. Preserve driver identity and order lifecycle across successive ticks.
3. Produce correlated weather, demand, supply, exposure, rest, and economics.
4. Make a trusted, authenticated SafePause control event change later driver availability, rest, exposure, and risk inputs; public audit approval remains non-authoritative.
5. Keep the existing `ZoneSnapshot`, forecast, prediction, optimizer, UI, and audit contracts compatible.
6. Make every tick reproducible, idempotent, inspectable, and safe to retry.
7. Support manual execution first, an opt-in two-minute accelerated-replay Scheduler after verification, and a separately named fifteen-minute real-operations profile.

### Success Metrics

| Metric | P0 target |
|---|---|
| Replay horizon | 24 hours / 96 published ticks |
| Published tick | 15 simulated minutes |
| Internal state resolution | Fifteen one-minute substeps per published tick |
| Wall-clock acceleration | Configurable; accelerated replay default 7.5x via one scheduled tick every two minutes |
| Zones | Existing ten Hanoi zones |
| Driver continuity | 100% of continuing drivers retain the same `driver_id_hash` |
| Tick reproducibility | Same scenario version + seed + tick input produces the same checksum |
| Snapshot coherence | Exactly one `snapshot_id` across all current zones |
| Exposure compatibility | `fresh_drivers + exposed_2h = active_drivers`; `exposed_4h` remains a subset of `exposed_2h` |
| Score coherence | Every prediction references the tick's exact `snapshot_id` |
| Retry behavior | Re-running a successful tick writes no duplicate logical events |
| Safety behavior | Missing/mismatched predictions remain monitoring-only |

## 2. Scope

### In Scope — P0

- Versioned scenario manifest and bounded, source-attributed weather replay fixture.
- Deterministic simulation clock, seeded randomness, tick ledger, and lease/idempotency handling.
- Stateful driver population with shift, zone, status, exposure, rest, hydration, workload, distance, current order, and current intervention.
- Event-sourced request/accept/pickup/dropoff/cancel/complete order lifecycle.
- Time-of-day demand baseline with day type, weather modifier, correlated city shock, zone variation, and over-dispersed counts.
- Driver/order movement between existing zone centroids; no turn-by-turn road routing.
- Projection into existing `weather_observations`, `zone_operations`, `demand_history`, and `zone_snapshots_current`.
- Persisted driver state as the source for `driver_current_features`.
- SafePause assignment lifecycle derived only from a trusted control queue with exact proposal/run/tick/snapshot lineage; existing public proposal/audit rows remain evidence-only.
- Per-tick snapshot scoring using the existing evaluated BigQuery ML model.
- Structured telemetry, status CLI, replay reset/start/tick commands, and verification queries.
- Opt-in Cloud Run simulation job and authenticated Cloud Scheduler trigger.
- Automated tests, BigQuery integration probes, manual UI proof, rollback/disable instructions, and README updates.

### Explicitly Out of Scope

- Apache Airflow, Composer, Pub/Sub streaming, Dataflow, Kafka, or WebSockets.
- Real production dispatch to drivers.
- Model retraining from simulated replay events on every tick.
- Claiming synthetic labels as real-world model accuracy or causal health outcomes.
- Core body temperature, diagnosis, illness labels, or mandatory wearable telemetry.
- Turn-by-turn OSM routing and raw 1-second GPS tracks.
- Importing raw Grab-Posisi, LaDe, TLC, or WorldPop data into production.
- Multi-city support.
- Replacement of the `live` weather scenario.
- Destructive migration or deletion of the existing demo history.

### Assumptions and Constraints

- `heatwave` becomes the stateful replay scenario; `live` retains current behavior.
- The current evaluated `heat_risk_escalation_model` exists before scheduled scoring begins.
- New fields added to existing BigQuery tables are `NULLABLE` for backward compatibility.
- New event tables are partitioned and clustered; current-state tables have an explicit retention/archival policy.
- Historical weather source time is preserved separately from the operational `simulation_time`; the replay clock never depends on wall-clock time.
- Root-level ignored `hanoi_weather.csv` and `hanoi_drivers.csv` are not authoritative inputs and are excluded from P0.
- Heat Index remains the decision model's screening input. Wind, rain, cloud, radiation, and optional UTCI are provenance/context fields, not medical outputs.
- All driver identifiers remain synthetic hashes; no PII is introduced.
- Provisioning and scheduler creation remain non-destructive and opt-in.
- `process/context/all-context.md` and `process/context/tests/all-tests.md` do not exist in this repository. Source files, README verification commands, and the current unittest suite are the planning source of truth.

## 3. Architecture Decisions

### AD-001: Stateful Replay, Not Random Snapshot Regeneration

**Decision:** Each tick loads the last committed driver state, processes fifteen deterministic one-minute substeps, persists events/history, then projects a new current snapshot.

**Rationale:** Identity continuity and state transitions are required for exposure, shift, trip, earnings, and intervention effects. Randomly perturbing zone aggregates cannot provide those relationships.

**Implications:**

- `driver_id_hash` is created once at run start and survives the run.
- New drivers may enter and existing drivers may leave according to shift transitions.
- State at tick `N+1` must be a function of committed state and events at tick `N`.

### AD-002: Determinism Is Defined Per Entity and Event

**Decision:** Seed every stochastic choice from:

```text
hash(scenario_version, run_seed, tick_index, entity_id, event_type)
```

Do not depend on one mutable process-global random stream.

**Rationale:** A retry, batching change, or reordered loop must not change unrelated entities.

**Implications:**

- Same inputs produce the same rows and checksum.
- Tests can reproduce one driver/order transition without replaying the whole fleet.
- The simulator may use standard-library gamma/Poisson sampling to avoid a new runtime dependency.

### AD-003: One Published Tick Contains Fifteen One-Minute Substeps

**Decision:** A tick advances `simulation_time` by 15 minutes, while the engine computes order and driver transitions at one-minute resolution.

**Rationale:** Fifteen-minute data aligns with existing demand history and TimesFM. One-minute internal steps prevent unrealistic instantaneous trip and pause transitions.

**Implications:**

- A 24-hour replay has 96 published ticks.
- The default accelerated-replay cadence of two wall-clock minutes creates a 7.5x demo; the separate real-operations profile uses fifteen minutes.
- The cadence is configuration, not business logic.

### AD-004: Event Tables Are Additive; Existing Snapshot Contract Is a Projection

**Decision:** Add run/tick/current-state/event storage, but continue to serve the UI from `zone_snapshots_current` and continue to score from `driver_current_features`.

**Rationale:** This isolates simulation complexity from the existing repository, decision service, copilot, and UI.

**Implications:**

- P0 does not rename or redefine `fresh_drivers`, `exposed_2h`, or `exposed_4h`.
- Their existing cumulative semantics remain:
  - `fresh_drivers`: active and continuous exposure `< 120` minutes.
  - `exposed_2h`: active and continuous exposure `>= 120` minutes.
  - `exposed_4h`: active and continuous exposure `>= 240` minutes.
- `active_drivers` means available/working supply only: `IDLE + TO_PICKUP + ON_TRIP`.
- `TO_COOLSTOP`, `PAUSED`, and `OFFLINE` are not active supply. `online_drivers` is a new nullable aggregate for all connected non-`OFFLINE` drivers.
- A new nullable `exposed_2_to_4h` may expose the exclusive middle cohort without overloading the public `exposed_2h` contract.

### AD-005: Write Events First, Publish Current Snapshot Last

**Decision:** Pre-create one coordinator row per scenario and all 96 tick-ledger rows. Load deterministic staging rows before publication, then use one BigQuery transaction to revalidate a unique lease fencing token, merge all tick-visible events/history, update current driver state and zone projection, advance the run cursor, and commit the tick ledger last.

**Rationale:** A partially written tick must never publish a mixed current snapshot.

**Tick states:**

```text
PENDING → RUNNING → SNAPSHOT_READY → SCORED → SUCCEEDED
                    └──────────────→ SCORE_FAILED
                    └──────────────→ SUCCEEDED
                                      scoring_outcome=SKIPPED_LOW_RISK
RUNNING with expired lease → RETRYING
```

**Implications:**

- A `SUCCEEDED` tick is a no-op on retry.
- A fresh `RUNNING` lease plus in-transaction owner/expiry revalidation prevents concurrent publication.
- A stale lease retries the same deterministic tick.
- `SCORE_FAILED` keeps the coherent snapshot but the app fails closed to monitoring-only until scoring succeeds.
- Conflicting BigQuery transactions use bounded retry with status re-read; staging tables live in an expiring disposable staging dataset.
- Publication sets `last_published_tick_index` and `pending_score_tick_id` but does not advance `last_completed_tick_index` or `next_simulation_at`.
- A persisted low-risk execution plan may finalize directly from
  `SNAPSHOT_READY` to `SUCCEEDED`, atomically record
  `SKIPPED_LOW_RISK`, clear the pending cursor, and advance completed/next
  cursors without passing through `SCORED`.
- A scoring-success transaction marks the same tick `SUCCEEDED`, clears `pending_score_tick_id`, advances the completed cursor/time, and only then licenses the next tick.
- `tick` always resumes `SNAPSHOT_READY`/`SCORE_FAILED` scoring before simulation. `tick --tick-id <id>` is the bounded operator/test retry surface; targeting a different tick fails closed.

### AD-006: Simulation Scoring Uses Persisted Driver State

**Decision:** Extend `score_snapshot` with an explicit feature source:

```text
simulation | legacy
```

The heatwave replay uses `simulation`; existing live behavior can retain `legacy`.

**Rationale:** Regenerating features from `active_drivers` destroys driver continuity and intervention effects.

**Implications:**

- `driver_current_features` is materialized from the active run's current driver rows.
- Only drivers eligible for current scoring are included.
- Predictions still include all existing counterfactual action choices and exact `snapshot_id`.

### AD-007: Trusted SafePause Control Is Consumed; Public Audit Is Not

**Decision:** Existing `intervention_proposals` and `intervention_events` remain audit-only. The authenticated control job creates an immutable `simulation_control_events` row that references an exact proposal, scenario, run, tick, and snapshot; the simulator records consumption/rejection/expiry in a separate receipt table and applies only valid, receipt-free, policy-bounded requests.

**Rationale:** The public UI is unauthenticated and the current audit service deliberately never dispatches commands. Treating its rows as authoritative simulation inputs would allow arbitrary Internet users to alter fleet state and demo economics.

**Implications:**

- Existing audit rows remain `SIMULATED` / `NOT_APPLICABLE` and are never consumed directly.
- Public monitoring is read-only/approval-disabled by default. The sole P0 writer is the IAM-authenticated `heatsafe-simulation-control` Cloud Run Job, invoked by a trusted operator with the `queue-control` CLI arguments.
- Control rows carry deterministic ID, source lineage, validity/expiry, actor/source, caps, consumption tick/time, and rejection reason.
- New lifecycle statuses are `ASSIGNED`, `TO_COOLSTOP`, `PAUSED`, `COMPLETED`, and `CANCELLED`.
- Pause/recovery parameters are scenario policy, not medical guidance.
- Recovery is gradual; no risk or heat-dose value resets instantly on approval.

### AD-008: Real Weather Shape, Synthetic Operations

**Decision:** Check in a small versioned 15-minute replay fixture derived from an official historical source and include source URL/query, retrieval time, units, timezone, license note, and derivation version in its manifest.

**Rationale:** Weather can be grounded in observed/reanalysis data while fleet operations remain clearly synthetic.

**Implications:**

- A city-level weather curve is shared across zones with stable zone offsets and correlated variation.
- P0 requires temperature, humidity, wind, rain, cloud, and shortwave radiation.
- Each fixture row preserves `source_observed_at`; operational tables map the curve onto `simulation_time`.
- `utci_c` may be stored when source-derived, but is not required by the current model.
- Independent per-zone random weather is forbidden.

### AD-009: Scheduler Is Opt-In and Authenticated

**Decision:** Add a `heatsafe-simulation-tick` Cloud Run Job and an opt-in Cloud Scheduler HTTP trigger calling the Cloud Run Jobs v2 `:run` endpoint with OAuth.

**Rationale:** Cloud Scheduler is sufficient for a once-per-minute prototype tick; Airflow adds no P0 value.

**Implications:**

- Enable `cloudscheduler.googleapis.com` only when requested.
- Use separate identities for the public reader, trusted operator/control writer, simulator runtime, scorer/trainer, deployer, and scheduler caller.
- Grant the scheduler caller only job-level `roles/run.invoker`; it receives no BigQuery, Storage, or Vertex role.
- Default deployment must not silently begin recurring writes or cost.
- Provide explicit create, pause, resume, force-run, and delete commands.

Reference: [Google Cloud — Execute Cloud Run jobs on a schedule](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule).

### AD-010: Incremental Checkpoint Is the Production Hot Path

**Decision:** Replace production replay-from-zero with a versioned, lossless,
per-tick checkpoint. `replay_to_tick()` remains the deterministic oracle,
auditing path, and last-resort recovery mechanism.

**Rationale:** The current repository rebuilds tick `N` by initializing the run
and executing all prior ticks. That is correct but `O(N)` and therefore
incompatible with a bounded accelerated-replay cadence as the replay advances.

**Implications:**

- Tick `0` starts from `initialize_state()`. Tick `N > 0` restores the committed
  end-state checkpoint for tick `N-1`, loads current trusted controls, and calls
  `advance_tick()` once.
- Checkpoints live in a dedicated regional GCS bucket with a 35-day lifecycle;
  the runtime receives `roles/storage.objectCreator` plus
  `roles/storage.objectViewer` only and cannot delete objects. Cleanup uses
  lifecycle management or an explicitly authorized operator. The raw-weather
  bucket is not broadened into a runtime state store.
- Before any checkpoint upload, the leased tick freezes one
  `input_manifest_json` and `input_checksum` containing the predecessor
  generation/state checksum, effective control IDs and payload checksums,
  `input_frozen_at`, run-wide risk model, generator, checkpoint codec, and
  execution-policy versions. Authorization expiry is evaluated against this
  frozen instant, not a later retry wall clock.
- The deterministic object name contains scenario version, run ID, tick index,
  input checksum, and checkpoint format version. Creation uses
  `ifGenerationMatch=0`. A control arriving after input freeze belongs to the
  next tick; it cannot change the bytes of an orphaned current-tick object.
- A checkpoint object is not authoritative until its URI, object generation,
  byte length, payload SHA-256, state checksum, and format version are committed
  in the fenced BigQuery tick transaction.
- Upload-before-transaction failures may leave an orphan object. Retrying must
  verify and reuse an identical orphan; a mismatched object fails closed.
- A missing or corrupt latest checkpoint falls back to the nearest earlier
  verified checkpoint and replays only the delta. If none exists,
  `replay_to_tick()` rebuilds from tick zero.
- The checkpoint codec must preserve Python float values losslessly. The
  existing canonical checksum representation formats floats to six decimal
  places and is suitable for comparison, not for persistence round-trips.
- Trusted controls are loaded after checkpoint restore. Checkpoints contain the
  resulting simulation state, never the authoritative control queue or
  wall-clock authorization decision.

### AD-011: Risk-Adaptive Work Changes Compute Cadence, Not State Cadence

**Decision:** Every invocation still advances exactly one 15-minute simulation
tick, validates invariants, publishes the coherent operational snapshot, and
commits a checkpoint. A deterministic execution policy controls only expensive
forecast, prediction, and explanation work.

**Rationale:** Daytime is the core intervention window, but the current heatwave
fixture remains at least `EXTREME_CAUTION` overnight and heat/exposure state
persists across daypart boundaries. Hard-coded `09:00–18:00` skipping would
discard valid risk and recovery behavior.

**Modes:**

| Mode | Entry rule | Expensive work |
|---|---|---|
| `FULL` | Current or 30-minute fixture look-ahead reaches `DANGER`; an eligible exposure cohort, pending control, or active intervention exists; or policy escalates after a forecast miss | Exact-snapshot ML prediction every tick; forecast refresh by cadence/anomaly; explanation by bounded cohort/cadence |
| `MONITOR` | `EXTREME_CAUTION` or pre-warm window, with no `FULL` trigger | Publish/checkpoint/current features every tick; zero inference and monitoring-only; reuse a provenance-tagged forecast horizon |
| `RECOVERY` | Below `EXTREME_CAUTION` for the configured hysteresis window and no eligible cohort/control/intervention remains | Publish/checkpoint and recovery telemetry; skip TimesFM/ML |

**Implications:**

- The policy is derived from simulation time and state, never Cloud Run wall
  clock.
- Exit from `FULL` requires hysteresis; one cool tick cannot suppress scoring.
- A skipped score is explicit `SKIPPED_LOW_RISK`, clears the pending-score
  cursor safely, and keeps the UI monitoring-only for that exact snapshot. It
  may never surface a previous tick's prediction as current.
- Reused forecasts carry `forecast_source_tick_id`,
  `forecast_source_snapshot_id`, and age. Refresh is forced when mode escalates,
  the horizon is exhausted, or actual demand materially deviates from the
  forecast under the validated anomaly rule.
- The accelerated-replay SLO is measured on representative `FULL` ticks. Low-risk skips
  cannot mask a slow critical daytime path.

## 4. Public Data Contract

### 4.0 New Table: `simulation_scenario_locks`

Current coordinator table; cluster: `scenario_id`.

| Field | Type | Mode | Meaning |
|---|---|---|---|
| `scenario_id` | STRING | REQUIRED | Pre-provisioned singleton key |
| `active_simulation_run_id` | STRING | NULLABLE | Current run, if any |
| `generation` | INT64 | REQUIRED | Monotonic fencing generation |
| `updated_at` | TIMESTAMP | REQUIRED | Wall-clock coordination timestamp |

Every `start` transaction must conditionally mutate this existing row. BigQuery logical keys are not treated as enforced uniqueness.

### 4.1 New Table: `simulation_runs`

Partition: `created_at`
Cluster: `scenario_id`, `status`

| Field | Type | Mode | Meaning |
|---|---|---|---|
| `simulation_run_id` | STRING | REQUIRED | Immutable run identity |
| `scenario_id` | STRING | REQUIRED | `heatwave` for P0 |
| `scenario_version` | STRING | REQUIRED | Versioned manifest identifier |
| `seed` | INT64 | REQUIRED | Run seed |
| `status` | STRING | REQUIRED | `READY`, `RUNNING`, `PAUSED`, `COMPLETED`, `FAILED` |
| `simulation_start_at` | TIMESTAMP | REQUIRED | First simulated instant |
| `simulation_end_at` | TIMESTAMP | REQUIRED | Exclusive replay end |
| `next_simulation_at` | TIMESTAMP | REQUIRED | Next published tick target |
| `tick_minutes` | INT64 | REQUIRED | `15` in P0 |
| `speed_multiplier` | FLOAT64 | REQUIRED | Display/operations metadata |
| `last_published_tick_index` | INT64 | NULLABLE | Last atomically published snapshot, whether scored or not |
| `last_completed_tick_index` | INT64 | NULLABLE | Resume cursor |
| `pending_score_tick_id` | STRING | NULLABLE | Published tick that must be scored/retried before any later simulation |
| `risk_model_version` | STRING | NULLABLE | Run-wide evaluated heat-risk model frozen before the first FULL score |
| `forecast_context_version` | STRING | NULLABLE | Idempotent simulation-only TimesFM seed contract |
| `forecast_context_seeded_at` | TIMESTAMP | NULLABLE | Seed completion time after range/count assertions |
| `forecast_context_point_count` | INT64 | NULLABLE | Verified points per zone for the selected context |
| `config_json` | JSON | REQUIRED | Frozen scenario/runtime config |
| `created_at` | TIMESTAMP | REQUIRED | Creation time |
| `updated_at` | TIMESTAMP | REQUIRED | Last state transition |
| `is_simulated` | BOOL | REQUIRED | Always `TRUE` |

### 4.2 New Table: `simulation_ticks`

Partition: `simulation_time`
Cluster: `scenario_id`, `simulation_run_id`, `status`

| Field | Type | Mode | Meaning |
|---|---|---|---|
| `simulation_run_id` | STRING | REQUIRED | Owning run |
| `scenario_id` | STRING | REQUIRED | Scenario |
| `tick_id` | STRING | REQUIRED | Deterministic `run_id:tick_index` |
| `tick_index` | INT64 | REQUIRED | `0..95` |
| `simulation_time` | TIMESTAMP | REQUIRED | Published time |
| `snapshot_id` | STRING | REQUIRED | Deterministic current snapshot ID |
| `status` | STRING | REQUIRED | Tick state |
| `lease_owner` | STRING | NULLABLE | Job execution identity |
| `lease_expires_at` | TIMESTAMP | NULLABLE | Retry safety |
| `input_checksum` | STRING | NULLABLE | Filled at lease acquisition; previous-state/config checksum |
| `input_manifest_json` | JSON | NULLABLE | Frozen predecessor, controls, model, generator, codec, and policy inputs |
| `input_frozen_at` | TIMESTAMP | NULLABLE | Wall-clock decision instant used for authorization validity |
| `output_checksum` | STRING | NULLABLE | Canonical output checksum |
| `checkpoint_uri` | STRING | NULLABLE | Deterministic GCS object committed for this end-of-tick state |
| `checkpoint_generation` | INT64 | NULLABLE | Exact immutable GCS object generation |
| `checkpoint_format_version` | STRING | NULLABLE | Versioned lossless codec identifier |
| `checkpoint_payload_sha256` | STRING | NULLABLE | SHA-256 of the stored compressed payload |
| `checkpoint_state_checksum` | STRING | NULLABLE | Canonical logical checksum of restored `SimulationState` |
| `checkpoint_size_bytes` | INT64 | NULLABLE | Stored object size for latency/cost evidence |
| `execution_mode` | STRING | NULLABLE | `FULL`, `MONITOR`, or `RECOVERY` |
| `execution_reasons_json` | JSON | NULLABLE | Deterministic policy reasons and thresholds |
| `scoring_outcome` | STRING | NULLABLE | `COMPLETED`, `SKIPPED_LOW_RISK`, or failure code |
| `forecast_source_tick_id` | STRING | NULLABLE | Tick that generated a reused TimesFM horizon |
| `driver_count` | INT64 | NULLABLE | Published driver count |
| `order_event_count` | INT64 | NULLABLE | Tick event count |
| `started_at` | TIMESTAMP | NULLABLE | Wall-clock start |
| `finished_at` | TIMESTAMP | NULLABLE | Wall-clock completion |
| `error_code` | STRING | NULLABLE | Stable failure code |
| `error_message` | STRING | NULLABLE | Bounded diagnostic |
| `generator_version` | STRING | REQUIRED | Simulator version |
| `is_simulated` | BOOL | REQUIRED | Always `TRUE` |

Logical uniqueness: `(simulation_run_id, tick_index)`.

### 4.3 New Table: `driver_simulation_state`

Current-state table; cluster: `scenario_id`, `simulation_run_id`, `zone_id`, `driver_id_hash`.

| Field group | Fields |
|---|---|
| Identity | `simulation_run_id`, `scenario_id`, `driver_id_hash`, `last_tick_id`, `event_time` |
| Location | `zone_id`, `latitude`, `longitude` |
| Lifecycle | `status`, `shift_started_at`, `shift_ends_at`, `current_order_id`, `current_intervention_id` |
| Work | `online_minutes_24h`, `trips_60m`, `distance_km_60m`, `workload_intensity` |
| Heat | `continuous_exposure_minutes`, `heat_dose_120m`, `rest_minutes_120m`, `hydration_gap_minutes`, `route_heat_load`, `acclimatization_class` |
| Economics | `earnings_60m_vnd`, `platform_contribution_60m_vnd` |
| Provenance | `generator_version`, `is_simulated`, `updated_at` |

Allowed `status` values:

```text
OFFLINE
IDLE
TO_PICKUP
ON_TRIP
TO_COOLSTOP
PAUSED
```

Logical uniqueness: `(simulation_run_id, driver_id_hash)`.

### 4.4 New Table: `order_events`

Partition: `event_time`
Cluster: `scenario_id`, `simulation_run_id`, `zone_id`, `order_id`

| Field group | Fields |
|---|---|
| Identity | `event_id`, `simulation_run_id`, `tick_id`, `scenario_id`, `order_id` |
| Lifecycle | `event_time`, `event_type`, `status`, `driver_id_hash` |
| Geography | `origin_zone_id`, `destination_zone_id`, `zone_id` |
| Service | `requested_at`, `accepted_at`, `pickup_at`, `dropoff_at`, `cancelled_at` |
| Metrics | `distance_km`, `estimated_duration_minutes`, `actual_duration_minutes`, `wait_minutes` |
| Economics | `fare_vnd`, `driver_pay_vnd`, `platform_contribution_vnd` |
| Provenance | `generator_version`, `is_simulated` |

Allowed `event_type` values:

```text
REQUESTED
MATCHED
PICKED_UP
COMPLETED
CANCELLED
UNFULFILLED
```

Logical uniqueness: `event_id`; the event ID is deterministic from run/order/event type/event time.

### 4.5 New Table: `driver_intervention_events`

Partition: `event_time`
Cluster: `scenario_id`, `simulation_run_id`, `intervention_id`, `driver_id_hash`

Fields:

```text
event_id
simulation_run_id
tick_id
scenario_id
intervention_id
proposal_id
driver_id_hash
zone_id
event_time
event_type
pause_start_delay_minutes
planned_duration_minutes
completed_rest_minutes
coolstop_name
baseline_risk_probability
action_risk_probability
earnings_delta_vnd
is_simulated
generator_version
```

Logical uniqueness: `event_id`.

### 4.6 New Table: `simulation_control_events`

Partition: `created_at`
Cluster: `scenario_id`, `simulation_run_id`, `status`, `proposal_id`

| Field | Type | Mode | Meaning |
|---|---|---|---|
| `control_event_id` | STRING | REQUIRED | Deterministic trusted-control identity |
| `scenario_id` | STRING | REQUIRED | Exact scenario |
| `simulation_run_id` | STRING | REQUIRED | Exact active run |
| `source_tick_id` | STRING | REQUIRED | Tick whose proposal is being authorized |
| `source_snapshot_id` | STRING | REQUIRED | Exact scored snapshot |
| `proposal_id` | STRING | REQUIRED | Existing audit proposal reference |
| `proposal_payload_checksum` | STRING | REQUIRED | Canonical immutable proposal/driver-decision checksum |
| `status` | STRING | REQUIRED | Immutable `AUTHORIZED`; legacy `QUEUED` rows remain readable during migration and are never updated by the runtime |
| `selected_driver_count` | INT64 | REQUIRED | Frozen authorized selection count |
| `requested_by` | STRING | REQUIRED | Fixed control-job service identity; not caller input |
| `actor_type` | STRING | REQUIRED | `TRUSTED_CONTROL_JOB` |
| `request_execution_id` | STRING | REQUIRED | Cloud Run control-job execution correlation |
| `created_at` | TIMESTAMP | REQUIRED | Wall-clock creation time |
| `authorization_expires_at` | TIMESTAMP | REQUIRED | Wall-clock authorization TTL |
| `valid_from_simulation_at` | TIMESTAMP | REQUIRED | Earliest replay time for consumption |
| `valid_until_simulation_at` | TIMESTAMP | REQUIRED | Latest replay time for consumption |
| `max_selected_drivers` | INT64 | REQUIRED | Bounded fan-out |
| `is_simulated` | BOOL | REQUIRED | Always `TRUE` |
| `generator_version` | STRING | REQUIRED | Control schema/generator version |

Only the `heatsafe-simulation-control` Cloud Run Job may create these immutable request rows. A trusted operator/group receives job-level invoke permission; the public principal is denied. The job records its fixed service identity and execution ID, while Cloud Audit Logs preserve the authenticated invoker. It loads the referenced immutable proposal, validates exact scenario/run/tick/snapshot and current predictions, canonicalizes `proposal_json` plus selected driver decisions, and stores the checksum/count.

### 4.7 New Table: `simulation_control_consumptions`

Partition: `recorded_at`
Cluster: `scenario_id`, `simulation_run_id`, `outcome`, `control_event_id`

| Field | Type | Mode | Meaning |
|---|---|---|---|
| `consumption_id` | STRING | REQUIRED | Deterministic receipt identity |
| `control_event_id` | STRING | REQUIRED | Immutable request reference |
| `scenario_id` | STRING | REQUIRED | Exact scenario |
| `simulation_run_id` | STRING | REQUIRED | Exact run |
| `consumed_by_tick_id` | STRING | NULLABLE | Consumer tick; null for pre-consumption rejection/expiry |
| `outcome` | STRING | REQUIRED | `APPLIED`, `REJECTED`, or `EXPIRED` |
| `recorded_at` | TIMESTAMP | REQUIRED | Wall-clock receipt time |
| `rejection_reason` | STRING | NULLABLE | Stable bounded reason |
| `generator_version` | STRING | REQUIRED | Simulator version |
| `is_simulated` | BOOL | REQUIRED | Always `TRUE` |

The simulator has read-only access to immutable control requests and write
access only to this receipt table. One deterministic receipt per control
provides the anti-join/idempotency boundary; a control is pending only when no
receipt exists. The simulator never changes
`simulation_control_events.status`. Replay fallback reconstructs applied
history from `APPLIED` receipts joined to their consumed tick, and ignores
`REJECTED`/`EXPIRED` receipts.

### 4.8 Existing Table Extensions

All added fields are `NULLABLE`.

**`weather_observations`:**

```text
simulation_run_id, tick_id, source_observed_at, source_next_observed_at,
source_interpolation_fraction, apparent_temperature_c,
source_temperature_c, temperature_adjustment_c, station_peak_anchor_c,
wind_speed_mps, wind_gust_mps,
precipitation_mm, cloud_cover_pct, shortwave_radiation_wm2,
utci_c, derivation_version, generator_version
```

**`zone_operations`:**

```text
simulation_run_id, tick_id,
online_drivers, idle_drivers, to_pickup_drivers, on_trip_drivers,
to_coolstop_drivers, paused_drivers, exposed_2_to_4h,
requests_15m, matched_15m, completed_15m, cancelled_15m, unfulfilled_15m,
median_wait_minutes, p90_wait_minutes, fulfillment_rate, generator_version
```

**`demand_history`:**

```text
simulation_run_id, tick_id, generator_version
```

**`driver_state_history`:**

```text
simulation_run_id, tick_id, driver_status, heat_dose_120m,
acclimatization_class, current_order_id, current_intervention_id,
earnings_60m_vnd, platform_contribution_60m_vnd, generator_version
```

**`driver_current_features`:**

```text
simulation_run_id, tick_id, driver_status, heat_dose_120m,
acclimatization_class, generator_version
```

**`driver_risk_predictions` and `zone_demand_forecasts`:**

```text
simulation_run_id, tick_id, generator_version
```

`zone_demand_forecasts` additionally receives:

```text
forecast_source_tick_id, forecast_source_snapshot_id,
forecast_source_prediction_run_id, forecast_source_generated_at,
forecast_age_minutes, forecast_reused
```

For a reused horizon, the row's existing `snapshot_id` and `tick_id` identify
the current published snapshot, while the new source fields identify where the
forecast was actually generated. `generated_at` is not rewritten to make reused
data appear fresh. In the simulation branch, `prediction_run_id` is the
deterministic current-tick materialization batch identity. First derive an
independent TimesFM source-generation identity:

```text
forecast_source_prediction_run_id:
  hash(run_id, source_tick_id, timesfm_model, context_window,
       horizon, forecast_context_version)
```

Then derive the materialization identity:

```text
sim-forecast-materialization:
  hash(run_id, current_tick_id, source_prediction_run_id, policy_version)
```

Fresh and reused rows both store the independent source-generation ID; reuse
preserves it while deriving a new current-tick materialization ID. Therefore
neither path is circular, collides with, or mutates the source batch.
`forecast_age_minutes` means replay-time age only:
`TIMESTAMP_DIFF(current_simulation_time, source_simulation_time, MINUTE)`,
persisted as a non-negative multiple of 15. Wall-clock generation time remains
separate provenance and is never used for horizon validity.

**`intervention_proposals` and `intervention_events`:**

```text
scenario_id, source_snapshot_id, simulation_run_id, source_tick_id,
expires_at
```

These fields improve audit lineage only. They do not authorize simulator control.

**`zone_snapshots_current`:**

```text
simulation_run_id, tick_id, generator_version,
online_drivers, idle_drivers, to_pickup_drivers, on_trip_drivers,
to_coolstop_drivers, paused_drivers, exposed_2_to_4h,
requests_15m, matched_15m, completed_15m, cancelled_15m, unfulfilled_15m,
median_wait_minutes, p90_wait_minutes, fulfillment_rate
```

### 4.9 Existing Snapshot Projection

`zone_snapshots_current` keeps its current caller-visible fields and semantics; the
new nullable lineage fields are ignored by existing `ZoneSnapshot` parsing.

For a simulation tick:

```text
scenario_id = "heatwave"
snapshot_id = deterministic tick snapshot ID
observed_at = simulation_time
weather_observed_at = simulation_time
operations_observed_at = simulation_time
active_drivers = IDLE + TO_PICKUP + ON_TRIP
online_drivers = every status except OFFLINE
fresh_drivers = active drivers with exposure < 120
exposed_2h = active drivers with exposure >= 120
exposed_4h = active drivers with exposure >= 240
exposed_2_to_4h = exposed_2h - exposed_4h
forecast_requests_30m = expected requests in the next two 15-minute slots
weather_is_simulated = FALSE only when replayed values are directly source-derived
operations_is_simulated = TRUE
source = historical weather replay + stateful simulated fleet operations
```

Compatibility invariants:

```text
fresh_drivers + exposed_2h = active_drivers
0 <= exposed_4h <= exposed_2h
exposed_2_to_4h = exposed_2h - exposed_4h
active_drivers <= online_drivers
```

## 5. Data Flow

```text
Versioned scenario manifest + historical weather fixture
                         |
                         v
              start/resume simulation run
                         |
Cloud Scheduler ---> acquire deterministic tick lease
                         |
                         v
       restore previous committed checkpoint
       (tick 0 initializes; oracle fallback is explicit)
                         |
                         v
      load authoritative controls for the requested tick
                         |
                         v
              fifteen one-minute substeps
       +-----------------+------------------+
       |                 |                  |
       v                 v                  v
 weather state       order events       driver transitions
       |                 |                  |
       +-----------------+------------------+
                         |
       authenticated exact-snapshot control events
                         |
                         v
       append events/history + update current state
                         |
                         v
 project weather + zone operations + demand + current snapshot
                         |
                         v
 serialize/upload/verify end-state checkpoint
                         |
                         v
 fenced publication commits rows + exact checkpoint metadata
                         |
                         v
 choose FULL / MONITOR / RECOVERY from simulation state
                         |
                         v
 materialize driver_current_features from persistent driver state
                         |
                         v
 FULL: run exact-snapshot inference
 MONITOR/RECOVERY: skip inference and record monitoring-only outcome
                         |
                         v
 score exact snapshot with existing BQML counterfactual pipeline
                         |
                         v
 Streamlit repository loads one coherent snapshot_id
                         |
                         v
 user refreshes UI and sees changed weather/supply/exposure/risk/trade-offs
```

## 6. Acceptance Criteria

### Functional

- **AC-01:** `start` creates one active run from a versioned scenario and creates its initial persistent driver population.
- **AC-02:** `tick` advances exactly 15 simulated minutes and never uses wall-clock time as simulation state.
- **AC-03:** Successive ticks retain continuing driver IDs and valid status transitions.
- **AC-04:** Orders follow valid lifecycle transitions; no completion precedes pickup and no order is assigned to two drivers.
- **AC-05:** Demand, weather, and supply vary over time while remaining reproducible for the same scenario and seed.
- **AC-06:** Every current zone shares exactly one tick `snapshot_id`, and snapshot timestamps equal the simulation time.
- **AC-07:** `fresh_drivers + exposed_2h = active_drivers`, `0 <= exposed_4h <= exposed_2h`, and `exposed_2_to_4h = exposed_2h - exposed_4h` for every zone.
- **AC-08:** Every `FULL` heatwave tick reads persistent current driver state
  and produces predictions for the exact active snapshot. A policy-authorized
  skip records `SKIPPED_LOW_RISK`, produces no current prediction, and remains
  monitoring-only.
- **AC-09:** Only an authenticated, unexpired, unconsumed control row with exact proposal/scenario/run/tick/snapshot lineage may create per-driver lifecycle events and change later state. Public audit approval alone has no control authority.
- **AC-10:** Retrying a successful tick does not duplicate order, driver-history, intervention, prediction, or snapshot rows.
- **AC-11:** A scoring failure leaves the snapshot coherent and the UI in monitoring-only mode.
- **AC-12:** Completing all 96 ticks marks the run `COMPLETED` and stops further advancement.
- **AC-13:** `live` ingestion/scoring behavior and snapshot-mode fallback tests continue to pass.
- **AC-14:** Scheduler creation is opt-in, authenticated, pauseable, and documented.
- **AC-24:** A lease fencing token is revalidated inside the same publication transaction; an expired/late owner cannot commit.
- **AC-25:** Heatwave TimesFM context ends at or before `simulation_time` and its horizon starts after it; live forecasting preserves wall-clock behavior.
- **AC-26:** Tick-scoped predictions and forecast decisions are deterministic,
  retained for the declared evidence window, and are not deleted by a later
  tick retry. A reused forecast retains its original source tick/snapshot and
  exposes its age.
- **AC-27:** Project, dataset, bucket, table, scenario, and model identifiers are validated before interpolation into SQL or resource paths.
- **AC-28:** Public reader, trusted operator, simulator, scorer/trainer, deployer, and Scheduler caller permissions are separated; the public identity cannot write simulation control state.
- **AC-29:** The terminal tick produces an operator alert/terminal signal and the runbook pauses or deletes the recurring schedule within the declared SLA; later dispatches remain no-op.
- **AC-30:** One mandatory production-path replay publishes exactly tick indices `0..95`; invocation 97 is a measured no-op with unchanged state/event/prediction/checksum counts.

### Data Quality

- **AC-15:** Weather values are source-attributed, timezone-aware, unit-validated, and spatially correlated.
- **AC-16:** Each tick passes non-negative count, valid state, exposure partition, demand reconciliation, and snapshot-coherence checks.
- **AC-17:** `requests = matched + cancelled + unfulfilled` at the defined 15-minute aggregation boundary; completed trips are reported separately by completion time.
- **AC-18:** Simulation-produced state/event rows carry `simulation_run_id`, the applicable `tick_id` or `source_tick_id`, `generator_version`, and `is_simulated`; coordinator/run metadata use their explicitly defined provenance fields.
- **AC-19:** No field or UI copy represents synthetic risk as diagnosis, observed illness, or proven incident reduction.

### Operational

- **AC-20:** Measured one-tick `duration_ms` stays within the configured SLO and Cloud Run Job timeout for the ten-zone demo fleet; lease TTL exceeds the allowed runtime plus safety margin.
- **AC-21:** Failed/stale leases can be retried safely; fresh concurrent leases do not double-process.
- **AC-22:** Structured Cloud Logging emits execution ID, run, tick, snapshot, row-count, checksum, duration, scoring, invariant, lease, and terminal/no-op outcomes under a versioned schema.
- **AC-23:** Deployment can pause/delete the scheduler and job IAM binding without deleting run, snapshot, prediction, audit, or historical evidence.

## 7. Phase Completion Rules

A phase is NOT complete until:

1. **Integration Test** — it works with the existing system pieces.
2. **Manual Test** — the documented command or user flow works.
3. **Data Verification** — database/state changes are queried and confirmed.
4. **Error Handling** — failure and retry behavior are exercised.
5. **User Confirmation** — the user says the phase works.

Status meanings:

- ⏳ PLANNED — not started.
- 🔨 CODE DONE — written but not tested end-to-end.
- 🧪 TESTING — currently being tested.
- ✅ VERIFIED — tested and user-confirmed.
- 🚧 BLOCKED — an issue prevents completion.

After each phase, document:

- [ ] Automated test command and result.
- [ ] Manual test performed.
- [ ] BigQuery/state query and result.
- [ ] Failure case exercised.
- [ ] User confirmation received.

Do not mark a phase ✅ VERIFIED from build, compile, file existence, or HTTP 200 alone.

## 8. Execution Brief

### Phase 1 — Contract and Scenario Foundation

Add schemas, config, scenario manifest, real-weather fixture contract, and deterministic identifiers without switching the runtime.

**Proof boundary:** provisioning is additive; fixture validates; all existing tests remain green.

### Phase 2 — Local Deterministic Engine

Implement the clock, per-entity RNG, demand/order lifecycle, driver state machine, heat/recovery transitions, intervention transition rules, aggregation, and invariant validator as pure Python.

**Proof boundary:** two same-seed replays match exactly; different seeds vary bounded details; transitions and invariants pass locally.

### Phase 3 — BigQuery Persistence and Snapshot Projection

Implement run start/resume, lease handling, event/history writes, current-state update, and projection into existing HeatSafe tables.

**Proof boundary:** one tick transaction creates a coherent snapshot; an exact targeted retry does not republish, a fake-success finalizer advances the cursor once, and the disposable concurrency/rollback probe is green. Two changing scored snapshots are proven in Phase 4.

### Phase 4 — Scoring and Closed-Loop Intervention

Read current features from persistent driver state, score the exact snapshot, consume simulated approvals, and apply pause/recovery effects to later ticks.

**Proof boundary:** a controlled approval changes only selected drivers in the next ticks and predictions remain snapshot-matched.

### Phase 5 — Cloud Run Job and Optional Scheduler

Package the tick CLI as a Cloud Run Job, create a least-privilege scheduler trigger behind an explicit flag, and document pause/resume/force-run.

**Proof boundary:** one authenticated scheduler dispatch advances exactly one tick; a concurrent dispatch does not duplicate it.

### Phase 6 — End-to-End Replay, UI Proof, and Closeout

Run a bounded replay, verify distributions/invariants/checksums, inspect the UI across multiple ticks, test failure recovery, and update operational documentation.

**Proof boundary:** the user confirms that the demo visibly changes while preserving decision safety and provenance.

### Expected Outcome

- HeatSafe's heatwave scenario evolves through a repeatable operational day.
- Driver and order state persist between refreshes.
- SafePause has visible downstream state and risk-input effects.
- Existing UI and copilot contracts continue to work.
- The simulator can run manually, every two minutes for accelerated replay, or
  every fifteen minutes under the separately validated real-operations profile.
- All data remains clearly labelled as simulated where appropriate.

## 9. Phased Delivery Plan

### Phase 1 — Contract and Scenario Foundation

**Status:** ✅ VERIFIED — automated/disposable-cloud proof green and user-confirmed 24-07-2026
**Dependencies:** Validate Contract approved
**Estimate:** 0.5–1 day

#### Stage 0: Pre-Phase Research

1. Re-read `infra/provision_gcp.py`, `heatsafe/bigquery_io.py`, `heatsafe/config.py`, `.env.example`, and current table contents.
2. Freeze a field-by-field BigQuery name/type/mode/partition/cluster matrix; every extension to a non-empty table must be `NULLABLE`.
3. Add a schema preflight that rejects existing name/type/mode/partition/cluster conflicts instead of silently accepting them.
4. Select and record the exact historical weather source/date/query/checksum, timezone mapping, units, ranges, 96 expected timestamps, zone-offset representation, and derivation version; do not treat ignored root CSVs as source data.
5. Define legacy-write policy: unchanged seed/live rows preserve simulation lineage on a matched replay key, and legacy ingestion may not clear lineage by staging omitted nullable fields as `NULL`.
6. Define a `--schema-only` disposable-dataset path that never calls `ensure_bucket()`.
7. Present final schema/fixture diff and stop for approval.

#### Implementation

1. Add simulation settings and strict parsing to `heatsafe/config.py`.
2. Add the eight new tables and nullable extensions to `table_schemas()`, partitioning, and clustering maps.
3. Add `infra/provision_gcp.py --schema-only`: create/update only the selected disposable dataset, skip `ensure_bucket()`, print schema/config read-back, and support explicit cleanup of only that dataset.
4. Add table-specific legacy write policies: history/event writers never match or clear simulation lineage; demand history keys include run isolation for replay; intentional static/live current-snapshot replacement explicitly clears stale simulation lineage.
5. Extend `merge_rows()` with an explicit validated update-field policy or use dedicated writers; omitted nullable fields must not implicitly become destructive updates.
6. Add `data/scenarios/hanoi_heatwave_v1/manifest.json`.
7. Add a bounded `weather_15m.csv` fixture with source attribution and validated units.
8. Add scenario loading/validation models under `heatsafe/simulation/`.
9. Add schema conflict, partition/cluster, legacy seed/live compatibility, identifier negative/injection, image-context, and fixture tests.
10. Update `.env.example` with disabled-safe simulator defaults.

#### Test Procedure

```bash
venv/bin/python -m unittest tests.test_simulation_contract -v
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m compileall -q app.py heatsafe infra
venv/bin/python -m pip check
```

Run provisioning against a disposable/test dataset and verify existing tables retain their rows.

```bash
venv/bin/python infra/provision_gcp.py \
  --schema-only \
  --dataset "<explicit-disposable-dataset>"
```

#### Data Verification

```sql
SELECT table_name
FROM `<project>.<dataset>.INFORMATION_SCHEMA.TABLES`
WHERE table_name IN (
  'simulation_runs',
  'simulation_ticks',
  'simulation_scenario_locks',
  'driver_simulation_state',
  'order_events',
  'driver_intervention_events',
  'simulation_control_events',
  'simulation_control_consumptions'
)
ORDER BY table_name;
```

```sql
SELECT table_name, column_name, data_type, is_nullable
FROM `<project>.<dataset>.INFORMATION_SCHEMA.COLUMNS`
WHERE column_name IN ('simulation_run_id', 'tick_id', 'wind_speed_mps')
ORDER BY table_name, column_name;
```

#### Failure Scenarios

- Missing manifest field.
- Non-monotonic weather timestamps.
- Wrong timezone or unit.
- Duplicate zone/time row.
- Existing field name with incompatible BigQuery type/mode/partition/cluster configuration.
- Legacy MERGE clears simulation lineage.
- Schema-only probe attempts to touch the shared raw bucket.
- Coordinator preflight returns zero or more than one row, or conditional start does not affect/read back exactly one row.
- Schema migration against existing non-empty tables.

#### Done Criteria

- New schemas are additive and provisioning remains non-destructive.
- Scenario fixture provenance is explicit and all ten zones can resolve each tick.
- Existing test suite remains green.
- User approves the contract before Phase 2.

#### Phase 1 Execution Evidence — 24-07-2026

**Execution boundary:** Phase 1 only. No simulator engine, runtime switch, shared
dataset mutation, deploy, IAM, Cloud Run Job, Scheduler, or Phase 2 code was
created.

Implemented:

- Strict fail-closed settings parsing and allowlists for project, dataset,
  bucket, region, mode, scenario, current snapshot table, model, scenario
  version, generator version, booleans, tick, seed, and lease values.
- Eight simulator tables plus all frozen `NULLABLE` existing-table extensions.
- Existing-table preflight for field name/type/mode, partition field/type,
  clustering fields/order, and dataset location.
- `--schema-only --dataset <explicit-disposable>` provisioning/read-back and
  explicit cleanup; its code path does not call `ensure_bucket()`.
- Explicit MERGE update-field ownership. Legacy/live writes update only fields
  present in every row; deliberate current-snapshot replacement may explicitly
  clear stale simulator lineage.
- Checked `hanoi_heatwave_v1` manifest, deterministic fixture builder, 96-row
  `weather_15m.csv`, and fail-closed runtime loader/validator.
- Disabled-safe simulator environment defaults.

Contract correction discovered during implementation:

- Section 4.6 froze `simulation_control_events` clustering as
  `(scenario_id, simulation_run_id, status, proposal_id)` but its field matrix
  omitted `status`. BigQuery cannot cluster on a missing field, so Phase 1 adds
  `status STRING REQUIRED` with the frozen P0 value `AUTHORIZED`. This is an
  additive new-table correction and does not alter any existing table.

Fixture evidence:

- Exact source canonical SHA-256:
  `13e289c702b3d4213986d237d54eeb3225fbd8b4c493e8b169c16af371c859ac`.
- Checked fixture SHA-256:
  `6e47f3e0a4e7590cbd6e16913ac86fa465f30695c4b7adb80ab6bd8e92400137`.
- 96 ticks from `2026-05-26T00:00:00+07:00` through
  `2026-05-26T23:45:00+07:00`.
- Peak calibrated temperature `41.1°C` at 16:00, source ERA5 `39.5°C`,
  humidity `44%`, derived Heat Index `53.9°C`, maximum transparent adjustment
  `1.6°C`, and zero daily precipitation.

Local proof:

```text
venv/bin/python -m unittest discover -s tests -v
Ran 62 tests in 0.591s — OK

venv/bin/python -m compileall -q app.py heatsafe infra scripts tests
PASS

venv/bin/python -m pip check
No broken requirements found.

git diff --check
PASS

validate-plan-artifact.mjs --strict <selected-plan>
0 failures, 0 warnings (2,039 validator-counted lines)
```

The existing Streamlit suite still emits pre-existing bare-mode, AI-unavailable,
SQLite resource, and dependency deprecation warnings; there were no test
failures.

Disposable BigQuery proof in project `cohort2track2`:

1. `heatsafe_phase1_20260724_0108` created 21 total tables, including all eight
   new simulator tables, with exact partition/clustering read-back and
   `rows_total=0`. Storage was not called. The dataset was then deleted and
   absence verified.
2. `heatsafe_phase1_migration_20260724_0112` began with one legacy
   `weather_observations` row. Provisioning appended
   `simulation_run_id STRING NULLABLE`; post-migration row count remained `1`,
   the legacy `snapshot_id` remained `legacy-proof`, and the new field read
   back as `NULL`. The dataset was then deleted and absence verified.

**Gate result:** implementation and proof boundary are green. The user confirmed
Phase 1 on 24-07-2026, so it is `VERIFIED`. Phase 2 Stage 0 research is
authorized; its implementation remains behind the parameter-review stop gate.

#### Phase 1 Research Findings — 23-07-2026

**Research status:** COMPLETE for contract/source selection; no schema, data, code, or cloud mutation performed.

##### R1. BigQuery migration contract

- Existing non-empty tables accept only new `NULLABLE`/`REPEATED` columns; fields are appended at schema tail. Immediate verification uses `tables.get`, not eventually consistent `INFORMATION_SCHEMA`. Source: [Google Cloud — Modifying table schemas](https://docs.cloud.google.com/bigquery/docs/managing-table-schemas).
- New empty simulator tables may use `REQUIRED` fields. Existing-table extensions below are all `NULLABLE`.
- Preflight compares exact field name/type/mode, time partition field/type, clustering fields/order, and table location. Any collision fails closed; no type coercion, relaxation, repartition, or clustering rewrite is automatic.
- Clustering uses top-level non-repeated supported fields and no more than four columns. Source: [Google Cloud — Create clustered tables](https://docs.cloud.google.com/bigquery/docs/creating-clustered-tables).
- `--schema-only` accepts an explicit disposable dataset, does not instantiate a Storage client, verifies with `tables.get`, and cleans up only that dataset after row-count/schema evidence is retained.

Authoritative exact schemas for previously grouped tables:

| Table | Type / mode | Fields |
|---|---|---|
| `driver_simulation_state` | STRING REQUIRED | `simulation_run_id`, `scenario_id`, `driver_id_hash`, `last_tick_id`, `zone_id`, `status`, `acclimatization_class`, `generator_version` |
|  | STRING NULLABLE | `current_order_id`, `current_intervention_id` |
|  | TIMESTAMP REQUIRED | `event_time`, `updated_at` |
|  | TIMESTAMP NULLABLE | `shift_started_at`, `shift_ends_at` |
|  | FLOAT64 REQUIRED | `latitude`, `longitude`, `distance_km_60m`, `workload_intensity`, `heat_dose_120m`, `route_heat_load` |
|  | INT64 REQUIRED | `online_minutes_24h`, `trips_60m`, `continuous_exposure_minutes`, `rest_minutes_120m`, `hydration_gap_minutes`, `earnings_60m_vnd`, `platform_contribution_60m_vnd` |
|  | BOOL REQUIRED | `is_simulated` |
| `order_events` | STRING REQUIRED | `event_id`, `simulation_run_id`, `tick_id`, `scenario_id`, `order_id`, `event_type`, `status`, `origin_zone_id`, `destination_zone_id`, `zone_id`, `generator_version` |
|  | STRING NULLABLE | `driver_id_hash` |
|  | TIMESTAMP REQUIRED | `event_time`, `requested_at` |
|  | TIMESTAMP NULLABLE | `accepted_at`, `pickup_at`, `dropoff_at`, `cancelled_at` |
|  | FLOAT64 NULLABLE | `distance_km`, `estimated_duration_minutes`, `actual_duration_minutes`, `wait_minutes` |
|  | INT64 NULLABLE | `fare_vnd`, `driver_pay_vnd`, `platform_contribution_vnd` |
|  | BOOL REQUIRED | `is_simulated` |
| `driver_intervention_events` | STRING REQUIRED | `event_id`, `simulation_run_id`, `tick_id`, `scenario_id`, `intervention_id`, `proposal_id`, `driver_id_hash`, `zone_id`, `event_type`, `generator_version` |
|  | STRING NULLABLE | `coolstop_name` |
|  | TIMESTAMP REQUIRED | `event_time` |
|  | INT64 NULLABLE | `pause_start_delay_minutes`, `planned_duration_minutes`, `completed_rest_minutes`, `earnings_delta_vnd` |
|  | FLOAT64 NULLABLE | `baseline_risk_probability`, `action_risk_probability` |
|  | BOOL REQUIRED | `is_simulated` |

Exact existing-table extensions (every field `NULLABLE`):

| Table(s) | Fields and BigQuery types | Legacy matched-write policy |
|---|---|---|
| `weather_observations` | STRING: `simulation_run_id`, `tick_id`, `derivation_version`, `generator_version`; TIMESTAMP: `source_observed_at`, `source_next_observed_at`; FLOAT64: `source_interpolation_fraction`, `source_temperature_c`, `temperature_adjustment_c`, `station_peak_anchor_c`, `apparent_temperature_c`, `wind_speed_mps`, `wind_gust_mps`, `precipitation_mm`, `cloud_cover_pct`, `shortwave_radiation_wm2`, `utci_c` | Legacy/live writer updates only legacy-owned weather values; it does not clear simulator lineage on unrelated keys |
| `zone_operations` | STRING: `simulation_run_id`, `tick_id`, `generator_version`; INT64: `online_drivers`, `idle_drivers`, `to_pickup_drivers`, `on_trip_drivers`, `to_coolstop_drivers`, `paused_drivers`, `exposed_2_to_4h`, `requests_15m`, `matched_15m`, `completed_15m`, `cancelled_15m`, `unfulfilled_15m`; FLOAT64: `median_wait_minutes`, `p90_wait_minutes`, `fulfillment_rate` | Dedicated simulator projection; legacy writer updates only its original columns |
| `demand_history` | STRING: `simulation_run_id`, `tick_id`, `generator_version` | Replay key includes run/tick/time; legacy `(scenario, zone, interval)` rows cannot overwrite replay rows |
| `driver_state_history` | STRING: `simulation_run_id`, `tick_id`, `driver_status`, `acclimatization_class`, `current_order_id`, `current_intervention_id`, `generator_version`; FLOAT64: `heat_dose_120m`; INT64: `earnings_60m_vnd`, `platform_contribution_60m_vnd` | Append/idempotent event ID only; never full-row legacy update |
| `driver_current_features` | STRING: `simulation_run_id`, `tick_id`, `driver_status`, `acclimatization_class`, `generator_version`; FLOAT64: `heat_dose_120m` | Explicit full current replacement by scenario/run; old lineage is intentionally removed only in this current-state operation |
| `driver_risk_predictions`, `zone_demand_forecasts`, `zone_snapshots_current` | STRING: `simulation_run_id`, `tick_id`, `generator_version`; snapshot also receives the operation fields listed above with matching types | Prediction/forecast append-MERGE by deterministic tick key; current snapshot replacement explicitly clears stale lineage for a deliberate static/live restore |
| `intervention_proposals`, `intervention_events` | STRING: `scenario_id`, `source_snapshot_id`, `simulation_run_id`, `source_tick_id`; TIMESTAMP: `expires_at` | Audit-only lineage; no direct simulator consumption |

##### R2. Exact historical weather source

Selected source:

- Provider/API: [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api), explicitly `models=era5`.
- Underlying dataset: ERA5 reanalysis, hourly, approximately 0.25° grid. ERA5 was chosen over ERA5-Land because the live response test returned complete wind, gust, cloud, precipitation, and solar radiation fields; ERA5-Land returned `null` for several of them. Open-Meteo documents the model/variable coverage difference.
- Scenario day: **2026-05-26 local Hanoi time**, selected because Láng station measured `41.1°C`. Reports citing the National Center for Hydro-Meteorological Forecasting identify this as Hanoi's highest reading that day and joint-highest nationally with Đô Lương—not the sole national maximum and not Láng's all-time record. References: [NCHMF forecast for 26 May](https://nchmf.gov.vn/kttv/vi-VN/1/dieu-tiet-ho-chua-2079-15.html), [VnExpress measured-temperature report](https://vnexpress.net/ha-noi-nghe-an-nong-41-do-c-5078532.html).
- Station anchor: Láng/Hanoi WMO `48820`, coordinate `21.02, 105.80`.
- Requested ERA5 coordinate: `21.02, 105.80`.
- Resolved ERA5 grid/elevation: `21.0, 105.75`, `16 m`.
- Timezone: `Asia/Ho_Chi_Minh`, UTC+07:00.
- Retrieval time: `2026-07-23T17:43:09Z`.
- License: CC BY 4.0; manifest/UI attribution is `Weather data by Open-Meteo.com`, links the license, credits ERA5/Copernicus, and says the data were temporally interpolated. Source: [Open-Meteo license](https://open-meteo.com/en/license).

Exact acquisition query (one-hour buffer day on each side is required for boundary interpolation):

```text
https://archive-api.open-meteo.com/v1/archive?latitude=21.02&longitude=105.80&start_date=2026-05-25&end_date=2026-05-27&hourly=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,cloud_cover,wind_speed_10m,wind_gusts_10m,shortwave_radiation&wind_speed_unit=ms&timezone=Asia%2FHo_Chi_Minh&models=era5
```

Canonical source-payload SHA-256 on retrieval (JSON key-sorted with volatile `generationtime_ms` removed):

```text
13e289c702b3d4213986d237d54eeb3225fbd8b4c493e8b169c16af371c859ac
```

Observed target-day envelope from the captured response:

| Variable | 2026-05-26 ERA5 range |
|---|---:|
| Temperature | `29.8..39.5 °C`; peak at `16:00` |
| Relative humidity | `44..74 %` |
| Apparent temperature | `35.1..45.4 °C` |
| Precipitation | `0.0 mm/day` |
| Cloud cover | `0..6 %` |
| Wind speed | `1.96..3.53 m/s` |
| Wind gust | maximum `7.60 m/s` |
| Shortwave radiation | maximum `968 W/m²` |

ERA5 underestimates the point-station maximum because it represents a coarse grid cell: `39.5°C` versus Láng's measured `41.1°C`. P0 therefore uses the station value as a single daily-maximum calibration anchor and ERA5 for the 24-hour shape plus humidity/wind/cloud/radiation. This produces a credible hot-night, extreme-afternoon, clear/dry, low-wind day without pretending that ERA5 is the station observation.

##### R3. Fixture and manifest contract

`manifest.json` required keys:

```text
schema_version, scenario_id, scenario_version, display_name, description,
timezone, simulation_start_local, tick_minutes, expected_ticks,
source.provider, source.dataset, source.api_url, source.requested_coordinate,
source.resolved_grid, source.retrieved_at, source.canonical_sha256,
source.license, source.attribution, source.modifications,
calibration_anchor.station_name, calibration_anchor.wmo_id,
calibration_anchor.coordinate, calibration_anchor.observed_daily_max_c,
calibration_anchor.observation_date, calibration_anchor.source_urls,
calibration_anchor.era5_daily_min_c, calibration_anchor.era5_daily_max_c,
calibration_anchor.method, calibration_anchor.limitations,
derivation.version, derivation.method_by_field, derivation.output_columns,
validation.expected_first_time, validation.expected_last_time,
validation.expected_rows, validation.ranges,
zone_weather_offsets, operational_priors, disclaimer
```

Frozen values:

```text
scenario_id = heatwave
scenario_version = hanoi_heatwave_v1
simulation_start_local = 2026-05-26T00:00:00+07:00
tick_minutes = 15
expected_ticks = 96
expected_first_time = 2026-05-26T00:00:00+07:00
expected_last_time = 2026-05-26T23:45:00+07:00
derivation.version = lang-max-anchor-era5-linear-15m-v2
```

`weather_15m.csv` exact columns:

```text
simulation_offset_minutes, local_time, source_observed_at,
source_next_observed_at, source_interpolation_fraction,
source_temperature_c, temperature_adjustment_c, temperature_c,
station_peak_anchor_c, relative_humidity_percent, apparent_temperature_c,
precipitation_mm, cloud_cover_pct, wind_speed_mps, wind_gust_mps,
shortwave_radiation_wm2, source_grid_latitude, source_grid_longitude,
derivation_version
```

Derivation:

- Emit exactly 96 rows at 15-minute intervals.
- First calibrate each ERA5 hourly temperature with the transparent affine range mapping:

  ```text
  temperature_c =
    29.8 + (source_temperature_c - 29.8) * (41.1 - 29.8) / (39.5 - 29.8)
  ```

  This preserves the ERA5 daily minimum and timing while making the `16:00` maximum equal the observed Láng anchor. The factor is approximately `1.16495`; `temperature_adjustment_c` is explicit and bounded `0..1.6°C`.
- For calibrated temperature and other instant/continuous variables—humidity, source apparent temperature, cloud, wind, gust, radiation—use bounded linear interpolation between surrounding hourly rows; fractions are exactly `0`, `0.25`, `0.5`, `0.75`.
- For hourly accumulated precipitation, split the hour total across its four intervals and assert daily/three-day mass conservation. The selected target day remains zero throughout.
- Clamp only floating-point noise to physical domains (`humidity/cloud 0..100`, non-negative precipitation/wind/radiation); fail instead of silently clipping a materially invalid source value.
- Compute app `heat_index_c` with the existing `calculate_heat_index()` from calibrated temperature and ERA5 humidity. At the anchored peak (`41.1°C`, `44% RH`) the current formula yields `53.9°C`; keep this as a derived screening value, not a measured station field. Preserve Open-Meteo apparent temperature as uncalibrated source provenance; do not substitute it for Heat Index or label it UTCI.
- All ten P0 zones use the same city reference weather curve and **zero zone offsets**. This is more defensible than inventing unsupported 3–5°C district differences within one coarse ERA5 cell. Operational route heat load may vary by zone; microclimate offsets remain future calibrated work.
- Because the curve combines one station maximum, ERA5 reanalysis, affine calibration, and interpolation, projected rows use `weather_is_simulated=TRUE`, `operations_is_simulated=TRUE`, source `Láng 41.1°C anchor + Open-Meteo ERA5 historical replay`, and explicit derivation fields. Only `station_peak_anchor_c=41.1` is a reported station observation; the 96-point curve is source-grounded derived data.

Validation domains:

```text
source_temperature_c: 0..50
temperature_adjustment_c: 0..2
temperature_c: 0..50 and daily max exactly 41.1 within 0.05
station_peak_anchor_c: exactly 41.1
relative_humidity_percent: 0..100
apparent_temperature_c: 0..60
precipitation_mm per 15m: 0..100
cloud_cover_pct: 0..100
wind_speed_mps: 0..30
wind_gust_mps: 0..50 and >= wind_speed_mps
shortwave_radiation_wm2: 0..1400
```

The fixture test additionally locks the canonical source checksum, exact 96 timestamps, ERA5 envelope, calibrated maximum/time/tolerance, `temperature_c >= source_temperature_c`, maximum adjustment `1.6°C`, monotonic time, interpolation fractions, no null/NaN/infinite value, daily precipitation conservation, and image inclusion under `Dockerfile`/`.dockerignore`.

##### R4. Identifier policy

This app intentionally uses a stricter subset than the maximum platform grammar:

| Input | Frozen validation |
|---|---|
| GCP project ID | `^[a-z][a-z0-9-]{4,28}[a-z0-9]$` |
| BigQuery dataset ID | `^[a-z][a-z0-9_]{0,127}$` |
| GCS bucket | `^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$`, plus reject IP-like names, `..`, `.-`, and `-.` |
| Region | allowlist: `asia-southeast1` for P0 |
| Scenario | allowlist: `heatwave`, `live` |
| Mode | allowlist: `auto`, `cloud`, `snapshot` |
| Current snapshot table | allowlist: `zone_snapshots_current` |
| Every other table/model name | internal constant allowlist derived from `table_schemas()` and the fixed model names; never accept a free-form external identifier |
| Scenario/generator version | `^[a-z][a-z0-9._-]{0,63}$`, and scenario version must resolve under the checked-in scenario catalog |
| Gemini model | allowlist: current configured production model only; changing it is a reviewed config change |

Tests include empty/overlong values, leading/trailing separators, dots/backticks/whitespace, SQL fragments, Unicode confusables, path traversal, unknown allowlist values, and a positive case for every current default. Validation occurs in `Settings.from_env()`/scenario loading before any SQL string or GCP resource path is built.

##### R5. Phase 1 decision

Phase 1 uses **Láng's measured 41.1°C daily maximum as a calibration anchor + ERA5 via Open-Meteo for the full-day meteorological shape + transparent 15-minute interpolation + zero district weather offsets**. The fixture explicitly separates reported station evidence, ERA5 source values, calibration delta, and derived values. This is reproducible, license-compatible, and materially more honest than either ignoring the station extreme or pretending a synthetic 96-point curve was directly observed. Phase 1 was implemented, proven, and user-confirmed on 24-07-2026.

### Phase 2 — Local Deterministic Engine

**Status:** ✅ VERIFIED — automated/manual evidence green and user-confirmed 24-07-2026
**Dependencies:** Phase 1 ✅ VERIFIED
**Estimate:** 1–1.5 days

#### Stage 0: Pre-Phase Research

1. Re-read current demand heuristic, `safepause.py`, risk features, and model feature ranges.
2. Lock scenario parameters: initial fleet distribution, shifts, transition probabilities, trip duration/distance bounds, recovery policy, and weather multipliers.
3. Identify every parameter that is an engineering prior rather than sourced evidence.
4. Reconcile the incomplete static aggregates into a coherent initial fleet: derive the population from `active_drivers`, preserve cumulative exposure cohorts, and document how unavailable/online drivers are added.
5. Freeze the current scoring envelope and define raw-state-to-scoring projection with explicit clipping/OOD flags and acceptance thresholds.
6. Present the parameter table and stop for approval.

#### Phase 2 Stage 0 Research Findings — 24-07-2026

**Research status:** COMPLETE. These findings freeze the proposed P0 simulation contract but do not authorize implementation.

##### R1. Evidence boundary

Phase 2 deliberately separates three evidence classes:

| Class | Meaning | P0 use |
|---|---|---|
| Repository contract | Directly checked in current source, snapshot, model generator, or accepted plan | May be treated as authoritative for compatibility |
| External structural evidence | Public evidence supports the shape of a process, but the underlying records are not imported/calibrated here | May justify lifecycle/model form, never a Hanoi numerical claim |
| Engineering prior | A transparent, bounded demo assumption selected for coherent behavior | Must be named in code/config and remain replaceable |

Checked repository facts:

- `data/demo_snapshot.json` is a single 13:00 aggregate anchor with 3,115 active drivers and 2,741 forecast requests per 30 minutes. It contains no driver histories, shift records, order events, cancellations, travel times, or empirical distributions.
- The existing intraday demand heuristic has morning, lunch, and evening peaks and deterministic low-amplitude waves. Phase 2 retains that shape instead of inventing a second demand curve.
- `safepause.py` currently assumes one trip per active driver per 30 minutes for its aggregate optimizer, evaluates 10–30 minute pauses, and uses 5-minute internal steps. Those are compatibility inputs, not observed trip telemetry.
- `infra/ml_pipeline.py` creates synthetic model training examples. Therefore its feature ranges are compatibility envelopes, not physiological or operational truth.

External evidence supports only the following structural choices:

- Real last-mile data commonly models accept/finish events and courier/task lifecycles; LaDe is a relevant public event-oriented reference, but is not Hanoi calibration data: [LaDe dataset](https://github.com/wenhaomin/LaDe).
- A Hanoi study based on 50,767 ride-hailing bookings reports spatial/time and congestion heterogeneity. It supports zone/time-dependent demand and travel, not the numerical multipliers below: [Journal of Transport Geography paper](https://doi.org/10.1016/j.jtrangeo.2025.104422).
- Service-demand counts are often over-dispersed relative to Poisson; a Gamma-Poisson/negative-binomial form represents that without claiming a fitted Hanoi dispersion: [over-dispersed service-demand method](https://pmc.ncbi.nlm.nih.gov/articles/PMC6413888/).
- NIOSH recommends access to cool recovery areas, more frequent rest, hydration, and acclimatization practices. It supports gradual exposure/recovery state, but the simulator is not a medical model and its numerical recovery rates remain engineering priors: [NIOSH heat-stress recommendations](https://www.cdc.gov/niosh/heat-stress/recommendations/).

No public dataset found in Stage 0 jointly provides Hanoi motorcycle ride-hailing demand, driver status, heat exposure, SafePause controls, and matching outcomes at the required granularity. P0 therefore uses the checked-in aggregate as its calibration anchor and labels all unsourced distributions below as engineering priors. Importing/fitting Grab-Posisi, LaDe, or proprietary trip telemetry remains post-P0 work.

##### R2. Static aggregate reconciliation

The current display fields are not a partition:

```text
active_drivers        = 3,115
fresh_drivers (raw)   = 1,000
exposed_2h            =   669
exposed_4h            =   198  # cumulative subset of exposed_2h
unclassified active   = 1,446  # active - raw fresh - exposed_2h
```

The simulation contract is:

- `active = IDLE + TO_PICKUP + ON_TRIP`.
- `online = active + TO_COOLSTOP + PAUSED`.
- `exposed_4h` is a cumulative subset of `exposed_2h`.
- `fresh = active - exposed_2h`; the raw snapshot's `fresh_drivers` is retained only as a legacy source value and never used as a partition count.
- At the 13:00 calibration anchor this yields `fresh=2,446`, `exposed_2h=669`, and `exposed_4h=198`.
- At an arbitrary replay start, the active population is selected from the deterministic shift schedule. The per-zone `exposed_2h/active` and `exposed_4h/active` anchor ratios initialize cumulative cohorts, rounded with largest-remainder allocation so counts remain exact and nested. Subsequent ticks derive cohorts from each driver's continuous-exposure state.
- Before a trusted control is applied, no driver starts in `TO_COOLSTOP` or `PAUSED`. These statuses are created only through an intervention lifecycle; they are not invented to make `online` larger.

##### R3. Frozen fleet and shift parameters

All numerical values in this subsection are **engineering priors** unless marked “snapshot anchor”.

| Parameter | Frozen P0 value | Reason / acceptance |
|---|---|---|
| Per-zone roster | `2.00 × active_drivers` at the 13:00 anchor; exactly 6,230 citywide | Supports an average 8.66 scheduled hours per rostered driver under the target curve without treating the displayed active count as the whole registered fleet |
| Active-supply target | Piecewise-linear multipliers over each zone's 13:00 anchor: `00=.25, 04=.20, 06=.50, 08=1.10, 10=.90, 13=1.00, 15=.85, 18=1.20, 20=.95, 22=.50, 24=.25` | Morning/evening peaks with an exact 13:00 compatibility anchor |
| Shift allocator | Materialize all 96 15-minute availability slots at run start. Retain the prior active set first, then enter/exit drivers by per-entity hash and longest-rest/longest-service priority until the exact zone target is met. Prefer minimum 60 minutes online and 30 minutes offline; the exact target wins when these preferences conflict. | Produces contiguous and occasional split shifts, exact target counts, and no independent per-minute online coin flips |
| Initial active statuses | `IDLE=.60`, `TO_PICKUP=.20`, `ON_TRIP=.20`, exact by largest remainder | Allows an in-flight start without invalid or duplicated orders |
| Initial exposure cohorts | Snapshot-anchor per-zone ratios; `exposed_4h ⊆ exposed_2h`; exposure minutes are deterministically spread within `[0,119]`, `[120,239]`, and `[240,360]` | Preserves cumulative cohorts while making individual state explicit |
| Acclimatization tag | `LOW=.20`, `MEDIUM=.60`, `HIGH=.20` | Scenario sensitivity tag only; never displayed as a diagnosis |
| Start location | Deterministic jitter within 1.5 km of the zone centroid, clipped to the scenario bounding box | No false road-level precision |
| Shift boundary | A driver finishes an accepted trip before going offline; a SafePause still active at shift end closes as an explicit partial pause and recovery continues offline; no new work is accepted after shift end | Prevents impossible order abandonment while preserving truthful partial-rest evidence |

Schedule assignment is a deterministic constrained allocation by zone and driver hash. The target multiplier table is authoritative. The allocator materializes the complete schedule before the first state transition and a larger than 3% deviation is an invariant failure, not silent random drift.

Execution-entry correction: the Stage 0 nominal template percentages could not
simultaneously reproduce the frozen 06:00, 08:00, 13:00, and 18:00 targets
within 3%. They were replaced before source implementation by the deterministic
slot allocator above. The roster, target curve, minimum-duration preferences,
and no-minute-coin-flip intent are unchanged.

##### R4. Frozen demand and order parameters

Expected zone demand per 15 minutes is:

```text
lambda15(z,t) =
  forecast_requests_30m(z) / 2
  × intraday_factor(t) / intraday_factor(13:00)
  × weather_factor(z,t) / weather_factor(z,13:00)
  × city_shock(t)
  × zone_shock(z,t)
```

`forecast_requests_30m/2` is the snapshot anchor. The remaining numerical settings are engineering priors:

| Parameter | Frozen P0 value |
|---|---|
| Intraday curve | Reuse `_intraday_demand_factor`; no second demand heuristic |
| City shock | AR(1), `phi=.85`, innovation SD `.04`, clamp `[.85,1.20]` |
| Zone shock | Independent per-zone AR(1), `phi=.65`, innovation SD `.03`, clamp `[.90,1.10]` |
| Heat demand factor | `1 + .06 × clamp((heat_index_c - 35) / 18, 0, 1)` |
| Rain demand factor | `1 + .15 × clamp(precipitation_mm / 5, 0, 1)`; zero on the selected fixture |
| Cloud factor | No direct demand multiplier in P0 |
| Count distribution | Gamma-Poisson/negative-binomial with dispersion `k=40`; generate at one-minute resolution from `lambda15/15` |
| Request TTL | 8 minutes; then terminal `UNFULFILLED` |
| Matching | Same-zone `IDLE` drivers first; stable distance then driver-ID tie-break |
| Acceptance | Base `.90`, minus `.10` when workload intensity exceeds `1.8`, clamp `[.70,.95]` |
| Pickup time | Triangular `2/5/10` minutes |
| Trip distance | Lognormal median `4.0 km`, log-sigma `.55`, clamp `[.8,18] km` |
| Effective speed | `26 km/h` overnight, `22 km/h` off-peak, `16 km/h` commute peaks; linear shoulder interpolation |
| Trip duration | `ceil(60 × distance/speed)`, clamp `[6,45]` minutes |
| Destination | 65% same zone; otherwise inverse-centroid-distance weighted |
| Cancellation | 4% before match over the TTL and 2% after match/before pickup; no synthetic mid-trip cancellation |
| Economics | On completion only: snapshot zone-average driver earnings and platform contribution multiplied by a deterministic `[.92,1.08]` factor; fare is their non-negative sum |

The small-/large-mean request sampler is part of the deterministic contract: Gamma sampling uses a bounded Marsaglia-Tsang implementation; Poisson uses inversion for mean `<30` and bounded transformed rejection otherwise. It uses the request stream keyed by scenario/seed/tick/zone, permits at most 128 rejection attempts, and raises a simulation error rather than falling back to a non-deterministic sampler.

The lifecycle conservation rules are:

```text
all-time requested = open_unmatched + matched + pre-match cancelled + unfulfilled
tick open_start + tick requested =
  tick matched + tick pre-match cancelled + tick unfulfilled + tick open_end
matched = awaiting_pickup + on_trip + completed + post-match cancelled
```

Every terminal order has exactly one terminal reason and every accepted non-terminal order has exactly one driver.

##### R5. Frozen driver, heat, and intervention transitions

Valid transitions are:

```text
OFFLINE     -> IDLE
IDLE        -> TO_PICKUP | TO_COOLSTOP | OFFLINE
TO_PICKUP   -> ON_TRIP | IDLE
ON_TRIP     -> IDLE
TO_COOLSTOP -> PAUSED | IDLE
PAUSED      -> IDLE | OFFLINE
```

Additional engineering-prior rules:

- A driver can own at most one non-terminal order and one non-terminal intervention; order work and pause work never overlap.
- Exposure minutes increment in `IDLE`, `TO_PICKUP`, `ON_TRIP`, and `TO_COOLSTOP`; they decay by 3 minutes per minute in `PAUSED` and by 1 minute per minute in `OFFLINE`, floored at zero. A pause never instantaneously erases exposure.
- Hydration gap increments while exposed. Five consecutive `PAUSED` minutes reset the gap to zero; `OFFLINE` decrements it by one minute per minute.
- `rest_minutes_120m` counts actual `PAUSED` minutes in a rolling 120-minute window, not assigned pause duration.
- Heat dose is raw operational state with 120-minute exponential half-life. Per-minute input is `max(0, heat_index_c-27) × route_heat_load × acclimatization_factor / 60`; pause/offline input is zero. Acclimatization factors are `LOW=1.15`, `MEDIUM=1.00`, `HIGH=.90`.
- Route-load status multipliers are `IDLE=.90`, `TO_PICKUP=1.10`, `ON_TRIP=1.25`, `TO_COOLSTOP=1.00`, `PAUSED=.20`, `OFFLINE=0`, multiplied by a stable per-driver/zone base in `[.8,1.4]`.
- Workload intensity is derived from rolling trips, distance, and current work status and retained raw in `[0,3.5]`.
- A trusted SafePause control may request 15 or 30 minutes with a maximum start delay of 45 minutes. `IDLE` drivers travel to the CoolStop immediately; busy drivers finish their current order first. CoolStop travel is 2–10 minutes. Shift end closes an unfinished tracked pause as `SHIFT_ENDED_PARTIAL_PAUSE`; gradual offline recovery continues and the completed rest is never inflated to the planned duration.
- Without a trusted control, the engine produces no intervention. The simulator never diagnoses a driver or claims that a particular pause is medically sufficient.

The one-minute update order frozen in the Implementation list is normative. A transition generated later in that order cannot be retroactively observed by an earlier step in the same minute.

##### R6. Raw-to-model scoring projection and OOD gate

Only `IDLE`, `TO_PICKUP`, and `ON_TRIP` drivers are scoring-eligible. Operational raw values are never overwritten. A separate projection supplies these compatibility bounds derived exactly from the current synthetic model generator:

| Numeric feature | Model projection interval |
|---|---:|
| `heat_index_c` | `[33.05, 50.55]` |
| `humidity_percent` | `[46, 68]` |
| `continuous_exposure_minutes` | `[30, 360]` |
| `trips_60m` | `[1, 5]` |
| `distance_km_60m` | `[3.0, 20.9]` |
| `rest_minutes_120m` | `[0, 45]` |
| `hydration_gap_minutes` | `[15, 180]` |
| `route_heat_load` | `[.60, 3.09]` |
| `workload_intensity` | `[.50, 2.69]` |

Projection output contains `raw_features`, `model_features`, sorted `clipped_fields`, `ood_count`, and `ood_reasons`. Non-finite values and negative duration/count/distance values are hard invariant failures, not clips.

The 41.1°C Láng fixture reaches a derived heat index above the current model's synthetic training envelope. This is expected and must remain visible:

- Weather-field clips are counted and reported separately from behavior-field clips.
- Per tick, each non-weather feature may clip at most 10% of eligible drivers.
- Per tick, all clipped numeric feature cells combined may not exceed 25%.
- Across a full-day replay, combined clipping may not exceed 20%.
- Exceeding a threshold marks the tick/run `MODEL_INPUT_OOD`. The pure Phase 2 runner may complete so evidence can be inspected, but Phase 4 must fail closed to monitoring-only and must not emit trusted intervention recommendations.

These thresholds are operational compatibility gates, not evidence that a clipped prediction is medically valid.

##### R7. Phase 2 acceptance envelope

Implementation must prove all existing Phase 2 tests plus:

- At 13:00, every zone's target active count is within 2% of the snapshot anchor; at every frozen supply breakpoint it is within 3% of the target curve.
- Same seed/input produces the same canonical per-tick and final checksums across process/hash-order changes.
- A different seed changes request/order details while full-day city request total remains within 10% and each hourly city mean remains within 15% of the expected curve.
- Cohort nesting, fleet/status counts, order conservation, and exclusive order/intervention ownership hold on every minute, not only at 15-minute output ticks.
- Raw feature values, projection values, clipped fields, and OOD rates are included in the manual evidence summary.
- The hourly summary prints active/online/offline supply, requests/matches/completions/cancellations/unfulfilled, exposure cohorts, interventions, raw feature extrema, and clipping rates.

**Stage 0 stop gate:** satisfied by the user's explicit
`goal: COMPLETE PHASE 2 FULLY` authorization on 24-07-2026.

#### Implementation

1. Implement immutable domain models and valid transition enums.
2. Implement deterministic per-entity random streams and canonical checksums using sorted entities/events, UTC datetime encoding, fixed float quantization, and no wall-clock fields.
3. Implement start-of-run driver population generation.
4. Implement city/zone demand mean, correlated shock, and over-dispersed request counts with a specified bounded small-/large-lambda sampler; never use a shared mutable RNG.
5. Implement order request/match/pickup/dropoff/cancel/unfulfilled lifecycle.
6. Implement driver shift, location, status, workload, earnings, exposure, rest, hydration, and heat-dose updates using one frozen per-minute order: finish due transitions → apply trusted control → shift/offline transitions → generate requests → match → advance travel/trips → update heat/rest/hydration/economics → validate.
7. Implement intervention assignment/pause/recovery transitions.
8. Implement zone projection and invariant validator.
9. Implement an in-memory two-tick and full-day runner for tests.

#### Test Procedure

Test files:

```text
tests/test_simulation_randomness.py
tests/test_simulation_demand.py
tests/test_simulation_transitions.py
tests/test_simulation_invariants.py
```

Command:

```bash
venv/bin/python -m unittest \
  tests.test_simulation_randomness \
  tests.test_simulation_demand \
  tests.test_simulation_transitions \
  tests.test_simulation_invariants -v
```

Required cases:

- Same seed/input returns byte-stable canonical output.
- Reordered drivers do not change their stochastic result.
- Different seeds change bounded details, not schema or invariants.
- All valid and invalid driver/order transitions.
- Overnight, meal/commute peaks, rain shock, zero demand, oversupply, and undersupply.
- Pause assignment, travel to CoolStop, partial pause, completion, and cancellation.
- Exposure bucket boundary values at 119, 120, 239, and 240 minutes.
- Cross-process determinism under different `PYTHONHASHSEED` and input iteration order.
- `TO_COOLSTOP` remains online but unavailable; intervention lifecycle is distinct from driver status.
- Raw scoring features retain operational truth while the model projection records clipping/OOD rates.

#### Manual Test

Run two local in-memory replays with the same seed and compare final checksum; run a third with a different seed and compare hourly aggregate shapes.

#### Done Criteria

- Pure engine has no BigQuery or wall-clock dependency.
- Determinism and invariant tests are green.
- Full-day in-memory replay completes.
- User accepts the printed hourly demand/supply/exposure summary.

#### Phase 2 Execution Evidence — 24-07-2026

**Execution boundary:** pure local Python engine only. No BigQuery mutation,
Cloud Run Job, Scheduler, IAM, deployment, persistence switch, or public UI
behavior was changed.

Implemented:

- Frozen immutable driver, order, intervention, weather, scoring, zone, state,
  and tick models plus explicit driver/order transition matrices.
- SHA-256 per-entity random streams, bounded Gamma/Poisson sampling,
  cross-process canonical checksums, and sorted-entity determinism.
- Exact 6,230-driver synthetic roster with pre-materialized 96-slot schedules,
  nested exposure cohorts, stable locations/acclimatization, and valid in-flight
  initial orders.
- Existing intraday demand shape plus correlated bounded city/zone shocks,
  weather multipliers, negative-binomial request counts, matching, pickup,
  trip, cancel, unfulfilled, destination, and economics lifecycles.
- One-minute driver/order/intervention transitions within 15-minute public
  ticks, including gradual exposure/recovery, rolling work/rest/economics,
  SafePause travel/pause/completion, duplicate-control idempotency, and
  maximum-start-delay cancellation.
- Queue-aware request conservation:
  `open_start + requested = matched + pre-match_cancelled + unfulfilled + open_end`.
- Raw operational scoring features and a separate clipped model projection with
  per-field reasons/rates and `MODEL_INPUT_OOD`.
- Zone projections, cross-entity invariants, two-tick runner, full-day runner,
  and hourly evidence summary.
- Full structural, cohort, ownership, numeric, and request-flow validation runs
  after every one-minute transition and again at each 15-minute boundary.

Contract correction:

- The nominal Stage 0 shift percentages could not mathematically reproduce all
  frozen supply breakpoints within 3%. Before source implementation they were
  replaced with a deterministic sticky slot allocator. The 2× roster, supply
  curve, minimum-duration preferences, and no per-minute online coin flips are
  unchanged. Full-scale evidence matches every tested breakpoint exactly.
- The tick-boundary request equation now includes starting and ending unmatched
  queue depth. This avoids false reconciliation when a late-tick request is
  matched in the next tick.

Automated evidence:

```text
venv/bin/python -m unittest \
  tests.test_simulation_randomness \
  tests.test_simulation_demand \
  tests.test_simulation_transitions \
  tests.test_simulation_invariants -v

Ran 32 tests — OK

venv/bin/python -m unittest discover -s tests -v

Ran 94 tests — OK
```

The final 94-test count is recorded after all Phase 2 minute-invariant,
zero/over/undersupply, exhaustive transition-matrix, partial-pause,
CoolStop-semantics, summary, flow-conservation, and failure-path cases were
added; pre-existing Streamlit/AI/SQLite/deprecation warnings remain non-failing.

Execution commits:

- `b7606d1 feat(simulation): implement deterministic phase 2 engine`
- `e511b45 test(simulation): complete phase 2 evidence gates`

Full-scale manual evidence, 6,230 drivers and 96 ticks:

| Evidence | Result |
|---|---|
| Seed 42 replay A final checksum | `3b9a2391b4ef01d76d5d3c617dbf67f3135d5a0b05669855a74e3fba10f01f71` |
| Seed 42 replay B final checksum | exact match, including all 24 hourly request aggregates |
| Seed 43 final checksum | `d6f6abff7124d000b7298f0541c7033c6ce30343c5e8ae85fae26ae602bdb2fe` |
| Seed 42/43 full-day requests | `118,250` / `118,270`; delta `0.017%` |
| Maximum seed-to-seed hourly delta | `7.434%`, below the frozen `15%` bound |
| Supply breakpoints | exact at 00:00, 04:00, 06:00, 08:00, 10:00, 13:00, 15:00, 18:00, 20:00, 22:00, and 23:45 |
| Request-flow balance | zero for every checked zone/tick |
| Tick checksums | 96 distinct checksums |
| One full-scale local replay runtime | `94.21s` with invariant validation after every minute; Phase 5 owns the separate per-tick Cloud Run SLO |

Model compatibility finding:

- Seed 42 full-day clipped-cell rate is `27.4316%`; 94 of 96 ticks are marked
  `MODEL_INPUT_OOD`. Seed 43 is `27.2809%`, also 94 ticks.
- This is an expected, correctly surfaced compatibility failure against the
  current synthetic training envelope, especially for extreme weather,
  continuous exposure, and hydration/workload state. Raw state was not altered
  to make the model appear valid.
- Per the frozen gate, Phase 4 must remain monitoring-only for OOD ticks unless
  its Stage 0 review explicitly expands/retrains and revalidates the model
  envelope. This does not invalidate the pure engine or its deterministic state
  transitions.

User confirmation was received on 24-07-2026: `confirmed Phase 2 OK`.
Phase 2 is `✅ VERIFIED`. The next valid transition is inter-phase
`ENTER UPDATE PROCESS MODE` before Phase 3.

### Phase 3 — BigQuery Persistence and Snapshot Projection

**Status:** ✅ VERIFIED — automated/disposable-cloud proof green and user-confirmed 24-07-2026
**Dependencies:** Phase 2 ✅ VERIFIED
**Estimate:** 1 day

#### Stage 0: Pre-Phase Research

1. Inspect current production/test row counts and partition sizes.
2. Confirm transaction/staging strategy, conflict error codes, partition-pruned predicates, and per-query/per-tick/full-replay `maximum_bytes_billed`.
3. Define run-tagged current-table backup and targeted transactional restore; keep `--seed-demo` only as a clearly labelled full reseed/emergency path, not normal rollback.
4. Present persistence SQL and rollback path; stop for approval.

#### Stage 0 Decision Record — 24-07-2026

Research and innovation are recorded in
[`phase3_persistence_RESEARCH_24-07-26.md`](phase3_persistence_RESEARCH_24-07-26.md).
The user explicitly authorized the full Phase 3 RIPER sequence on 24-07-2026.
The implementation boundary is local/fake-client only: no shared demo dataset,
IAM, Scheduler, Cloud Run, or deployment mutation. The existing disposable
BigQuery feasibility probe remains an `INCONCLUSIVE` Hybrid gate and must not
be represented as live proof.

The frozen design uses a dedicated repository, precreated immutable tick IDs,
conditional lease acquisition plus in-transaction fencing revalidation,
expiring staging, transactionally coherent history/current projection with
`SNAPSHOT_READY` last, and a separate scoring-finalization cursor. The CLI is
an adapter only and contains no SQL or public control authority.

#### Implementation

1. Add `heatsafe.simulation.cli` commands:

   ```text
   validate-scenario
   start
   tick
   tick --tick-id <deterministic-tick-id>
   status
   pause
   resume
   ```

2. Pre-provision one coordinator row per scenario and all 96 tick rows; future tick `input_checksum` remains `NULL` until acquisition. Abort start unless coordinator `COUNT(*)=1`, conditional mutation affects exactly one row, and read-back returns the expected generation/run.
3. Implement conditional tick lease acquisition with a unique owner/fencing token, winner read-back, TTL greater than timeout plus margin, and bounded conflict retry.
4. Load deterministic rows into an expiring staging dataset before publication.
5. In one BigQuery transaction, revalidate the lease owner and expiry, merge tick-visible order/intervention/history rows, update current driver state, demand history, weather/operations projection and current snapshot, set `last_published_tick_index`/`pending_score_tick_id`, and commit the tick ledger `SNAPSHOT_READY` last. Do not advance the completed cursor/time.
6. Add a scoring-finalization repository transaction. On success it marks `SUCCEEDED`, clears the pending score, and advances `last_completed_tick_index`/`next_simulation_at`; on failure it records `SCORE_FAILED` without advancing. After tick 95 succeeds it marks the run `COMPLETED`.
7. Emit structured tick telemetry.
8. Add fake-client crash-point tests and a disposable-dataset integration probe covering two concurrent clients, rollback, `SNAPSHOT_READY` retry without republish, `SUCCEEDED` total no-op, delayed retry, partition pruning, byte caps, and staging expiry.
9. Keep legacy `merge_rows()` behavior isolated; do not broaden it into the multi-table publisher.
10. Apply explicit retention: archive/preserve event evidence and delete inactive-run current state only through a separate, documented cleanup command.

#### Test Procedure

```bash
venv/bin/python -m unittest tests.test_simulation_repository tests.test_simulation_cli -v
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m compileall -q app.py heatsafe infra
```

Manual:

```bash
venv/bin/python -m heatsafe.simulation.cli start \
  --scenario heatwave \
  --scenario-version hanoi_heatwave_v1 \
  --seed 42

venv/bin/python -m heatsafe.simulation.cli tick --scenario heatwave
venv/bin/python -m heatsafe.simulation.cli tick \
  --scenario heatwave \
  --tick-id "<tick-id>"
venv/bin/python -m heatsafe.simulation.cli status --scenario heatwave
```

#### Data Verification

```sql
SELECT tick_index, simulation_time, snapshot_id, status,
       driver_count, order_event_count, output_checksum
FROM `<project>.<dataset>.simulation_ticks`
WHERE simulation_run_id = @run_id
ORDER BY tick_index;
```

```sql
SELECT snapshot_id, COUNT(*) AS zones,
       COUNT(DISTINCT observed_at) AS observed_times
FROM `<project>.<dataset>.zone_snapshots_current`
WHERE scenario_id = 'heatwave'
GROUP BY snapshot_id;
```

Expected: one row, ten zones, one observed time.

```sql
SELECT zone_id, active_drivers, fresh_drivers, exposed_2h, exposed_4h
FROM `<project>.<dataset>.zone_snapshots_current`
WHERE scenario_id = 'heatwave'
  AND (
    active_drivers != fresh_drivers + exposed_2h
    OR exposed_4h < 0
    OR exposed_4h > exposed_2h
  );
```

Expected: zero rows.

Retry the published tick by exact `--tick-id`. In the Phase 3 repository probe, use a deterministic fake-success scoring finalizer to prove completed-cursor advancement without depending on Phase 4 ML changes. Confirm `SNAPSHOT_READY` retry never republishes state/events and `SUCCEEDED` retry is a total no-op. Two different production snapshots become a Phase 4 proof boundary after scoring integration exists.

#### Failure Scenarios

- Concurrent tick caller.
- Expired versus fresh lease.
- Staging write failure.
- Transaction failure before current snapshot publication.
- Expired owner attempts to publish after a new owner acquired the lease.
- Transaction conflict is retried only within the bounded retry policy.
- Process crash leaves an orphan staging table that must expire automatically.
- Missing previous current state.
- Tick invoked after run completion.

#### Phase 3 Execution Evidence — 24-07-2026

Implemented within the approved local/fake-client boundary:

- `heatsafe.simulation.repository`: 96-tick precreation, active-run exclusion,
  conditional fencing-token lease, lease expiry, deterministic replay resume,
  lineage-complete driver/zone/order row projection, idempotent
  `SNAPSHOT_READY` retry, separate score finalization, and completed-cursor
  advancement exactly once.
- `heatsafe.simulation.cli`: `validate-scenario`, `start`, `tick`, `status`,
  `pause`, and `resume` adapters. The CLI contains no SQL or control authority.
- BigQuery publication SQL shape: byte cap, labels, in-transaction lease
  assertion, one-hour expiring per-tick driver/zone/order staging tables,
  weather/operations/demand/driver-history/order/current-state/current-snapshot
  MERGEs, and `SNAPSHOT_READY` before commit.
- Durable BigQuery lifecycle SQL: run/coordinator creation, all 96 precreated
  tick identities, status reload for a later CLI process, pause/resume,
  conditional lease read-back assertion, and separate score-finalization cursor.
- `scripts/probe_phase3_bigquery.py` is dry-run by default and refuses any
  dataset not prefixed `heatsafe_phase3_probe_`; its explicit `--execute` path
  provisions then deletes only that disposable dataset.
- Automated evidence: `14` targeted repository/CLI tests and the full `110`
  test suite pass; compile and dependency checks pass. Local
  `validate-scenario --memory` also returns the expected 96 weather points.

Review boundary:

- The isolated provider probe has now proved a real concurrent lease winner,
  durable cross-process reload/retry, injected transaction rollback preserving
  `RUNNING`, separate score finalization, and cursor/tick read-back. Publisher
  and Phase 3 probe count queries enforce a 250 MB billing cap; staging tables have a
  one-hour expiry set before the transaction. The session does not wait an hour
  merely to observe expiry.
  No shared demo dataset was queried or mutated.
- Therefore Phase 3 remains `🧪 TESTING`, not `✅ VERIFIED`, solely until the
  user confirms this Hybrid evidence. Do not begin Phase 4 before that
  confirmation.

Hybrid correction (24-07-2026): the first isolated probe caught a real
cross-runtime identity defect: BigQuery `TO_HEX` returns uppercase/full 64-char
SHA-256 while Python uses lowercase 32-char digests. Tick and snapshot SQL now
applies `SUBSTR(LOWER(...), 1, 32)`; the probe dataset was deleted by its
`finally` cleanup before this correction.

Hybrid correction (continued): the next isolated probe reached staging and
caught non-JSON-serializable Python datetimes. Staging now emits RFC 3339 text
for every datetime before `load_table_from_json`; fake-client coverage asserts
that no raw datetime reaches the BigQuery client.

Hybrid correction (continued): provider execution then caught `INSERT ROW`
positional alignment against autodetected staging schemas. Each staging load now
uses the exact schema of its target table, preserving BigQuery column order and
types before the fenced transaction runs.

Hybrid correction (continued): real row-count inspection found that publish did
not persist `last_published_tick_index`/`pending_score_tick_id`, so the score
finalizer could not advance the completed cursor. The same fenced transaction
now updates those run fields immediately before committing `SNAPSHOT_READY`.

Hybrid evidence (24-07-2026): on disposable dataset
`cohort2track2.heatsafe_phase3_probe_20260724p`, two independent repository
clients produced exactly one `LEASED` winner. After publish/finalize, direct
read-back returned one `SUCCEEDED` tick and 95 `PENDING`,
`last_published_tick_index=0`, `last_completed_tick_index=0`, and a null pending
score. The process deleted the dataset in its `finally` block. A subsequent
probe injected a failed BigQuery transaction; direct read-back still showed the
run `RUNNING` before it proceeded to the same exclusive lease path and cleanup.

Hybrid correction (continued): a restarted repository initially could not map
BigQuery `simulation_run_id` into the repository `run_id`. Tick reload now
performs that explicit mapping, and a durable `SUCCEEDED` retry reconstructs
only its deterministic local projection cache, never a second publication.

#### Done Criteria

- One tick publishes one coherent snapshot; its targeted retry is no-republish and its fake-success finalization advances the cursor exactly once.
- Retry is logically idempotent.
- No mixed snapshot is observable.
- User confirms BigQuery evidence before Phase 4.

User confirmation was received on 24-07-2026:
`confirmed phase 3 ok`. Phase 3 is `✅ VERIFIED`.

### Phase 4 — Snapshot Scoring and Closed-Loop SafePause

**Status:** ✅ VERIFIED — automated/disposable-cloud proof green and user-confirmed 24-07-2026
**Dependencies:** Phase 3 ✅ VERIFIED
**Estimate:** 1 day

#### Stage 0: Pre-Phase Research

1. Re-read `score_snapshot`, repository prediction queries, audit proposal JSON, decision-service selection, and fail-closed tests.
2. Inspect one real proposal JSON to verify driver decisions are available and add proposal lineage (`scenario_id`, source run/tick/snapshot, expiry) without granting control authority.
3. Define the `heatsafe-simulation-control` Job, `queue-control` command, proposal checksum/decision-count rule, wall-clock authorization TTL, simulation-time validity window, caps, atomic exact-lineage consumption, and public approval-disabled behavior.
4. Define heatwave TimesFM history seeding and simulation-time anchoring while preserving the live wall-clock branch.
5. Present the feature SQL, scoring envelope/OOD policy, retention policy, and action lifecycle mapping; stop for approval.

#### Stage 0 Decision Record — 24-07-2026

Research and frozen implementation decisions are recorded in
[`phase4_scoring_control_RESEARCH_24-07-26.md`](phase4_scoring_control_RESEARCH_24-07-26.md).
The user explicitly authorized full Phase 4 execution on 24-07-2026. Shared
production/demo mutation, deployment, IAM, Cloud Run, and Scheduler remain out
of scope; provider proof uses only a disposable prefixed dataset.

#### Implementation

1. Add explicit `feature_source` handling to `score_snapshot`.
2. Materialize heatwave `driver_current_features` from `driver_simulation_state`, not `GENERATE_ARRAY`; preserve raw state and emit explicit bounded scoring features plus clipping/OOD flags.
3. Preserve all existing model feature names and action counterfactuals.
4. Invoke scoring with explicit `simulation_run_id`, `tick_id`, `snapshot_id`, and `simulation_time`; require ten coherent zones and write tick status `SCORED`/`SCORE_FAILED`.
5. Keep current features current-only, but append/MERGE forecasts and predictions by deterministic run/tick/snapshot/model/action keys. Retain all replay forecasts/predictions for 30 days after run completion and never delete them before the Phase 6 evidence pack is archived; cleanup is an explicit partition-pruned command.
6. Anchor heatwave TimesFM context to `@simulation_time` and run-scoped seeded history; keep live forecasting wall-clock based.
7. Add `queue-control` to `heatsafe.simulation.cli` and deploy it only through the `heatsafe-simulation-control` Job. It loads the proposal/predictions, verifies immutable checksum/count and exact lineage, and takes no free-form actor field.
8. Query trusted immutable `simulation_control_events`, anti-join `simulation_control_consumptions`, revalidate exact lineage/payload/clocks/caps, and atomically write one deterministic consumption/rejection/expiry receipt plus any driver lifecycle events.
9. Apply assignment, delay, travel, pause, completion, recovery, and economics on later substeps.
10. Ensure current baseline scores reflect the updated driver state.
11. Add lineage/payload mismatch, both expiry clocks, duplicate consumption, public non-authority, score failure, OOD, and selected-versus-control tests.

#### Test Procedure

```bash
venv/bin/python -m unittest \
  tests.test_simulation_scoring \
  tests.test_simulation_interventions \
  tests.test_core -v
```

Manual E2E:

1. Start run and execute a tick.
2. Score and open the Streamlit app.
3. Authenticate as a trusted operator and run:

   ```bash
   gcloud run jobs execute heatsafe-simulation-control \
     --region "$GOOGLE_CLOUD_REGION" \
     --args="queue-control,--proposal-id=<proposal-id>,--run-id=<run-id>,--source-tick-id=<tick-id>,--source-snapshot-id=<snapshot-id>"
   ```

   Public/unauthenticated execution must return permission denied and write zero control rows.
4. Execute enough ticks to cover delay and pause duration.
5. Compare selected and control driver histories.

#### Data Verification

```sql
SELECT snapshot_id, COUNT(DISTINCT prediction_run_id) AS runs,
       COUNT(DISTINCT driver_id_hash) AS drivers
FROM `<project>.<dataset>.driver_risk_predictions`
WHERE scenario_id = 'heatwave'
GROUP BY snapshot_id
ORDER BY snapshot_id DESC
LIMIT 2;
```

```sql
SELECT event_time, driver_id_hash, event_type, completed_rest_minutes,
       baseline_risk_probability, action_risk_probability
FROM `<project>.<dataset>.driver_intervention_events`
WHERE intervention_id = @intervention_id
ORDER BY driver_id_hash, event_time;
```

```sql
SELECT event_time, driver_status, continuous_exposure_minutes,
       rest_minutes_120m, current_intervention_id
FROM `<project>.<dataset>.driver_state_history`
WHERE simulation_run_id = @run_id
  AND driver_id_hash = @driver_id
ORDER BY event_time;
```

#### Failure Scenarios

- No evaluated model.
- Prediction does not match current snapshot.
- Proposal has no driver decisions.
- Public audit approval is presented as if authoritative.
- Control lineage is stale/mismatched, expired, over cap, or processed twice.
- Proposal payload/selected decisions changed after the control checksum was created.
- Driver goes offline before pause begins.
- Scoring fails after snapshot commit.

#### Phase 4 Execution Evidence — 24-07-2026

Implementation and review are recorded in
[`phase4_scoring_control_REPORT_24-07-26.md`](phase4_scoring_control_REPORT_24-07-26.md).

- Simulation scoring reads persisted current drivers with exact
  run/tick/snapshot/time lineage, retains raw and bounded/OOD feature evidence,
  appends deterministic TimesFM forecasts and counterfactual predictions, and
  advances through `SCORED` to `SUCCEEDED`.
- Score failure remains coherent/pending and exact retry succeeds without
  republishing. The legacy/live branch retains wall-clock driver-generation
  behavior and its regression tests.
- Review corrected the model projection to the exact frozen Phase 2 envelope.
  Tick 0 and tick 1 were marked `MODEL_INPUT_OOD`, retained for monitoring, and
  excluded from trusted control; tick 2 was the first non-OOD control source.
- Public audit remains non-authoritative. Trusted `queue-control` verifies
  payload checksum/count, lineage, both clocks and caps without a free-form
  actor. A new worker deterministically consumes the control and atomically
  publishes intervention lifecycle plus one receipt.
- Disposable TimesFM provider evidence: 20,160 context rows, ten zones, 160
  future rows, first horizon `+15m`, two-run deviation `0.0`, 745,920 processed
  bytes under a 250 MB cap, and automatic cleanup.
- Disposable exact scoring/closed-loop evidence: four retained prediction
  snapshots / 27,522 rows, four retained forecast snapshots / 640 rows, two
  selected drivers mutated, two intervention rows, one `APPLIED` receipt,
  control `CONSUMED`, tick 3 `SUCCEEDED`, cursors at `3`, no pending score, and
  automatic cleanup.
- Publisher provider measurement required a bounded 350 MB cap after the
  complete control transaction. The combined scoring/TimesFM script uses a
  bounded 300 MB cap after its OOD-safe version billed 230,686,720 bytes and
  required one additional 20,971,520-byte statement; the standalone TimesFM
  probe remains capped at 250 MB.
- Full suite: 129 tests green; compile, dependencies, strict plan validation and
  diff checks pass.

#### Done Criteria

- Exact-snapshot predictions exist for the active tick.
- Selected drivers show valid downstream state changes.
- Control drivers are not mutated by the intervention.
- Public/unauthenticated users cannot write authoritative control state.
- TimesFM horizon is replay-time-relative and tick history remains queryable after later ticks.
- The mandatory disposable TimesFM Hybrid probe passes: 21-day run-scoped 15-minute seed ending at `@simulation_time`; `MAX(context_time) <= @simulation_time`; `MIN(forecast_at) > @simulation_time`; ten successful zones; two-run results within an explicitly recorded tolerance; live-branch regression green; partition/byte ceiling respected.
- Model failure remains monitoring-only.
- User confirms the closed-loop behavior.

User confirmation was received on 24-07-2026:
`onfirmed Phase 4 OK` (understood as `confirmed Phase 4 OK`).
Phase 4 is `✅ VERIFIED`. The next valid transition is an inter-phase process
update before Phase 5 research or execution.

### Phase 5 — Cloud Run Job and Optional Scheduler

**Status:** 🧪 TESTING
**Dependencies:** Phase 4 ✅ VERIFIED
**Estimate:** 0.5 day

Inter-phase UPDATE PROCESS completed on 24-07-2026. Phase 4 confirmation was
reconciled, live provider inventory was read back, the paused legacy scheduler
was preserved, and Phase 5 research/execute authority was received. Research is
recorded in `phase5_cloud_run_scheduler_RESEARCH_24-07-26.md`.

Research corrected one material IAM assumption: publisher load staging tables
cannot live in the authoritative dataset without dataset-level create
permission. Phase 5 therefore uses the dedicated one-hour-expiry
`heatsafe_sim_staging` dataset, exact table IAM in `heatsafe_data`, and an
exact-resource IAM Condition scoped to the prediction model. Scheduler remains
default-off. The historical one-minute/`<=45s` gate below is retained as
Phase 5 evidence but is superseded for Phase 5R completion by the V2
accelerated-replay contract: two-minute cadence, `FULL` p95 `<=105s`, every
dispatch-to-terminal interval `<120s`, and zero overlap.

#### Stage 0: Pre-Phase Research

1. Verify current gcloud syntax against official Cloud Run Scheduler documentation.
2. Confirm exact identities and bindings for public reader, trusted operator, simulator runtime, scorer/trainer, deployer, and Scheduler caller.
3. Inspect current Scheduler resources to avoid name collision and preserve the paused legacy `heatsafe-live-ingest-15m` resource.
4. Measure manual tick duration and dry-run/actual bytes before enabling a one-minute cadence.
5. Present commands, pinned image digest, IAM migration order, terminal-pause owner/SLA, and cost/write cadence; stop for deployment approval.

P0 identity matrix (exact service-account names may receive the project suffix):

| Principal | Resource-scoped grants | Explicit denial/absence proof |
|---|---|---|
| `heatsafe-public-reader` | Project `roles/bigquery.jobUser`; dataset `roles/bigquery.dataViewer`; runtime identity for the public service | No dataset/table editor, control-job invoke, Scheduler, Storage writer, or Vertex role |
| trusted operator group | Job-level `roles/run.invoker` on `heatsafe-simulation-control` only | Cannot invoke tick job or write BigQuery directly |
| `heatsafe-sim-control-writer` | Project `roles/bigquery.jobUser`; table-level viewer on proposals/predictions/runs/ticks; table-level `roles/bigquery.dataEditor` only on `simulation_control_events` | No dataset-wide editor, tick-job invoke, Scheduler, Storage, or Vertex role |
| `heatsafe-sim-runtime` | Project `roles/bigquery.jobUser`; table-level `roles/bigquery.dataEditor` on simulator-owned state/event/projection/prediction tables and `simulation_control_consumptions`; table-level viewer on immutable controls/proposals/audit/model inputs; runtime/logging permissions | No editor on `simulation_control_events`, proposals, or audit; no Scheduler/admin/Run-invoker role; fixture is packaged, so no Storage role |
| `heatsafe-trainer` | Project `roles/bigquery.jobUser`; model/training dataset `roles/bigquery.dataEditor` | No public service, control, Scheduler, or Run-admin role |
| `heatsafe-sim-scheduler` | Job-level `roles/run.invoker` on `heatsafe-simulation-tick` only | No BigQuery, Storage, Vertex, control-job, or project-wide Run role |
| deployer human/CI | `roles/run.admin`, `roles/cloudscheduler.admin`, and `roles/iam.serviceAccountUser` only on the named workload service accounts; API enablement remains a separately approved bootstrap action | Not used as any runtime identity |

Scoring executes in-process inside the tick job under `heatsafe-sim-runtime`; there is no separate scorer identity in P0. Training remains a separate job/identity. IAM validation must read back every binding and run negative permission tests for each “absence proof.” Broad project roles on legacy `heatsafe-demo` are revoked only after its public/ingest/train/score workloads have been migrated and smoke-tested; the exact before/after binding manifest is retained.

The IAM negative suite must prove `heatsafe-sim-runtime` cannot insert/update/delete `simulation_control_events`, while `heatsafe-sim-control-writer` cannot write `simulation_control_consumptions` or driver/current-state tables.

#### Implementation

1. Keep the existing Scheduler API state; do not disable/re-enable it during rollback because missed executions can run after re-enable.
2. Add a separate simulator deployment command rather than coupling schedule enablement to public UI/schema/score deployment.
3. Deploy `heatsafe-simulation-tick` and `heatsafe-simulation-control` from one recorded image digest under their separate runtime identities. Tick uses `tasks=1`, `parallelism=1`, `max-retries=1`, `task-timeout=300s`, `cpu=1`, and `memory=1Gi`; control uses `tasks=1`, `parallelism=1`, `max-retries=0`, and a 60-second timeout.
4. Require measured p95 tick duration `<=45s` before schedule enablement; set lease TTL to `360s`. A fresh lease returns a bounded no-op but does not hide overlapping-execution cost.
5. Add an explicit deployment flag such as `--enable-simulation-schedule`.
6. Create/update `heatsafe-simulation-every-minute` using:
   - HTTP `POST`
   - Cloud Run Jobs v2 `:run` URI
   - OAuth service account
   - `Asia/Ho_Chi_Minh` timezone
   - `* * * * *` schedule
   - `attempt-deadline=30s`
   - Scheduler `max-retry-attempts=0`
7. Grant only job-level `roles/run.invoker` on the simulation job to the Scheduler caller; it has no BigQuery, Storage, Vertex, or project-wide Run role.
8. Give the simulator runtime project-level `roles/bigquery.jobUser`, dataset-scoped data access, and only bucket-scoped object-read if the packaged fixture is not used.
9. Migrate IAM in order: create identities → grant scoped roles → deploy/smoke-test → switch workload identity → verify → revoke obsolete broad roles.
10. Add pause, resume, force-run, terminal alert, and delete instructions. The operator pauses/deletes the schedule within five minutes of `COMPLETED`; all later dispatches remain logical no-ops.
11. Enforce per-query/per-tick/full-replay byte ceilings and record maximum execution/task-attempt counts in the cost evidence.

#### Test Procedure

```bash
gcloud run jobs execute heatsafe-simulation-tick \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --region "$GOOGLE_CLOUD_REGION" \
  --wait
```

After scheduler creation:

```bash
gcloud scheduler jobs run heatsafe-simulation-every-minute \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --location "$GOOGLE_CLOUD_REGION"
```

#### Data Verification

- One scheduler dispatch produces one new successful tick.
- Two near-simultaneous dispatches produce at most one logical tick.
- IAM read-back proves the Scheduler caller has only job-level invoke permission.
- Cloud Logging contains the same `simulation_run_id`, `tick_id`, `snapshot_id`, and checksum as BigQuery.
- Pausing the scheduler stops advancement without changing the run state.
- Tick 95 emits a terminal signal; schedule state becomes `PAUSED` or absent within five minutes, and a later dispatch is a no-op.

#### Done Criteria

- Job invocation is authenticated and least-privilege.
- Scheduler is explicitly opt-in and operationally reversible.
- Cleanup pauses Scheduler first, waits for active executions, then deletes only the new scheduler/job/binding while preserving image and BigQuery evidence.
- User confirms recurring execution before Phase 6.

#### Execution Result — 24-07-2026

Implementation and provider execution are complete; evidence is recorded in
`phase5_cloud_run_scheduler_REPORT_24-07-26.md`.

- Final pinned image:
  `sha256:f30511403e41d386d499ccb0fbc2085c7f22721798a212318f0ebedcb878280c`.
- Both jobs, scoped IAM, expiring staging dataset, and the optional Scheduler
  are deployed.
- Manual resume, lineage, IAM readback, overlap fencing, and Scheduler OAuth
  dispatch pass.
- Three successful tick container durations are `49.455s`, `87.044s`, and
  `96.568s`; observed p95 is `96.568s`, so the `<=45s` enablement gate fails.
- `heatsafe-simulation-every-minute` remains `PAUSED`; the legacy
  `heatsafe-live-ingest-15m` scheduler remains `PAUSED` and untouched.
- Full local suite: 140 tests pass.

Phase 5 stays `🧪 TESTING`. It must not transition to `✅ VERIFIED` or unlock
Phase 6 until latency is brought under the accepted gate and the user confirms
recurring execution.

### Phase 5R — Incremental Checkpoint and Risk-Adaptive Latency Remediation

**Status:** 🔨 STAGES 1–4 CODE COMPLETE/LOCAL GREEN — STAGE 5 provider proof
blocked until replay-clock correction is implemented and revalidated

**Dependencies:** Phase 5 deployed evidence; Scheduler remains `PAUSED`

**Complexity:** Complex remediation inside the existing Phase 5 proof boundary

**Execution authority:** Phase 5R EXECUTE has progressed through Stages 1–4.
This amendment authorizes no new source/provider/Scheduler mutation by itself;
resume requires the existing execution gate and the Stage 5 sequence below.

> **TL;DR:** Make GCS incremental checkpoints the production hot path, retain
> replay-from-zero as an oracle/fallback, remove known BigQuery round trips and
> scans, and preserve deterministic risk-adaptive work. First correct the
> replay clock so ledger time, engine time, forecast context, and fixture time
> all share the 26-05-2026 scenario epoch. Then prove the accelerated replay at
> two minutes per tick. Parallel forecast versus feature/ML is conditional on
> the corrected baseline, not a prerequisite.

#### Phase 5R V2 Amendment — 25-07-2026

**Why the plan changed:** The disposable v7 provider run completed ticks 0–3
but tick 4 failed because the run ledger was anchored to Cloud Run wall time
(July) while the engine and observed demand were anchored to the fixture
(26-05-2026). TimesFM was therefore asked to produce ten valid series from a
misaligned replay window. This is a correctness failure, not evidence that
TimesFM latency is the dominant bottleneck.

**Evidence carried forward:**

- Local Phase 5R implementation is green across 168 tests plus `compileall`,
  `pip check`, shell syntax, and diff checks.
- Disposable image v7 was
  `sha256:a40bd4c367aae71ba9d1bc3ff3696d5802dd7e33b124383b85de49effa465076`.
- Tick-4 failed after `71.180s`; measured components included checkpoint restore
  `7.858s`, checkpoint upload `7.789s`, publication `15.467s`, and scoring
  failure after `14.789s`.
- Stage 0E measured `AI.FORECAST` at `6.431s`; the earlier estimate of
  `20–25s` is rejected for planning purposes.
- v7 tick-1 BigQuery use was approximately `470 MB` versus approximately
  `629 MB` in v6. The projected full replay remains close to, but below, the
  `50 GB` cumulative cap and must still be enforced before dispatch.
- All failed-run disposable jobs, datasets, and bucket objects were cleaned up.
  The production Scheduler remains `PAUSED`; the immutable v7 image is retained
  only as evidence.

**Current stage ledger:**

| Stage | State | Exit condition |
|---|---|---|
| Stage 1 — checkpoint codec/store | 🔨 Code complete, local green | Final deployed codec/GCS equivalence and IAM proof |
| Stage 2 — incremental hot path/fencing | 🔨 Code complete, local green | Correct replay epoch plus deployed recovery/concurrency proof |
| Stage 3 — BigQuery critical-path reduction | 🔨 Code complete, local green | Corrected provider baseline and byte evidence |
| Stage 4 — risk-adaptive scoring/lineage | 🔨 Code complete, local green | Corrected forecast/scoring lineage across FULL/retry cases |
| Stage 5 — provider proof/closeout | 🚧 Blocked | Execute the ordered V2 sequence below and pass `96+1` |

**Ordered execution sequence:**

1. **Correct replay-clock ownership.** Derive
   `simulation_runs.simulation_start_at`, all precreated
   `simulation_ticks.simulation_time` values, and the in-memory run start from
   the scenario fixture's first timezone-aware timestamp. Wall clock remains
   limited to leases, authorization, audit, and job timing. Reject
   `scenario_id != fixture.manifest["scenario_id"]`.
2. **Prove the clock contract locally.** Add tests for fixture epoch,
   BigQuery start parameters, tick 0/4/95 timestamps, checkpoint restore,
   retry, and a negative guard that rejects ledger/engine/forecast-context
   drift before `AI.FORECAST` is dispatched. A restored checkpoint is accepted
   only when scenario version, seed, fixture epoch, and predecessor minute index
   match the active run/tick.
3. **Deploy a new immutable candidate with Scheduler paused.** Use unique
   disposable job/dataset/staging-dataset/bucket/run names and exact IAM/config
   readback. Never reuse the failed v7 run.
4. **Run a corrected manual baseline.** Execute ticks 0–4 first, require tick 4
   to score all ten zones, then collect at least 20 representative `FULL` ticks
   including 24/48/95 with per-component timing and bytes.
5. **Apply the conditional optimization gate.**
   - If corrected `FULL` p95 is `<=90s`, defer parallel scoring.
   - If `90s < p95 <=105s`, run a bounded serial-versus-parallel A/B experiment
     because cadence passes but lacks comfortable headroom.
   - If p95 is `>105s`, the parallel experiment is mandatory before the replay
     can proceed; do not weaken correctness or byte gates to pass it.
6. **Run the accelerated replay proof.** Use a disposable authenticated
   Scheduler with `*/2 * * * *`, complete 96 ticks plus invocation 97, require
   zero overlap/duplicate logical ticks, and clean up disposable resources.
7. **Close out only after evidence review.** Preserve the two operating
   profiles below, keep recurring execution disabled by default, and require
   explicit user confirmation before enabling either Scheduler.

**Operating profiles:**

| Profile | Scheduler cadence | Purpose | Latency contract |
|---|---:|---|---|
| `accelerated-replay` | 2 minutes (`*/2 * * * *`) | Hackathon/demo replay; 96 ticks take about 192 minutes | `FULL` p95 `<=105s`, every dispatch-to-terminal interval `<120s`, zero overlap |
| `real-operations` | 15 minutes (`*/15 * * * *`) | Future wall-clock-aligned operations | Target p95 `<=120s`, fail-safe `<300s`; requires separate production-readiness evidence |

**Execution estimate after V2 validation:**

| Work item | Expected elapsed time |
|---|---:|
| Replay-clock implementation plus local gates | 0.25–0.5 day |
| New candidate deployment, IAM/config readback, ticks 0–4 | 0.25–0.5 day |
| Corrected 20-`FULL` baseline and evidence synthesis | 0.5 day |
| Stage 3P serial/parallel experiment | 0 days if deferred; up to 0.5 day if triggered |
| Scheduled `96+1` proof | At least 3h12 wall time plus evidence/cleanup |

Cloud Run Service with RAM-resident state is explicitly out of scope. The
selected architecture remains Cloud Run Jobs plus immutable GCS checkpoints.
Storage Write API, removal of BigQuery current-state projections, and
fire-and-forget asynchronous forecast jobs are also deferred because they alter
the fenced publication/retry contract more than this Stage 5 blocker requires.

#### Review of the Latency Discussion

| Claim or risk | Review against current source/evidence | Plan treatment |
|---|---|---|
| Replay-from-zero is `O(t)` | Confirmed in `replay_to_tick()`: every invocation advances all prior ticks before the requested tick | Replace only the production hot path; retain the function unchanged as oracle/fallback |
| BigQuery ML itself explains most of `96.568s` | Not proven. Only three successful end-to-end samples exist and current logs do not separate restore, advance, staging, publication, TimesFM, prediction, and explanation | Stage 0 adds component timings and job metadata before optimization |
| Nine staging loads are sequential | Confirmed; `_stage_rows()` is called serially and each load waits on `.result()` | Parallelize independent loads with a bounded worker pool after concurrency feasibility proof |
| Staging expiration adds two API calls per table | Confirmed; the staging dataset already has default one-hour TTL | Delete per-table `get_table`/`update_table` expiration calls; verify dataset TTL on deploy/readback |
| `driver_state_history` MERGE scans too broadly | Confirmed; join uses only `state_id`, without a target partition predicate | Add exact `event_time`/tick partition pruning and verify processed bytes |
| TimesFM context seed is regenerated each tick | Confirmed; the 21-day context source and MERGE execute inside every simulation score | Seed once per run or idempotently on first use; keep only current tick demand append on later ticks |
| Checkpoint can preserve determinism | Correct if serialization is lossless and metadata/object commit ordering is fenced | Add a versioned lossless codec, immutable object generation, read-back verification, equivalence and corruption gates |
| Current `canonical_json()` can be reused as checkpoint bytes | Rejected. It formats floats to `.6f`; equal canonical checksums do not prove future calculations are bit-for-bit unchanged after a lossy round-trip | Encode floats losslessly (for example IEEE-754 hex strings in an explicit schema); use canonical checksum only as a logical comparison |
| GCS partial upload can expose a truncated checkpoint | GCS object publication is atomic, but crash/orphan, wrong generation, corrupt payload, and metadata divergence remain real failure modes | Use `ifGenerationMatch=0`, payload hash, generation match, deserialize validation, state checksum, and transactionally committed metadata |
| New controls can be missed after restore | Confirmed architectural risk if controls are embedded in checkpoint or loaded before restore | Fetch authoritative controls after restore; persist only resulting intervention state; test exact validity boundaries |
| Forecast reuse can become stale | Confirmed | Persist source lineage/age and force refresh on mode escalation, horizon exhaustion, or validated demand anomaly |
| Orders/events make checkpoint grow without bound | Not confirmed in current engine. Terminal orders are removed each minute, tick events reset, completed interventions are aged out, and driver rolling arrays are capped at 60/120 minutes | Do not add terminal-order pruning. Measure checkpoint size at ticks 0/24/48/95 and add a regression ceiling |
| Hard-coded daytime scheduling is safe | Rejected. The fixture remains at least `EXTREME_CAUTION` overnight and exposure/recovery crosses daypart boundaries | Use deterministic `FULL`/`MONITOR`/`RECOVERY` policy; never skip state advancement |

#### Performance Budget

The first Stage 0 provider baseline owns the final component allocations. Until
then, the non-negotiable external budgets are:

| Metric | Gate |
|---|---:|
| Accelerated-replay representative `FULL` tick p95, minimum 20 measured ticks including 24/48/95 | `<=105s` |
| Any accelerated-replay dispatch-to-terminal interval | `<120s`, measured from Scheduler dispatch through terminal Cloud Run execution |
| Cloud Run execution fail-safe | `<300s` timeout |
| Checkpoint restore + verify + upload + verify p95 | `<=3s`, measured rather than assumed |
| Checkpoint logical equivalence | `100%` at tick 0/24/48/95 and control-boundary fixtures |
| Duplicate logical tick/checkpoint under overlap or retry | `0` |
| Stale prediction presented as current after a skip | `0` |
| Full accelerated replay BigQuery bytes | `<=50,000,000,000`, enforced cumulatively before dispatch |
| Scheduler during remediation | `PAUSED` |

Stage 0 must record duration and BigQuery job/byte evidence for:

```text
lease/read state
controls load
checkpoint restore or fallback
advance_tick
publication row projection
staging schema lookup
each staging load
fenced publication query
TimesFM context ensure
AI.FORECAST
ML.PREDICT
ML.EXPLAIN_PREDICT
score finalization
checkpoint serialize/upload/read-back
total container duration
```

#### Stage 0 — Baseline and Contract Freeze

**What happens:** Freeze the research contract first, then—only after separate
EXECUTE authorization—add instrumentation and run bounded provider probes.

**Stage 0R — read-only research: ✅ COMPLETE**

1. Freeze the component event schema:
   `component` enum, execution/task-attempt/retry IDs, optional BigQuery job ID,
   monotonic `elapsed_ms`, `slot_millis`, processed/billed bytes, outcome, and
   exactly one `tick_total` event per attempt. Logs exclude checkpoint/control
   payloads and driver rows.
2. Confirm the pinned Python/container version and statically verify GCS retry,
   regional bucket, object-precondition, BigQuery job-statistics, and Cloud Run
   logging APIs. Provider behavior remains an EXECUTE-only probe.
3. Freeze the checkpoint codec candidate schema, object path, compressed and
   expanded byte/count ceilings, decompression-ratio guard, retry/deadline
   policy, deterministic gzip header requirements, and version-mismatch fallback
   before implementation.
4. Pre-register the TimesFM evaluation:
   - latency: one fixed replay cutoff, ten repeated calls per window;
   - quality: 21 leakage-free folds covering seven held-out days at local
     origins `05:45`, `10:45`, and `16:45`, each with horizon 16;
   - actual series: the same run-scoped 15-minute demand source, strictly after
     each fold cutoff and never present in model input;
   - metrics: city-wide and per-zone WAPE/MAE with a recorded non-zero actual
     denominator, 90% interval coverage, peak interval error, SafePause
     feasibility/guardrail result, and selected-driver-count delta;
   - acceptance: AI.FORECAST p95 improves at least 20%, WAPE is no more than 5%
     relatively worse, interval coverage is no more than five percentage points
     worse, peak error is at most one 15-minute interval worse than baseline,
     no feasibility/guardrail result flips, selected count changes at most 5%,
     and the complete FULL tick improves.
5. Freeze the execution-mode policy using existing HeatSafe risk tiers,
   30-minute fixture look-ahead, exposure cohorts, controls/interventions, and
   hysteresis. Any heat-dose trigger remains an internal operational threshold,
   explicitly not a clinical boundary.
6. Freeze the demand anomaly rule from replay data. Prefer city-wide WAPE plus
   a minimum-volume condition over raw per-zone percentage error near zero.
7. Present Stage 0R findings and stop. No source edit, provider query, bucket,
   dataset, job, IAM, or Scheduler mutation is authorized in RESEARCH mode.

**Frozen Stage 0R contract:** see
[`phase5r_latency_remediation_RESEARCH_24-07-26.md`](phase5r_latency_remediation_RESEARCH_24-07-26.md).
It locks the component-event enum/schema, runtime identity gate, checkpoint
codec/path/limits/retry/fallback policy, leakage-free TimesFM corpus and
evaluation, execution-mode hysteresis, and city demand anomaly rule.

Research found that the authoritative 41.1°C fixture is
`EXTREME_CAUTION` or worse for all 96 ticks and contains exposure cohorts from
tick 0. Therefore it legitimately remains `FULL` throughout; risk-adaptive
skipping is not credited toward the Phase 5 latency SLO. Checkpointing and
BigQuery critical-path reduction must independently make the fixed FULL-tick
sample pass.

**Stage 0E — instrumentation and provider experiment (EXECUTE-only):**

1. Add the frozen structured component timers and BigQuery job statistics
   without optimizing the path. Capture the deployed image/base-image digest,
   exact Python/zlib/client-library versions, and codec version into the frozen
   `runtime_contract_id` before generating golden checkpoint bytes. The current
   `python:3.12-slim` Docker tag alone is not an exact runtime pin.
2. Reuse the Phase 5 durations as the production baseline and profile ticks
   0/24/48/95 in a disposable dataset using an oracle-seeded sentinel harness.
   Do not sequentially run 96 pre-optimization BigQuery ticks, jump the active
   cursor, or touch the active evidence run. The sentinel harness proves
   component cost only; Stage 5 owns continuity.
3. Benchmark lossless JSON-hex plus deterministic gzip against the frozen
   schema-stable alternative and retain the codec only if every field
   round-trips exactly without pickle.
4. Run a controlled TimesFM 2.5 context-window experiment on identical,
   replay-time-capped inputs for `512`, `1024`, and `2048` points:
   - pin `model => 'TimesFM 2.5'` instead of relying on the provider default;
   - retain `2048` as the baseline because 15-minute data covers approximately
     21.33 days, while `1024` covers 10.67 days and `512` only 5.33 days;
   - keep `horizon=16` because the four-hour horizon supports forecast reuse;
   - measure `AI.FORECAST` elapsed/slot time, total `FULL` tick time, and
     processed/billed bytes over repeated runs;
   - calculate the pre-registered 21-fold metrics and downstream decisions;
   - select a smaller window only if every frozen latency/quality/decision
     threshold passes;
   - otherwise retain `2048`. The proposed “approximately 50% faster” result is
     an unverified hypothesis, not an acceptance assumption.
   - create the frozen disposable evaluation corpus from
     `earliest_cutoff - 2,047 * 15 minutes` through
     `latest_cutoff + 16 * 15 minutes`; the existing 21-day seed alone cannot
     supply both a 2,048-point context and seven held-out evaluation days;
   - disable query cache, assert corpus timestamps/count/ten-zone checksum
     before billing, use one warm-up plus ten measured calls per window, and
     compute the 21 folds exactly as pre-registered.
5. Confirm the input subquery still exposes only `zone_id`, `interval_start`,
   and `requests`—the current implementation already satisfies this—and narrow
   the source time filter/seed volume to the selected context. Changing only
   `context_window` while still aggregating 21 days is not considered the full
   optimization.
6. Present Stage 0E evidence and stop for implementation approval.

**Stage 0E status: ✅ COMPLETE — STOPPED BEFORE STAGE 1**

Evidence:
[`phase5r_stage0e_EXECUTION_24-07-26.md`](phase5r_stage0e_EXECUTION_24-07-26.md).

- The unchanged disposable oracle baseline measured tick totals
  `74.033s / 93.688s / 121.944s / 177.887s` at 0/24/48/95. Tick-95 replay
  alone was `102.165s`; score finalization was `26.412s`, including
  `AI.FORECAST` `6.431s`, `ML.PREDICT` `0.819s`, and
  `ML.EXPLAIN_PREDICT` `1.278s`.
- The UTC-only `json-floathex-gzip-v1` candidate is rejected: typed equality
  passed but next-tick checksum changed because the engine consumes local
  datetime fields. The accepted Stage 1 candidate is
  `json-floathex-offset-gzip-v1`; it passed typed/byte/next-tick equivalence at
  0/24/48/95 and remained below the frozen byte ceilings.
- The TimesFM experiment used 117 cache-disabled jobs over a 26,840-row,
  ten-zone replay-capped corpus. `512` improved p95 `21.193%` but failed
  quality gates; `1024` was `1.191%` slower and failed quality gates. Retain
  `2048`, horizon 16, exactly three input columns, and explicit TimesFM 2.5.
- All disposable datasets/staging datasets and the runtime job were deleted.
  The active model was read-only, active evidence tables/run were excluded,
  and the production Scheduler remains `PAUSED`.

**Green proves:** The team knows which components own the latency and has a
lossless, testable contract before changing the hot path.

#### Stage 1 — Lossless Checkpoint Codec and Store

**What happens:** Implement checkpoint encode/decode, metadata, GCS storage, and
local/in-memory fakes without changing production tick selection.

1. Add a dedicated checkpoint module with an explicit `format_version`,
   complete schema, enum validation, timezone-aware datetimes, integer bitmasks,
   lossless float encoding, bounded collection sizes, and unknown/missing-field
   rejection. Use tagged `float.hex()`/`float.fromhex()`, UTC microsecond
   datetime strings, decimal integers, explicit field order, and static
   constructors only.
2. Make compressed bytes deterministic and record both payload SHA-256 and
   logical `SimulationState` checksum. Use an empty gzip filename/comment,
   `mtime=0`, pinned compression level/runtime, and a container golden hash.
   Enforce both compressed and expanded byte ceilings, a decompression-ratio
   ceiling, collection-count limits, and bounded retry/deadlines before object
   data is accepted.
3. Add `CheckpointStore` protocol plus in-memory and GCS implementations.
4. Use deterministic object names including the frozen input checksum and
   `ifGenerationMatch=0`; on precondition conflict, download and accept only an
   identical object.
5. Read back the uploaded object, verify generation/size/hash, deserialize it,
   and compare logical state before returning metadata.
6. Add nullable checkpoint/execution fields to `simulation_ticks`; provision
   them additively.
7. Provision a dedicated regional checkpoint bucket with uniform bucket-level
   access, a 35-day lifecycle, public-access prevention, and runtime-only
   `roles/storage.objectCreator` plus `roles/storage.objectViewer`. Runtime has
   no object-delete permission; the Scheduler and control identities receive no
   Storage access. Bucket creation/lifecycle/IAM is an explicit deployer
   bootstrap action; any manual deletion requires a separately authorized,
   time-bounded operator role and is never granted to a workload identity.

**Automated gates:**

- State equality and next-tick checksum equality after round-trip at ticks
  0/24/48/95.
- Same bytes/hash for repeated encoding on the pinned runtime.
- Missing field, unknown enum, invalid datetime, non-finite float, wrong format,
  corrupt gzip, payload-hash mismatch, state-checksum mismatch, and oversized
  payload all fail closed.
- Object precondition conflict accepts identical bytes and rejects different
  bytes.
- No pickle dependency or executable deserialization path exists.

**Green proves:** A checkpoint can be stored and restored without changing the
next deterministic tick.

#### Stage 2 — Incremental Hot Path, Fencing, and Recovery

**What happens:** Switch production tick calculation to the previous committed
checkpoint while keeping replay as a verifiable fallback.

1. For tick 0, initialize once. For tick `N`, select only checkpoint metadata
   from the latest committed predecessor, validate object generation/hash/state,
   then call `advance_tick()` once.
2. Load trusted controls after restore, then transactionally freeze the tick
   input manifest under the active lease before advancing or uploading. The
   manifest records effective controls and evaluates wall-clock authorization
   against `input_frozen_at`. Retry must reuse it; later controls move to the
   next tick. Freeze SQL writes manifest/checksum/time only when null under the
   active fencing token, asserts exactly one winner, and every later lease owner
   reads the stored manifest instead of recomputing it. Canonical manifest bytes,
   not BigQuery JSON rendering, are the checksum source.
3. Reconcile the existing control implementation with the immutable-request
   contract before checkpoint rollout:
   - new control rows use immutable `AUTHORIZED`; legacy `QUEUED` rows remain
     readable but are never mutated;
   - pending controls are selected by absence of a receipt;
   - replayed applied history comes from `APPLIED` receipts and consumed tick
     lineage;
   - remove the runtime `UPDATE simulation_control_events` statement so the
     deployed viewer-only IAM boundary is executable.
4. Freeze the run-wide evaluated heat-risk model version before the first
   `FULL` score. Every retry and later tick in the run uses that model; a model
   rotation cannot change prediction identity mid-run.
5. Serialize and upload the new end-state checkpoint before publication.
6. Extend the fenced BigQuery publication transaction to commit exact
   checkpoint metadata with `SNAPSHOT_READY`. An uploaded object with no
   committed tick metadata is an orphan and not a source of truth.
7. Preserve score retry behavior: a `SNAPSHOT_READY`, `SCORED`, or
   `SCORE_FAILED` retry does not recompute or overwrite the checkpoint.
8. On missing/corrupt latest checkpoint, try earlier committed checkpoints in
   descending tick order and replay only the delta. Emit
   `CHECKPOINT_FALLBACK`; fall back to tick zero only when no verified
   checkpoint exists. Every reconstructed intermediate tick uses that tick's
   already frozen input manifest; recovery must not query today's controls or
   re-evaluate wall-clock authorization.
   Every authorization/expiry decision for the tick uses the persisted
   `input_frozen_at`; later `self.now()`/`CURRENT_TIMESTAMP()` values may fence
   the lease but may not change the frozen control set.
9. Keep `replay_to_tick()` callable in audit/CI and compare checkpoint output to
   it at configured sentinel ticks. Never run the oracle on the normal hot path.
10. Add an operator verification/repair command that reports orphans and corrupt
   objects; automatic cleanup is out of the critical path and may delete only
   objects proven unreferenced after the retention window. Runtime cannot
   delete; cleanup requires a separately authorized operator.
11. Add `HEATSAFE_SIMULATION_STATE_MODE=oracle|checkpoint`. New deployments
    start in `oracle` while Scheduler is paused, switch to `checkpoint` only for
    the bounded provider proof, and record the active mode in every tick log.
    Primary rollback is: pause Scheduler → switch the same
    control-migration-compatible image to `oracle` → run one checksum/cursor
    smoke tick → preserve nullable checkpoint metadata/objects → resume only if
    correctness and `FULL`-tick SLO remain green. Image rollback is allowed only
    to a recorded post-control-migration digest with immutable
    `AUTHORIZED`/receipt-only compatibility; the pre-Phase-5R image is not a
    valid rollback target.
12. Refactor the coupled repository flow into explicit, independently
    fault-injectable boundaries:
    `freeze_tick_inputs()` → `compute_from_manifest()` →
    `store_checkpoint()` → `commit_publication()`. No boundary may advance the
    completed cursor before the fenced commit/finalization contract permits it.

**Failure and concurrency gates:**

- Crash after upload but before BigQuery commit.
- Crash after input freeze but before upload; retry sees the identical control
  set, authorization instant, model, and object name.
- Crash after BigQuery publication but before scoring.
- Missing tick N-1 object with valid N-2.
- Corrupt latest object with valid earlier object.
- Two workers upload/publish the same tick.
- Lease loss after object upload.
- Control queued between checkpoint N-1 and tick N.
- Control queued after tick N input freeze is deferred to N+1 and cannot create
  a different orphan for N.
- Authorization and `valid_until_simulation_at` exactly on a tick boundary.
- Checkpoint restore followed by `advance_tick()` equals replay-from-zero for
  no-control, queued-control, active-pause, and completed-pause cases.

**Green proves:** Normal execution is `O(1)` engine ticks, retry-safe, and still
auditable against the original deterministic oracle.

#### Stage 3 — BigQuery Critical-Path Reduction

**What happens:** Remove redundant network work and reduce scans without
changing published row semantics.

1. Remove staging-table `get_table`/`update_table` expiration calls and verify
   the staging dataset's one-hour default TTL at deployment and runtime startup.
2. Cache target schemas within the invocation and test a bounded parallel
   staging-load implementation. Use a small fixed pool, collect all failures,
   and never enter the fenced transaction unless every required load succeeds.
   Keep `workers=1` as the deploy-time fallback until the provider probe proves
   shared-client/quota behavior and a material complete-tick improvement.
3. Add an exact target partition predicate to `driver_state_history` MERGE and
   every other history MERGE found by Stage 0 to scan beyond the current
   partition.
4. Seed the 21-day TimesFM context once per run (at start or first score) with
   an idempotent run-level version/seeded-at/point-count marker. Set the marker
   only after exact range/count assertions succeed. Later ticks append only
   their current demand point and must not regenerate the 20,160-row seed
   source.
5. In the simulation branch only, pin `AI.FORECAST` to `TimesFM 2.5` and use
   the Stage 0 selected context window. Limit the input filter/seed to that
   window and pass exactly `zone_id`, `interval_start`, and `requests`; do not
   claim a column-selection gain because the current query already has this
   shape. Leave the shared legacy/live branch byte-for-byte unchanged and cover
   it with regression tests.
6. Keep `ML.PREDICT` exact-snapshot. Separate `ML.EXPLAIN_PREDICT` cadence or
   eligible cohort only if Stage 0 proves it material; persist explanation
   source lineage and age.
7. Preserve existing per-query byte caps, lower them only after provider
   evidence, and add processed-byte regression assertions.

**Green proves:** A `FULL` tick meets the latency budget without relying on
low-risk skipping, while publication remains atomically identical.

##### Stage 3P — Conditional Parallel Forecast and Feature/ML Experiment

This is an evidence-triggered substage after the replay-clock fix and corrected
serial baseline. It is not permitted to delay the correctness fix or bypass the
serial fallback.

```text
fenced publication commit
        |
        +--> branch A: AI.FORECAST / verified forecast reuse
        |
        +--> branch B: exact feature projection
                         -> ML.PREDICT
                         -> ML.EXPLAIN_PREDICT
        |
        +--> join both outcomes
                -> final fenced scoring transaction
```

1. Parallelize only the independent forecast and feature/ML branches.
   `ML.PREDICT` remains strictly downstream of exact feature projection.
2. Use a bounded two-worker pool and a separate BigQuery client per worker.
   Capture both branch job IDs, duration, processed/billed bytes, outcome, and
   exception; never share mutable query-job state across threads.
3. Join both branches before finalization. No partial branch may advance
   `last_completed_tick_index`, clear the pending cursor, or mark the tick
   `SUCCEEDED`.
4. Forecast failure may use only the already specified, exact-lineage,
   within-horizon reuse path. If no verified source is eligible, persist
   `SCORE_FAILED` and retain the same pending tick for retry.
5. Keep deterministic MERGE/materialization IDs so retrying either branch is
   idempotent. A late branch from a lease loser is rejected by the same fencing
   token as serial scoring.
6. A/B the same immutable image/config/run inputs in serial and parallel modes
   with query cache disabled where applicable. Use at least ten paired complete
   `FULL` attempts per mode on separate disposable runs, counterbalance
   serial/parallel order, and correlate each pair by tick/fixture/config/image.
   Accept parallel mode only if:
   - complete `FULL` tick p50 and p95 improve by at least `15%`;
   - prediction, explanation, forecast materialization, checksum, and lineage
     results are identical;
   - no new partial-success or retry anomaly appears;
   - per-query caps remain unchanged and cumulative 96-tick projection remains
     within `50 GB`;
   - the accelerated-replay gates (`p95 <=105s`, every
     dispatch-to-terminal interval `<120s`) pass.
7. If the experiment fails any gate, retain serial mode. Do not escalate
   directly to asynchronous/fire-and-forget forecasting in Phase 5R V2.

**Green proves:** Only genuinely independent BigQuery work overlaps, while the
tick still has one atomic, fenced, retry-safe completion boundary.

#### Stage 4 — Deterministic Risk-Adaptive Scoring and Forecast

**What happens:** Reduce recurring BigQuery/TimesFM work outside the operational
risk window while preserving every state transition and checkpoint.

1. Implement a pure `plan_tick_execution()` function returning mode, reason
   codes, current/look-ahead tiers, cohort/control signals, forecast decision,
   and scoring decision.
2. Persist the plan on the tick for audit and retry; the same tick may not
   choose a different policy after restart.
3. Split exact current-feature projection from ML inference.
   `driver_current_features` is refreshed for every published tick with current
   run/tick/snapshot lineage, including `MONITOR` and `RECOVERY`. Projection
   failure records `FEATURE_PROJECTION_FAILED`, retains the pending cursor, and
   may never finalize as `SKIPPED_LOW_RISK`.
   Before any simulation scoring mutation, assert that run epoch, tick
   `simulation_time`, snapshot/current projection timestamps, and current
   demand timestamp match the fixture-owned expected instant. After context
   seeding and immediately before `AI.FORECAST`, assert ten-zone context
   bounds/count and `MAX(interval_start) = @simulation_time`. These assertions
   are simulation-only; the legacy/live branch stays unchanged.
4. `FULL` performs exact-snapshot risk scoring. `MONITOR` and `RECOVERY` may
   skip only inference under the frozen contract and atomically transition
   `SNAPSHOT_READY → SUCCEEDED` with
   `scoring_outcome=SKIPPED_LOW_RISK`, no `SCORED` intermediate state, cleared
   pending cursor, advanced completed/next cursors, zero exact-snapshot
   predictions, and an explicit monitoring-only UI result. A persisted `FULL`
   decision that reaches `SCORE_FAILED` remains `FULL` on retry.
5. Generate TimesFM at the frozen cadence (initial target: once per four ticks)
   and materialize/reuse the remaining horizon with
   `forecast_source_tick_id`, source snapshot, generation time, and age.
   Reused rows receive the current `snapshot_id`/`tick_id` so existing
   exact-current readers can find them, retain the original generation time,
   set `forecast_reused=TRUE`, and expose original source tick/snapshot/age.
   Elapsed forecast points are dropped; reuse is forbidden after horizon expiry.
   `prediction_run_id` is the deterministic current-tick materialization ID;
   `forecast_source_prediction_run_id` is the original AI generation ID.
6. Update `DemandForecast`, repository queries, decision-service inputs, and UI
   source copy to surface reuse lineage/age rather than presenting copied rows
   as newly generated. Readers select the exact current
   run/tick/snapshot/materialization identity and never use lexicographic
   `MAX(prediction_run_id)`.
7. Force refresh when the mode escalates to `FULL`, the horizon expires, the
   previous forecast failed, or the Stage 0 anomaly rule fires.
8. Add enter/exit hysteresis and two-tick pre-warm so scoring is ready before,
   not after, a forecasted `DANGER` transition.
9. Leave `live` scenario behavior unchanged.

**Policy gates:**

- Boundary matrix for `NORMAL`, `CAUTION`, `EXTREME_CAUTION`, `DANGER`, and
  `EXTREME_DANGER`.
- Overnight high humidity remains `MONITOR` or `FULL` when warranted; clock
  hour alone never causes a skip.
- Pending/valid control and active intervention force `FULL`.
- One cool tick cannot exit `FULL`; exit occurs only after the frozen
  hysteresis conditions.
- A reused forecast is always traceable and within its horizon.
- Demand shock triggers the next allowed forced refresh.
- Missing/skipped predictions keep recommendations monitoring-only and never
  fall back to stale current decisions.

**Green proves:** Compute cadence follows operational risk without weakening
state continuity, auditability, or fail-closed behavior.

#### Stage 5 — Provider Proof and Phase 5 Verification

1. Implement and locally prove the replay-clock correction before building a
   new candidate:
   - both in-memory and BigQuery repositories derive the run epoch from the
     loaded scenario fixture, never `self.now()`;
   - persisted run/tick time, engine state time, demand-history cutoff, and
     forecast input cutoff agree exactly;
   - a mismatch fails before forecast dispatch and leaves the tick retryable.
2. Deploy one new immutable image digest with production Scheduler paused and
   unique disposable resources. Parameterize job/Scheduler names; use
   `heatsafe-simulation-tick-${PHASE5R_TAG}` and
   `heatsafe-simulation-replay-2m-${PHASE5R_TAG}` rather than updating the
   legacy hard-coded resource. Record exact environment/IAM/config readback.
3. Run ticks 0–4 manually. Tick 4 must produce ten successful TimesFM zone
   series, exact-current features/predictions, a committed checkpoint, and
   `SUCCEEDED`; retry it once to prove idempotency.
4. Run the codec/store IAM negative suite and bucket lifecycle/readback.
5. Execute at least 20 representative corrected `FULL` ticks including tick 24,
   48, and 95; record component p50/p95/max, checkpoint sizes, BigQuery
   jobs/bytes, fallback count, and clock-alignment assertions.
6. Apply Stage 3P only according to the `<=90s`, `90–105s`, or `>105s`
   decision gate. If tested, persist serial and parallel evidence separately.
7. Execute representative `MONITOR`/`RECOVERY` ticks using a bounded test
   fixture if the 41.1°C scenario cannot naturally exercise them; do not alter
   the authoritative demo fixture to manufacture savings.
8. Run one missing-checkpoint recovery and one corrupt-checkpoint recovery in a
   disposable run; restore/cleanup only the disposable objects.
9. Re-run overlap, score-failure resume, Scheduler OAuth dispatch, pause, and
   terminal no-op gates. OAuth dispatch targets a uniquely named disposable
   Phase-5R job/scheduler/dataset/bucket/run with environment and IAM readback;
   pausing the production Scheduler alone is not isolation.
   The tick-4 retry proof injects failure before scoring finalization, then
   retries the retained pending tick and compares output checksum/lineage; a
   second invocation after success is only the idempotent no-op proof.
10. Run the complete 96-tick replay plus invocation 97 using the selected
   serial/parallel hot path and a disposable Scheduler named for the
   accelerated profile with `*/2 * * * *`. Compare sentinel checksums to the
   oracle and assert zero concurrent active attempts/duplicate logical ticks.
   The harness enforces a cumulative replay byte budget: before each query it
   checks remaining allowance and stops before dispatch when the next bounded
   query could exceed the ceiling.
   `scripts/benchmark_phase5r_runtime.py` must exist and have automated tests
   for FULL-only filtering, nearest-rank p95, byte-budget pre-dispatch stop,
   execution correlation, overlap/duplicate detection, invocation 97, and
   exact-target cleanup before this Hybrid step is executable.
11. Do not enable recurring execution unless corrected `FULL` p95 is `<=105s`,
   every dispatch-to-terminal interval is `<120s`, the `96+1` replay has zero overlap, no
   correctness/IAM/cost gate fails, and the user explicitly confirms.
   Production recurring Scheduler remains paused otherwise.
12. Document, but do not automatically enable, the separately named
   `real-operations` profile at `*/15 * * * *`. Production-readiness requires
   its own operational, security, incident-response, and real-data validation;
   the demo replay alone does not prove that status.
13. Persist an evidence manifest containing image and rollback-compatible
   digests, checkpoint/state mode, checkpoint/policy/codec/generator versions,
   risk-model version, selected TimesFM model/context window, FULL tick indices,
   resource names, Scheduler profile/cadence, cumulative bytes, timing
   aggregation rule, clock-alignment results, Stage 3P decision/evidence, and
   cleanup outcome.

#### Phase 5R Exact Validation Commands

Fully automated:

```bash
venv/bin/python -m unittest discover -s tests -p 'test_simulation_clock.py' -v
venv/bin/python -m unittest discover -s tests -p 'test_simulation_checkpoint.py' -v
venv/bin/python -m unittest discover -s tests -p 'test_simulation_execution_policy.py' -v
venv/bin/python -m unittest discover -s tests -p 'test_simulation_repository.py' -v
venv/bin/python -m unittest discover -s tests -p 'test_simulation_interventions.py' -v
venv/bin/python -m unittest discover -s tests -p 'test_simulation_scoring.py' -v
venv/bin/python -m unittest discover -s tests -p 'test_phase5_deployment_contract.py' -v
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m compileall -q app.py heatsafe infra scripts
venv/bin/python -m pip check
```

Hybrid probes are forbidden during VALIDATE. They require explicit Phase 5R
EXECUTE authority, a pinned image, Scheduler `PAUSED`, a unique disposable
dataset/bucket/run tag, bounded billed bytes/run count, and `finally` cleanup:

```bash
PHASE5R_TAG="$(date -u +%Y%m%d%H%M%S)"

venv/bin/python scripts/profile_phase5r_baseline.py \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --region asia-southeast1 \
  --dataset "heatsafe_phase5r_probe_${PHASE5R_TAG}" \
  --sentinels 0,24,48,95 \
  --oracle-seed-disposable \
  --maximum-total-bytes-billed 5000000000 \
  --execute

venv/bin/python scripts/probe_phase5r_checkpoint.py \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --region asia-southeast1 \
  --dataset "heatsafe_phase5r_probe_${PHASE5R_TAG}" \
  --bucket "${GOOGLE_CLOUD_PROJECT}-heatsafe-phase5r-${PHASE5R_TAG}" \
  --maximum-bytes-billed 350000000 \
  --execute

venv/bin/python scripts/probe_phase5r_timesfm.py \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --dataset "heatsafe_phase5r_probe_${PHASE5R_TAG}" \
  --model 'TimesFM 2.5' \
  --windows 512 1024 2048 \
  --repeats 10 \
  --horizon 16 \
  --quality-days 7 \
  --origins 05:45 10:45 16:45 \
  --min-latency-improvement-pct 20 \
  --max-relative-wape-regression-pct 5 \
  --max-coverage-regression-pp 5 \
  --max-peak-regression-intervals 1 \
  --max-selected-driver-delta-pct 5 \
  --maximum-bytes-billed 250000000 \
  --execute

venv/bin/python scripts/benchmark_phase5r_runtime.py \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --region asia-southeast1 \
  --image "$PINNED_IMAGE" \
  --disposable-job-prefix "heatsafe-phase5r-${PHASE5R_TAG}" \
  --dataset "heatsafe_phase5r_probe_${PHASE5R_TAG}" \
  --bucket "${GOOGLE_CLOUD_PROJECT}-heatsafe-phase5r-${PHASE5R_TAG}" \
  --full-ticks 24,28,32,36,40,44,48,52,56,60,64,68,72,76,80,84,88,92,94,95 \
  --oracle-sentinels 0,24,48,95 \
  --scheduler-cadence '*/2 * * * *' \
  --max-full-p95-seconds 105 \
  --max-tick-seconds 120 \
  --parallel-scoring-policy conditional \
  --parallel-trigger-seconds 90 \
  --min-parallel-improvement-pct 15 \
  --invoke-terminal-noop \
  --maximum-replay-bytes-billed 50000000000 \
  --enforce-cumulative-budget \
  --execute
```

The runtime harness must assert the named ticks were actually persisted as
`execution_mode=FULL`, calculate nearest-rank p50/p95/max, prove checkpoint p95
`<=3s`, corrected `FULL` p95 `<=105s`, every dispatch-to-terminal interval
`<120s`, fixture
epoch agreement, tick-4 ten-zone forecast success, zero overlap, and cumulative
bytes `<=50 GB`. It captures pre/post invocation-97 manifests and restores only
its disposable/run-tagged resources. If Stage 3P is triggered, the harness must
also emit paired serial/parallel samples and reject the parallel candidate
unless complete-tick improvement is at least `15%`.

Agent/UI probe after a bounded `SKIPPED_LOW_RISK` fixture:

1. Record deployed revision/image and current snapshot/tick.
2. Refresh the app on the skipped snapshot.
3. Capture `phase5r_monitoring_only_overview.png` and
   `phase5r_monitoring_only_decision.png`.
4. Verify the UI says monitoring-only and displays forecast source age when
   reused; no previous recommendation is shown as current.
5. Query the exact tick: current feature rows must match its run/tick/snapshot,
   exact-snapshot prediction count must be zero, and
   `scoring_outcome=SKIPPED_LOW_RISK`.

#### Phase 5R Touchpoints

- `heatsafe/simulation/checkpoint.py` — new lossless codec/store contract
- `heatsafe/simulation/repository.py` — checkpoint selection, publication
  metadata, recovery, staging and partition improvements
- `heatsafe/simulation/scoring.py`, `infra/ml_pipeline.py` — explicit skip,
  TimesFM 2.5 pin, validated context window, seed/reuse, anomaly refresh, and
  explanation policy
- `heatsafe/repository.py`, forecast DTOs, decision-service consumers, and
  `app.py` — current-snapshot reused-forecast lookup, source age/provenance, and
  monitoring-only skip behavior
- `heatsafe/simulation/cli.py` — component telemetry and checkpoint
  verify/repair surface
- `heatsafe/config.py`, `.env.example` — checkpoint bucket/format/policy
  configuration
- `infra/provision_gcp.py`, `scripts/deploy_simulation_gcp.sh` — additive schema,
  bucket lifecycle, scoped IAM, readback gates
- `tests/test_simulation_checkpoint.py` — codec, corruption, retry, fallback
- `tests/test_simulation_clock.py` — fixture-owned run epoch, tick timestamp
  boundaries, ledger/engine/forecast alignment, and pre-dispatch drift failure
- `tests/test_simulation_scoring.py`, `tests/test_core.py`,
  `scripts/probe_phase5r_timesfm.py` — TimesFM model/context contract and new
  provider comparison evidence; preserve the Phase 4 probe as immutable prior
  evidence
- `scripts/probe_phase5r_checkpoint.py`,
  `scripts/profile_phase5r_baseline.py`,
  `scripts/benchmark_phase5r_runtime.py` — disposable component baseline,
  checkpoint/atomicity/IAM proof, clock gate, conditional serial/parallel A/B,
  and two-minute 20-FULL/96+1 provider evidence
- existing repository, transition, scoring, contract, CLI, and deployment tests
- Phase 5 research/report/runbook and this authoritative plan

#### Phase 5R Public Contracts

- Existing snapshot, driver, control, and prediction row meanings remain
  compatible.
- `simulation_control_events` is immutable and viewer-only to the runtime.
  Receipt anti-join is the sole pending/consumed boundary; legacy `QUEUED` rows
  are read-compatible but never updated.
- `simulation_ticks` receives only nullable checkpoint/execution provenance
  fields.
- `driver_current_features` remains current for every successful tick even when
  inference is skipped.
- A tick may complete with `scoring_outcome=SKIPPED_LOW_RISK`; consumers must
  treat it as monitoring-only for that exact snapshot.
- Reused demand forecasts are materialized for the current snapshot but expose
  original source tick/snapshot/generation time and age; reuse is never
  represented as newly generated.
- Checkpoint format is a versioned internal persistence contract. Unknown
  versions fail closed and require oracle recovery or an explicit migration.
- `replay_to_tick()` remains available and behaviorally unchanged.

#### Phase 5R Blast Radius

**Risk class:** High for the simulation data plane, medium for user-facing
behavior, low for the `live` scenario. Expected footprint is 8–14 source/test
files plus additive GCP resources and nullable schema fields. No existing table,
raw fixture, public endpoint, model, or legacy scheduler is deleted or replaced.

#### Phase 5R Verification Evidence

| Gate / Scenario | Strategy | Proves SPEC criterion |
|---|---|---|
| Lossless round-trip and next-tick equality at 0/24/48/95 | Fully-Automated | AC-02, AC-05, AC-10 |
| Checkpoint corruption/format/size rejection | Fully-Automated | AC-10, AC-21 |
| GCS create-only, generation/hash readback, IAM negative proof | Hybrid | AC-10, AC-23, AC-28 |
| Upload-before-commit crash and identical-orphan retry | Hybrid | AC-06, AC-10, AC-21 |
| Missing latest checkpoint replays from nearest verified predecessor | Hybrid | AC-05, AC-10 |
| Mid-run queued control and exact expiry boundary after restore | Hybrid | AC-09, AC-27 |
| Immutable control request plus receipt-only pending/applied lifecycle under deployed IAM | Hybrid | AC-09, AC-23, AC-27, AC-28 |
| Parallel staging all-or-nothing plus partition/byte checks | Hybrid | AC-06, AC-21, AC-23 |
| TimesFM seed-once and traceable forecast reuse/refresh | Hybrid | AC-25, AC-26 |
| TimesFM 2.5 `512/1024/2048` repeated latency/quality comparison | Hybrid | AC-23, AC-25, AC-26 |
| Risk-mode boundary, hysteresis, pre-warm, and fail-closed skip tests | Fully-Automated | AC-08, AC-11, AC-16 |
| Skipped tick retains exact current features and atomically advances without SCORED | Fully-Automated | AC-06, AC-08, AC-11 |
| Fixture epoch owns run/tick/engine/forecast time; drift fails before forecast dispatch | Fully-Automated + Hybrid | AC-05, AC-08, AC-10, AC-25 |
| Tick 4 returns ten forecast series and exact-current scoring after clock correction | Hybrid | AC-08, AC-11, AC-25, AC-26 |
| Minimum 20 representative `FULL` provider ticks, p95 `<=105s`, max dispatch-to-terminal `<120s` | Hybrid | AC-14, AC-20, AC-23 |
| Conditional serial/parallel scoring A/B with at least 15% complete-tick improvement and identical lineage | Hybrid | AC-06, AC-08, AC-10, AC-20, AC-23 |
| Production hot-path 96 ticks + invocation 97 + oracle sentinels | Hybrid | AC-02, AC-05, AC-10, AC-12, AC-30 |
| UI never displays stale prediction as current after skip | Agent-Probe | AC-08, AC-11 |
| Pause-first checkpoint-to-oracle/previous-image rollback and cursor checksum smoke | Hybrid | AC-10, AC-21, AC-23 |

#### Phase 5R Test Infra Improvement Notes

- Add a reusable checkpoint-store fake supporting generations, preconditions,
  corruption, missing objects, and orphan enumeration.
- Extend the fake BigQuery client to assert checkpoint metadata is committed in
  the same fenced publication transaction.
- Add a deterministic timing/job-metadata collector injectable in unit tests.
- Add a replay-clock contract helper/fake that asserts fixture epoch equality
  across run rows, precreated ticks, engine state, checkpoints, and scoring
  cutoffs before any provider forecast call.
- Add a bounded provider performance harness that selects sentinel ticks without
  mutating the active evidence run.
- Extend that harness with paired serial/parallel samples, Scheduler
  dispatch-to-terminal timestamps, zero-overlap assertions, and the conditional
  Stage 3P decision rule.
- Add a risk-policy matrix fixture separate from the authoritative 41.1°C demo
  scenario.

#### Phase 5R Resume and Execution Handoff

1. **Selected plan:** this Phase 5R section inside
   `heatsafe-p0-stateful-replay_PLAN_23-07-26.md`.
2. **Last completed step:** Stages 1–4 are code-complete/local green. The
   disposable v7 provider proof reached tick 4 and exposed the fixture-versus-
   wall-clock mismatch; its disposable resources were cleaned up.
3. **Validate status:** Historical Phase 5R validation is `PASS`
   (`0 FAIL / 0 CONCERN / 10 PASS`). The 25-07-2026 V2 amendment remains a
   placeholder and blocks resumed execution until validated.
4. **Context loaded:** Phase 5 research/report/runbook, repository/engine/models/
   randomness/scoring/CLI, BigQuery ML pipeline, config, deploy/provision
   scripts, and current simulation tests.
5. **Fresh-agent next step:** validate V2, then after explicit
   `ENTER PHASE 5R EXECUTE MODE`, implement the replay-clock correction and
   local drift gates. Provider proof restarts from a new disposable candidate
   at ticks 0–4 with Scheduler paused.
6. **RIPER-5 instruction:** this plan-writing turn grants no implementation or
   provider authority. Stage 3P remains conditional on the corrected baseline.

### Phase 6 — Fast Replay, Dynamic UI, End-to-End Proof, and Closeout

**Status:** 🚧 IN PROGRESS
**Dependencies:** Phase 5R Stages 1–4 code complete; Stage 5 bounded provider evidence accepted
**Estimate:** 1–2 days

#### Phase 6 Amendment — 25-07-2026

This amendment supersedes the earlier assumption that the UI remains a
manually refreshed current-snapshot view and that disposable Stage 5 must run a
second full `96+1` before the app-bound rehearsal.

The existing state machine, checkpoint codec, exact-snapshot scoring, control
contracts, and current projections remain unchanged. Phase 6 is a contained
extension with three implementation surfaces:

1. **Fast-run orchestration:** add a bounded command that repeatedly invokes
   the existing one-tick contract without Scheduler wait. `advance_tick()`,
   input freeze, controls, checkpoint, tick ledger, scoring lineage, and
   `zone_snapshots_current` publication remain sequential per tick.
2. **Optional hybrid write batching:** only heavy append-only history rows may
   buffer up to eight ticks. Demand history flushes before every TimesFM
   generation boundary and may never span more than four ticks. This
   optimization is accepted only after canonical-vs-fast checksums and
   mid-batch retry/idempotency tests pass; otherwise retain per-tick writes.
3. **Dynamic UI/read model:** query the existing immutable run/tick history
   (`simulation_ticks`, `zone_operations`, `weather_observations`, forecasts,
   predictions, and audit/control lineage) to reconstruct an exact tick.
   `zone_snapshots_current` remains the latest compatibility projection. Add a
   latest-follow mode and a read-only playback playhead; do not add a new
   history table unless Stage 0 proves the existing tables cannot reconstruct
   the public `ZoneSnapshot` contract exactly.

Stage 5 provider evidence remains the bounded infrastructure/correctness gate.
The one mandatory full `96+1` run moves to Phase 6 and runs against the
app-bound `heatsafe_data` dataset after backup, deployment, and explicit
production-write authorization. This avoids paying the time/cost of one
disposable full-day run followed by the same full-day app rehearsal.

Phase 6 targets:

| Surface | Target |
|---|---:|
| Full app-bound generation | `96+1 <=30 minutes`; stop and reassess if the 32-tick projection misses |
| UI playback | 1, 2, or 5 wall-clock seconds per simulated tick |
| State semantics | Bit-for-bit canonical checksums at ticks 0, 4, 24, 48, 95 |
| Forecast safety | No future-demand read; exact source tick/snapshot lineage |
| Retry safety | No lost/duplicate logical rows after injected chunk failure |
| Scenario isolation | `live` row counts/checksums unchanged |

#### Phase 6 Stage 0 Local Evidence — 25-07-2026

The cheap-local feasibility probe
[`fast_replay_history_batching_FEASIBILITY_25-07-26.md`](fast_replay_history_batching_FEASIBILITY_25-07-26.md)
is `VIABLE` within a deliberately narrow boundary:

- The current repository baseline is 194 tests green in 286.296 seconds; the
  new Phase 6 probe adds three targeted passing tests.
- Eight sequential ticks reconstructed the exact public `ZoneSnapshot`
  contract from existing history with zero mismatches.
- Batch sizes `1`, `4`, and `8` produced identical row counts and SHA-256
  manifests for `order_events` (17,533 rows) and `driver_state_history`
  (49,840 rows).
- Weather, operations, demand, controls, checkpoints, tick ledger, current
  projection, and scoring lineage are explicitly not licensed for batch-8.
- The probe took 34.382 seconds locally, but this includes local simulation and
  manifest hashing and does not prove BigQuery/Cloud Run runtime. The
  `96+1 <=30 minutes` target remains a provider gate.

#### Phase 6 Local Implementation Evidence — 25-07-2026

The first app-bound implementation slice is complete locally without deploying
or mutating provider resources:

- `BigQueryRepository` now lists replay runs, reports bounded progress, and
  reconstructs an exact committed tick from immutable run/tick/snapshot
  history. Mixed lineage, duplicate zones, incomplete ten-zone results,
  invalid run IDs, out-of-range ticks, and non-contiguous UI histories fail
  closed. `zone_snapshots_current` is not used for historical reconstruction.
- A read-only query against the existing `heatsafe_data` run
  `454bffa67d9846d7adfa743b7f35c868` returned its three succeeded ticks and
  reconstructed tick 2 with exactly ten zones. This also confirmed that the
  pre-clock-fix app-bound run ledger can differ from the fixture timestamps;
  exact immutable lineage is therefore the reader key. Direct image inspection
  later confirmed that provider images tagged `phase5r-v12`, `phase5r-v13`,
  and `phase5r-v14` all already use the fixture-owned epoch; the stale app-bound
  run predates that corrected candidate lineage.
- Historical forecasts are scoped to the exact selected
  run/tick/snapshot. The reader tolerates the currently deployed legacy
  forecast schema, where reuse metadata columns have not yet been added,
  without changing that table.
- `fast-replay` reuses the existing one-tick transaction sequentially, requires
  an explicit active `--run-id`, supports an explicit terminal tick and
  runtime limit, initializes the scorer once, stops on no progress or scoring
  failure, and currently accepts only `--batch-size=1`. Batch sizes above one
  remain rejected before mutation.
- The Fast Runner was built on the v14 core: SHA-256 checksums for repository,
  checkpoint, scoring, ML pipeline, scenario, and original one-tick CLI source
  matched the `phase5r-v14` image digest
  `sha256:7c4050e90a1a16cc04aad5c32b9bbed3103ebeaee0e7db81085a9a8e49f1f19e`.
  A pre-lease clock guard now rejects any legacy July-clock run before it can
  mutate tick lease state.
- The Streamlit app now provides run selection, committed progress,
  latest-follow, previous/play/next/latest, a tick slider, and 1/2/5-second
  presentation speed. Historical playback binds decisions and forecasts to
  the selected lineage and disables SafePause, Copilot, and unscoped audit
  details.
- Targeted repository/CLI/app tests passed `19/19`. The full regression suite
  passed `207/207` in `292.630s`; compile, `pip check`, and `git diff --check`
  also passed.
- Live read-only Streamlit interaction checks passed at latest-follow tick 2
  and historical tick 1 with no app exception. No Fast Runner invocation,
  deployment, Scheduler change, BigQuery write, or GCS write was performed.

Remaining provider gates are the immutable-image deployment, backup, bounded
app-bound replay, runtime/cost evidence, dynamic UI capture across the selected
ticks, control/retry proof, and invocation-97 terminal no-op.

#### Phase 6 App-Bound Provider Gate — 25-07-2026

Explicit Phase 6 execute authority was received after the local slice was
committed as `89b5b26`. The provider run preserved the stop/reassess boundary:

- The pre-replay backup is stored in
  `cohort2track2.heatsafe_phase6_backup` under tag `20260725113518` with a
  45-day default expiration. Source and backup row counts plus canonical
  checksums matched for 10 heatwave current-zone rows, 3,875 current-driver
  rows, one scenario lock, and one active-run row.
- Cloud Build `f582d9ed-6264-4fe3-8781-48e47e383f45` produced immutable image
  `sha256:d1f9af7c60804495184e786cd2720942832e792f365960127cdd495eccf49699`.
  The app is on revision `heatsafe-ops-00008-b22`, and the bounded job is
  `heatsafe-simulation-fast-replay-20260725113518`; neither deployment created
  or enabled a Scheduler.
- The first `start` attempt failed before creating a run because the shared
  app-bound `simulation_runs` table still had the legacy schema. The deployer
  then applied the additive `--schema-only-current` migration; runtime code did
  not provision infrastructure. The first tick attempt stopped before
  publication because the configured checkpoint bucket did not exist. The
  deployer created the regional, uniform-access, public-access-prevented
  `cohort2track2-heatsafe-sim-checkpoints` bucket with a 35-day lifecycle and
  runtime object-creator/viewer access. The failed lease was expired only after
  asserting its exact run, tick, token, status, and missing checkpoint object.
- Legacy run `454bffa67d9846d7adfa743b7f35c868` was backed up and transitioned
  from `RUNNING` to `FAILED` under an exact-row assertion. Corrected app-bound
  run `36a173c5a2d44e3a8f4da4eefae8709c` starts at
  `2026-05-26 00:00:00+07:00`. Ticks 0–3 committed and scored sequentially;
  tick 4 remained `PENDING`, there was no pending score, and the run was
  explicitly returned to `PAUSED`.
- Tick totals were 109.366s, 67.596s, and 73.176s for ticks 0–2. The dominant
  measured components were `publication_commit` at 18.719–56.738s and
  `score_finalize` at 27.534–38.620s. This projects far beyond the Phase 6
  `96+1 <=30 minutes` target, so the execution was cancelled at the bounded
  runtime gate rather than continuing to consume shared resources. No
  invocation-97 claim is made.
- Corrected tick-2 forecasts span `2026-05-26 00:45–04:00+07:00`; the old July
  forecast-axis mismatch is absent for the new lineage. A read-only decision
  replay at the same Hoàn Kiếm tick and UI controls still returned
  `NO_FEASIBLE` for 19 eligible drivers with the same upper-demand ETA and
  fulfillment conflicts. The prior clock mismatch was therefore not the direct
  cause of that safety outcome.
- All HeatSafe Scheduler resources remained `PAUSED`, the backup was retained,
  and the deployed service continued to return a healthy response.

The next implementation slice must reduce or amortize the measured
`publication_commit` and `score_finalize` costs without weakening per-tick
lineage, score barriers, checkpoint fencing, or fail-closed decisions. Resume
the paused run only after a fresh runtime projection passes the 30-minute gate.

#### Stage 0: Pre-Phase Research

1. Review all prior phase evidence and unresolved test-infra notes.
2. Prove whether scoring/control output changes the next simulation state and
   freeze the per-tick versus batchable durability matrix.
3. Verify that the existing historical tables can reconstruct all
   `ZoneSnapshot` fields with one run/tick/snapshot lineage.
4. Benchmark canonical ticks versus a back-to-back runner and candidate heavy
   history batch sizes `1`, `4`, and `8`; compare checksums and row multisets.
5. Create a dedicated evidence/backup dataset with default expiration longer than the evidence window. Before replay, copy exact heatwave rows from every overwritten current table (`zone_snapshots_current`, `driver_current_features`, and the coordinator/current-run rows) into run-tagged backup tables and store row counts plus canonical checksums.
6. Confirm dynamic UI proof steps and expected changes at selected times.
7. Stop if the 32-tick runtime projection exceeds the 30-minute full-run
   target, canonical checksums diverge, or any prior required gate is red.

#### Implementation and Validation

1. Add repository queries for run list/progress, latest committed tick, and
   exact tick reconstruction from existing history. Reject mixed or incomplete
   run/tick/snapshot results.
2. Add a bounded `fast-replay` command with explicit run, terminal tick,
   batch-size, byte/runtime circuit breakers, and resume behavior. Keep the
   existing single-tick command unchanged.
3. If Stage 0 approves hybrid batching, batch only the named heavy append-only
   histories. Keep checkpoint, tick ledger, current snapshot, controls, and
   forecast barriers per the amendment contract.
4. Replace manual-only refresh with Streamlit latest-follow and read-only
   playback controls: play/pause, previous/next, tick slider, and 1/2/5-second
   playback speed. Historical ticks may not queue a control.
5. Pin and deploy the app plus fast-run Job from one immutable image digest.
6. Pause Scheduler and execute one mandatory app-bound production-path replay of all 96 ticks, followed by a recorded 97th no-op invocation.
7. Validate hourly demand/supply/exposure shapes, state invariants, and checksums.
8. Exercise one no-action control interval and one SafePause interval.
9. Simulate scoring failure and successful retry.
10. Capture dynamic UI evidence at three distinct ticks:
   - early/low heat,
   - heat/demand escalation,
   - post-intervention.
11. Run full tests, compile, and dependency checks.
12. Update README architecture, runbook, commands, data provenance, and disclaimers.
13. Validate logs with a versioned JSON-schema checker/query: every Cloud Run execution has execution/run/tick/snapshot IDs, lease outcome, row counts, checksum, `duration_ms`, scoring/invariant result, bounded redacted error fields, and exactly one terminal outcome.
14. Record known gaps; do not relabel them as P0 completion.
15. Cleanup in order: keep Scheduler paused, wait for executions, preserve the evidence manifest, and use one bounded transaction to delete only heatwave/current coordinator rows then restore them from the run-tagged backups when rollback is selected. Recompute pre/post row counts and canonical checksums; abort and retain backups on mismatch. Do not use `--seed-demo` as rollback because it also mutates demand history and GCS.

#### Test Procedure

```bash
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m compileall -q app.py heatsafe infra
venv/bin/python -m pip check
```

Manual UI:

1. Open the deployed HeatSafe service with `heatwave` selected.
2. Select the active run and enable latest-follow while fast replay advances.
3. Pause playback, move between ticks 0, 24, 48, and 95, then resume at
   1/2/5-second speed.
4. Verify changed weather, demand, supply, exposure, risk, and exact lineage.
5. Confirm historical ticks cannot queue a trusted control.
6. Authenticate to the current-tick operator path, queue a trusted simulated
   SafePause, and advance through its lifecycle.
7. Verify audit, driver state, zone capacity, and risk-input changes.
8. Confirm no copy implies a medical diagnosis or real dispatch.

#### Final Verification Queries

```sql
SELECT COUNT(*) AS tick_rows,
       COUNT(DISTINCT tick_index) AS tick_indices,
       MIN(tick_index) AS first_index,
       MAX(tick_index) AS last_index,
       MIN(simulation_time) AS first_tick,
       MAX(simulation_time) AS last_tick
FROM `<project>.<dataset>.simulation_ticks`
WHERE simulation_run_id = @run_id AND status = 'SUCCEEDED';
```

```sql
WITH ordered AS (
  SELECT tick_index, simulation_time,
         TIMESTAMP_DIFF(
           simulation_time,
           LAG(simulation_time) OVER (ORDER BY tick_index),
           MINUTE
         ) AS gap_minutes
  FROM `<project>.<dataset>.simulation_ticks`
  WHERE simulation_run_id = @run_id AND status = 'SUCCEEDED'
)
SELECT *
FROM ordered
WHERE tick_index NOT BETWEEN 0 AND 95
   OR (tick_index > 0 AND gap_minutes != 15);
```

Expected: zero rows; the aggregate query returns one summary row with `tick_rows=96`, `tick_indices=96`, `first_index=0`, and `last_index=95`.

```sql
SELECT tick_id,
       COUNTIF(
         active_drivers != fresh_drivers + exposed_2h
         OR exposed_4h < 0
         OR exposed_4h > exposed_2h
       ) AS violations,
       COUNT(DISTINCT snapshot_id) AS snapshot_ids,
       COUNT(DISTINCT observed_at) AS observed_times
FROM `<project>.<dataset>.zone_operations`
WHERE simulation_run_id = @run_id
GROUP BY tick_id
HAVING violations > 0 OR snapshot_ids != 1 OR observed_times != 1;
```

Expected: zero rows. Store this exact query and result in the phase report.

The run must be `COMPLETED` with `last_published_tick_index=95`, `last_completed_tick_index=95`, and no pending score. Before and after invocation 97, store and compare a manifest containing run cursor/status, all tick/event/history/prediction/forecast counts and checksums, current driver/features/snapshot counts and checksums, control consumption statuses, and remaining staging tables. Every value must remain unchanged and staging count must be zero.

UI artifacts are:

```text
phase6_tick-00_early.png
phase6_tick-48_peak.png
phase6_tick-N_post-safepause.png
```

The evidence table beside them records service revision/URL, capture time, run/tick/snapshot IDs, zone, temperature, demand, active/online supply, cumulative exposure cohorts, risk, and intervention state.
Each screenshot row references a saved BigQuery result artifact and Cloud Logging execution query. A deployed negative-auth record proves the public principal cannot invoke the control job or create a control row; a trusted-operator positive record proves one valid row is created with matching Cloud Audit Log execution.

#### Done Criteria

- Required automated, integration, data, error, and manual gates are green.
- User confirms the visible demo behavior.
- Scheduler can be paused and replay state preserved.
- README and closeout evidence match the deployed behavior.
- P0 is marked ✅ VERIFIED only after user confirmation.

## 10. Implementation Checklist

### Contract and Fixtures

- [ ] Add disabled-safe simulator settings and validation.
- [ ] Add eight new BigQuery tables, including the coordinator, immutable trusted control queue, and consumption receipts.
- [ ] Add nullable fields to existing weather/operations/driver tables.
- [ ] Add partitioning and clustering definitions.
- [ ] Add a source-attributed versioned Hanoi scenario manifest.
- [ ] Add and validate a bounded 15-minute weather fixture.
- [ ] Add schema/fixture unit tests.

### Engine

- [x] Add deterministic entity/event RNG and checksum utilities.
- [x] Add simulation clock and run/tick models.
- [x] Add driver state machine and initial fleet generator.
- [x] Add demand generator and order lifecycle.
- [x] Add heat exposure/rest/hydration/economics transitions.
- [x] Add intervention transitions.
- [x] Add zone aggregation and invariant validation.
- [x] Add same-seed/different-seed/full-day tests.

### Persistence

- [x] Add start/tick/status/pause/resume CLI.
- [x] Add local/fake-client run ownership and tick lease handling.
- [x] Add local/fake-client coordinator/tick precreation and fencing-token validation.
- [x] Add deterministic row projection and idempotent `SNAPSHOT_READY` retry.
- [x] Add BigQuery transaction SQL shape for current-state and snapshot projection.
- [x] Add fake-client retry/concurrency/failure tests.
- [ ] Run isolated disposable-dataset Hybrid probe for actual lease fencing,
  transaction rollback, staging expiry, byte caps, and cross-process persistence.

### Scoring and Closed Loop

- [ ] Add simulation feature source to `score_snapshot`.
- [ ] Materialize current features from persistent state.
- [ ] Preserve exact-snapshot prediction guarantees.
- [ ] Keep public approvals audit-only; write immutable trusted controls through the authenticated job and consume them via separate idempotent receipts.
- [ ] Apply intervention effects over later ticks.
- [ ] Add selected/control/mismatch/failure tests.

### Automation and Proof

- [ ] Add simulation Cloud Run Job deployment.
- [ ] Add opt-in authenticated Scheduler creation.
- [ ] Add pause/resume/force-run/disable runbook.
- [ ] Instrument per-component tick duration and BigQuery job/byte evidence.
- [ ] Add a versioned lossless checkpoint codec and GCS checkpoint store.
- [ ] Commit immutable checkpoint metadata in the fenced tick transaction.
- [ ] Switch the production hot path to previous-checkpoint plus one
  `advance_tick()`; retain oracle and nearest-checkpoint fallback.
- [ ] Remove redundant staging expiry API calls, test bounded parallel staging,
  and add history partition predicates.
- [ ] Seed TimesFM context once per run and add traceable forecast reuse/refresh.
- [ ] Add deterministic `FULL`/`MONITOR`/`RECOVERY` execution policy and
  monitoring-only skip semantics.
- [ ] Make the scenario fixture timestamp the single replay epoch for run,
  tick, engine, demand-history, and forecast inputs; fail closed on drift.
- [ ] Run conditional serial/parallel forecast-versus-feature/ML A/B only when
  corrected `FULL` p95 is above `90s`; retain serial unless improvement is at
  least `15%` with identical outputs and lineage.
- [ ] Prove representative accelerated-replay `FULL` tick p95 `<=105s`, every
  dispatch-to-terminal interval `<120s`, and zero overlap before Scheduler
  enablement.
- [ ] Run full test/compile/dependency gates.
- [x] Add exact-tick history queries and mixed-lineage rejection.
- [x] Add the bounded back-to-back fast-replay command.
- [ ] Apply batch-8 only to Stage-0-approved heavy append-only rows; retain
  per-tick fallback.
- [x] Add latest-follow and read-only UI playback controls.
- [ ] Run the one mandatory app-bound full 96-tick replay and invocation-97
  no-op gate in `heatsafe_data`.
- [ ] Complete dynamic UI and BigQuery evidence.
- [ ] Update README and phase evidence.
- [ ] Obtain user confirmation.

## 11. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Existing rows cannot accept new required fields | Provisioning failure | Add fields as nullable; require strict values only for simulation rows |
| Concurrent scheduler executions | Duplicate/corrupt tick | Deterministic tick key, lease, event IDs, and transactional publication |
| BigQuery partial write | Mixed snapshot | Stage events; publish current state/snapshot and tick marker transactionally |
| Score fails after snapshot publish | UI temporarily lacks recommendation | Preserve coherent snapshot and fail closed; retry same score step |
| Synthetic model appears scientifically validated | Misleading demo | Keep `is_simulated`, provenance, disclaimer, and no medical claims |
| Demand/supply appears noisy or scripted | Low demo credibility | Shared latent shocks, autocorrelation, bounded transitions, distribution checks |
| SafePause changes aggregate only | No causal story | Persist per-driver lifecycle and compare selected versus control histories |
| Scheduler creates unbounded cost | Operational cost | Opt-in flag, measured cadence gate, explicit byte ceilings, terminal alert, five-minute pause/delete SLA, and no-op after completion |
| Public approval mutates simulator | Unauthorized state/cost change | Public identity is read-only; audit remains non-authoritative; only authenticated exact-lineage controls are consumed |
| Lease expires during publication | Late writer corrupts current state | Lease TTL exceeds timeout plus margin and fencing token is revalidated inside publication transaction |
| Run ledger follows wall clock while engine follows fixture | Empty/wrong historical forecast and tick failure | Derive the simulation epoch from the fixture for both repositories; assert ledger/engine/demand/forecast agreement before dispatch; live branch remains wall-clock |
| Ignored CSVs become accidental inputs | Unreproducible deployment | Exclude them explicitly; use checked-in versioned scenario fixture |
| Existing live scenario regresses | Demo outage | Feature-source switch and regression tests; no live schema rewrite |
| Lossy checkpoint changes later state | Determinism/audit failure | Versioned explicit codec with lossless float representation; round-trip state equality and next-tick oracle checks |
| Checkpoint object and BigQuery cursor diverge | Wrong predecessor or corrupt recovery | Immutable generation/hash metadata committed in the fenced publication transaction; ignore uncommitted objects |
| Control/expiry changes after orphan upload | Retry computes different bytes for one create-only object | Freeze predecessor/control/model/policy manifest before upload and include its checksum in the object name |
| New control is missed after restore | SafePause does not affect the intended next state | Restore first, then load authoritative controls; boundary tests for queued/consumed/expired controls |
| Runtime mutates immutable control with viewer-only IAM | Live control tick fails authorization | Remove request-row UPDATE; derive pending/applied state exclusively from immutable request plus receipt anti-join |
| Risk-adaptive mode hides a slow critical path | Scheduler appears green but fails during daytime danger | Measure SLO on at least 20 representative `FULL` ticks including late replay sentinels |
| Forecast reuse becomes stale during a shock | Capacity recommendation uses obsolete demand | Source lineage/age plus forced refresh on mode escalation, horizon expiry, failure, or validated anomaly |
| GCS latency/cost erodes the gain | Tick remains above cadence | Dedicated regional bucket, compressed bounded payload, component p95/size gates at 0/24/48/95 |
| Parallel staging overloads quotas or hides failure | Partial inputs or transient load failures | Bounded pool, wait for every load, aggregate errors, and enter transaction only when all required stages succeed |
| Parallel scoring finalizes one successful branch while the other failed | Incomplete or mismatched forecast/prediction state | Two independent clients, join both outcomes, one fenced final transaction, deterministic retry IDs, and serial fallback |
| Checkpoint rollout regresses or cannot be recovered quickly | Scheduler remains blocked or cursor is stranded | `oracle|checkpoint` kill switch, pinned previous image, pause-first rollback, manual checksum/cursor smoke before resume |

## 12. Security and Safety Posture

- Every environment-derived project/dataset/bucket/table/model/scenario identifier is validated against an explicit format/allowlist before SQL/resource interpolation.
- All BigQuery values use query parameters or validated enums.
- Scenario paths are selected from an allowlist; no arbitrary filesystem paths.
- Scheduler uses OAuth to a Google API endpoint and a dedicated caller identity.
- Public reader, trusted operator/control writer, simulator, scorer/trainer, deployer, and Scheduler caller use separate identities.
- Simulator service account receives only required dataset-scoped BigQuery, optional bucket-scoped object-read, Logging, and job permissions.
- Existing public UI is read-only/approval-disabled by default and cannot create trusted controls, enable schedulers, or dispatch.
- Trusted controls require exact proposal/run/tick/snapshot lineage, TTL, deterministic ID, atomic consumption, and fan-out caps.
- All driver IDs are synthetic hashes.
- Event payloads exclude secrets and raw provider credentials.
- Structured error messages are bounded before persistence.
- Production dispatch remains `NOT_APPLICABLE`.

## Touchpoints

### New Files

```text
data/scenarios/hanoi_heatwave_v1/manifest.json
data/scenarios/hanoi_heatwave_v1/weather_15m.csv
heatsafe/simulation/__init__.py
heatsafe/simulation/models.py
heatsafe/simulation/randomness.py
heatsafe/simulation/scenario.py
heatsafe/simulation/demand.py
heatsafe/simulation/transitions.py
heatsafe/simulation/engine.py
heatsafe/simulation/repository.py
heatsafe/simulation/cli.py
scripts/deploy_simulation.sh
tests/test_simulation_full_replay.py
tests/test_simulation_contract.py
tests/test_simulation_randomness.py
tests/test_simulation_demand.py
tests/test_simulation_transitions.py
tests/test_simulation_invariants.py
tests/test_simulation_repository.py
tests/test_simulation_cli.py
tests/test_simulation_scoring.py
tests/test_simulation_interventions.py
```

The executor may consolidate test modules when that preserves the stated scenario coverage; any consolidation must be recorded before implementation.

### Modified Files

```text
heatsafe/config.py
heatsafe/bigquery_io.py
heatsafe/audit.py
heatsafe/models.py
heatsafe/ai_decision.py
heatsafe/safepause.py
heatsafe/repository.py
infra/provision_gcp.py
infra/ml_pipeline.py
scripts/deploy_gcp.sh
.env.example
README.md
```

### Read/Compatibility Surfaces

```text
app.py
data/demo_snapshot.json
heatsafe/services/decision_service.py
tests/test_app.py
tests/test_core.py
tests/test_refinement.py
Dockerfile
requirements.txt
```

## Public Contracts

1. **`ZoneSnapshot` compatibility:** existing fields and scenario/snapshot behavior remain valid.
2. **Snapshot coherence:** all current heatwave zones share one `snapshot_id`.
3. **Prediction coherence:** predictions must match the active `snapshot_id`; no stale fallback.
4. **Audit semantics:** actions remain `SIMULATED` with `dispatch_status=NOT_APPLICABLE`.
5. **Exposure semantics:** `exposed_2h` remains cumulative `>=120` and `exposed_4h` remains its `>=240` subset; the optional `exposed_2_to_4h` is exclusive.
6. **CLI contract:**

   ```text
   venv/bin/python -m heatsafe.simulation.cli validate-scenario
   venv/bin/python -m heatsafe.simulation.cli start
   venv/bin/python -m heatsafe.simulation.cli tick
   venv/bin/python -m heatsafe.simulation.cli status
   venv/bin/python -m heatsafe.simulation.cli pause
   venv/bin/python -m heatsafe.simulation.cli resume
   venv/bin/python -m heatsafe.simulation.cli queue-control [exact lineage args]
   ```

   `queue-control` is packaged in the same CLI but is authorized for cloud use only through the `heatsafe-simulation-control` Job identity; it is not a public/local anonymous write path.

7. **Configuration contract:**

   ```text
   HEATSAFE_SIMULATION_ENABLED
   HEATSAFE_SIMULATION_SCENARIO_VERSION
   HEATSAFE_SIMULATION_SEED
   HEATSAFE_SIMULATION_TICK_MINUTES
   HEATSAFE_SIMULATION_LEASE_SECONDS
   HEATSAFE_SIMULATION_GENERATOR_VERSION
   ```

8. **Deployment contract:** scheduler creation requires an explicit opt-in flag and can be paused independently.
9. **Provenance contract:** every simulation-produced state/event carries run, applicable tick/source-tick, generator version, and simulated status; coordinator/run metadata follow their explicit schema.
10. **Medical-safety contract:** model output remains operational decision support, not diagnosis or proven health impact.
11. **Control-authority contract:** public audit approval is evidence only; only the authenticated trusted-control path can influence simulator state.
12. **Replay-time contract:** heatwave forecasting and scoring are anchored to the active run/tick/snapshot/simulation time; live remains wall-clock based.

## Blast Radius

**Risk class:** Medium–High for the demo data plane and external abuse surface. `SIMULATED` limits real-world dispatch impact but does not make unauthorized data/cost mutation low risk.

| Surface | Change | Risk |
|---|---|---|
| BigQuery schema | Eight new tables and nullable extensions | Migration/partition/query errors |
| Heatwave current snapshot | New stateful writer | Mixed or stale snapshot if publication is wrong |
| Driver scoring | New simulation feature source | Feature mismatch or missing predictions |
| Intervention control | Authenticated exact-lineage control queue | Replay, expiry, cap, or wrong-driver targeting |
| Cloud deployment | New job and optional schedule | Recurring cost/concurrent runs |
| UI/repository | No intended interface rewrite | Regression only if projected semantics drift |
| Live scenario | Intended unchanged | Must be covered by regression suite |

Expected implementation footprint: approximately 15–25 source/test files plus one compact scenario fixture. No destructive table replacement, no production driver command surface, and no new public HTTP API.

## Verification Evidence

| Gate / Scenario | Strategy | Proves SPEC criterion |
|---|---|---|
| Scenario manifest and weather fixture validation | Fully-Automated | AC-15, AC-18 |
| Additive schema test and disposable dataset provisioning | Hybrid | AC-13, AC-18 |
| Same-seed canonical replay equality | Fully-Automated | AC-02, AC-05, AC-10 |
| Per-entity randomness remains stable under reordered iteration | Fully-Automated | AC-05, AC-10 |
| Driver/order state transition matrix | Fully-Automated | AC-03, AC-04 |
| Exposure bucket boundary and partition invariants | Fully-Automated | AC-07, AC-16 |
| Demand/order aggregation reconciliation | Fully-Automated | AC-17 |
| Two real BigQuery ticks plus retry | Hybrid | AC-06, AC-10, AC-21 |
| Mixed-snapshot query returns zero violations | Hybrid | AC-06 |
| Simulation scoring references exact snapshot | Hybrid | AC-08, AC-11 |
| TimesFM historical replay-clock disposable probe | Hybrid | AC-25, AC-26 |
| Approval produces selected-driver lifecycle only | Hybrid | AC-09 |
| Missing model/mismatched prediction remains monitoring-only | Fully-Automated | AC-11 |
| Live and snapshot-mode regression suite | Fully-Automated | AC-13 |
| Scheduler OAuth invocation, IAM read-back, and concurrent dispatch | Hybrid | AC-14, AC-21, AC-23, AC-28 |
| Checkpoint lossless round-trip and oracle next-tick equality at 0/24/48/95 | Fully-Automated | AC-02, AC-05, AC-10 |
| Checkpoint upload/commit crash, corruption, and nearest-predecessor fallback | Hybrid | AC-06, AC-10, AC-21 |
| Risk-mode boundaries, hysteresis, pre-warm, skip fail-closed behavior | Fully-Automated | AC-08, AC-11, AC-16 |
| TimesFM seed-once plus forecast source-lineage and anomaly refresh | Hybrid | AC-25, AC-26 |
| Minimum 20 representative FULL ticks with p95 at or below 105 seconds and max dispatch-to-terminal below 120 seconds | Hybrid | AC-14, AC-20, AC-23 |
| Streamlit refresh across three ticks | Agent-Probe | AC-05, AC-06, AC-08 |
| Mandatory 96-tick replay plus invocation-97 no-op | Hybrid | AC-12, AC-15, AC-16, AC-30 |
| Versioned structured-log schema and one-terminal-outcome query | Hybrid | AC-20, AC-22, AC-29 |
| Pre-replay backup and targeted restore checksum equality | Hybrid | AC-23, AC-30 |
| Disclaimer/provenance UI and row inspection | Agent-Probe | AC-18, AC-19 |

## Test Infra Improvement Notes

Phase 5R planning identified these required improvements:

- checkpoint-store fake with object generations, write preconditions, corruption,
  missing-object, and orphan scenarios;
- fake BigQuery transaction assertions for atomic checkpoint metadata;
- injectable deterministic component timer and BigQuery job-metadata collector;
- provider performance harness for representative early/middle/late `FULL`
  ticks without mutating the active evidence run;
- risk-mode matrix fixture independent from the authoritative 41.1°C scenario.

Retain the existing need for a disposable dataset fixture, golden replay
checksums, Scheduler integration probe, and UI runtime evidence.

## 13. Ops Runbook Requirements

The implementation must document:

- Validate scenario without writes.
- Start a new run and inspect its config.
- Advance one tick manually.
- Inspect current run/tick/snapshot status.
- Pause and resume logical run state.
- Pause and resume Cloud Scheduler.
- Force-run Scheduler once.
- Retry `SCORE_FAILED` without advancing simulation time.
- Restore overwritten current heatwave/coordinator rows from the run-tagged backup transaction and prove pre/post checksums. Document `--seed-demo` separately as a broader reseed that also mutates demand history/GCS.
- Disable/delete the scheduler without deleting BigQuery evidence.
- Determine whether a failure occurred before or after snapshot publication.
- Query latest successful checksum and exact prediction run.

## 14. Change Management

If execution discovers a material change, stop and update this plan before coding further.

Material changes include:

- Replacing BigQuery with another state store.
- Changing the 15-minute published tick.
- Changing public exposure bucket semantics.
- Adding physiological telemetry to the decision model.
- Adding real dispatch, Pub/Sub, Dataflow, or Airflow.
- Training the model from replay output.
- Making scheduler deployment default-on.
- Changing the `live` scenario behavior.

For each change record:

1. Proposed modification.
2. Reason and evidence.
3. Affected acceptance criteria.
4. Schema/API compatibility impact.
5. New verification gates.
6. User approval.

## 15. Future Work

- Import/calibrate distributions from Grab-Posisi or LaDe.
- OSM route-level movement and shade/radiation exposure.
- UTCI-derived decision features after validation.
- Wearable signals with signal-quality metadata.
- Pub/Sub event transport and Dataflow streaming.
- Airflow/Composer only if multi-source backfills/training DAGs justify it.
- Multiple cities/scenario catalogs.
- Real outcome collection and model retraining governance.

## Resume and Execution Handoff

1. **Selected plan file path:**
   `process/general-plans/active/heatsafe-p0-stateful-replay_23-07-26/heatsafe-p0-stateful-replay_PLAN_23-07-26.md`
2. **Last completed phase or step:**
   Original Phases 1–4 are user-confirmed `✅ VERIFIED`. Phase 5R Stages 1–4
   are code-complete and locally green (168 tests plus compile/dependency/
   syntax/diff checks), but deployed verification is incomplete. Disposable v7
   passed ticks 0–3 and exposed a replay-clock correctness bug at tick 4:
   ledger/tick time used July wall clock while engine/demand used the
   26-05-2026 fixture. Candidate cloud resources were cleaned up and production
   Scheduler remains `PAUSED`.
3. **Validate-contract status:**
   The original plan validation passed. The Phase 5R amendment also passed deep
   VALIDATE on 24-07-2026 with `0 FAIL / 0 CONCERN / 10 PASS`. The V2
   replay-clock/cadence/conditional-parallel amendment dated 25-07-2026 is
   plan-written but its Validate Contract is still a placeholder; EXECUTE may
   not resume until that amendment is validated.
4. **Supporting context files loaded:**
   - Phase 5 research, report, and runbook artefacts
   - `phase5r_latency_remediation_RESEARCH_24-07-26.md`
   - `heatsafe/config.py`
   - `heatsafe/simulation/engine.py`
   - `heatsafe/simulation/models.py`
   - `heatsafe/simulation/randomness.py`
   - `heatsafe/simulation/repository.py`
   - `heatsafe/simulation/scoring.py`
   - `heatsafe/simulation/cli.py`
   - `infra/provision_gcp.py`
   - `infra/ml_pipeline.py`
   - `scripts/deploy_simulation_gcp.sh`
   - current `tests/test_simulation_*.py` suite
   - `.env.example`
   - `requirements.txt`
   - `process/context/all-context.md` — absent
   - `process/context/tests/all-tests.md` — absent
5. **Fresh-agent next step:**
   Validate the V2 amendment, then—only with explicit Phase 5R EXECUTE
   authority—implement the replay-clock correction, run its local gates, and
   stop before provider mutation if any clock assertion is not exact. When
   green, deploy a new disposable immutable candidate with Scheduler paused and
   follow the Stage 5 sequence starting at ticks 0–4.
6. **Working-tree note:**
   Preserve all user changes. Phase 5R source/tests/scripts and this plan are
   already modified/untracked in the working tree; do not discard, overwrite,
   rebase, or mix remote synchronization into the remediation.
7. **Execution authority:**
   This plan-writing turn authorizes no source/provider/Scheduler action.
   Recurring execution remains a separate explicit decision even after all
   automated and Hybrid gates pass.

## Validate Contract

### Phase 5R V2 amendment validation

**Validation date:** 25-07-2026
**Net Gate:** **CONDITIONAL** for local implementation; provider mutation
remains blocked until the named local/infrastructure prerequisites below pass.
**Authorization:** User entered `EXECUTE` mode. ADAS Level 2 authorization
permits one local agent with no network, worktree, irreversible action,
production mutation, or secret operation.

#### V2 validation synthesis

| Layer 1 dimension | Status |
|---|---|
| Infra/setup fit | CONCERN — architecture fits, deploy script still hard-codes one-minute/shared names |
| Test coverage | CONCERN — clock and benchmark harness tests are missing |
| Breaking changes | CONCERN — require scenario/fixture identity and explicit Scheduler naming |
| Security surface | CONCERN — frozen control time, scoring fence, and disposable cleanup need hard gates |

| Layer 2 section | Status |
|---|---|
| Replay-clock ownership | PASS |
| Local drift/checkpoint/scoring proof | CONCERN — mechanically local and fixable |
| Stage 3P parallel scoring | CONCERN / V2-PROBE REQUIRED |
| Stage 5 provider proof | CONCERN / V2-PROBE REQUIRED |

**Totals: 0 FAILs / 7 CONCERNs / 1 PASS**

**→ Phase 5R V2 Net Gate: CONDITIONAL**

The concerns are not waived. Fixable clock/test gaps are now plan requirements;
the provider-dependent concerns are hard stops and feasibility probes. Local
execution may begin at the clock correction. No GCP action may begin merely
because the local gate is green.

#### V2 plan fixes applied during validation

| # | Finding | Locked remediation |
|---|---|---|
| V2-P1 | Repository ledger uses wall clock while engine uses fixture | Central fixture-epoch resolver; reject scenario/manifest mismatch; preserve wall clock for leases/audit/auth |
| V2-P2 | Restored checkpoint can carry an incompatible clock | Validate scenario version, seed, fixture epoch, and exact predecessor minute index before accepting it |
| V2-P3 | Clock drift could reach scoring after deterministic writes | Simulation-only immutable lineage/time assertion first; context bound/count assertion immediately before `AI.FORECAST` |
| V2-P4 | Control validity can change between freeze and commit | All tick authorization uses persisted `input_frozen_at`; later wall clock only fences the lease |
| V2-P5 | Tick-4 “retry after success” was not a real retry | Inject pre-finalization failure, retry the retained pending tick, then run a separate post-success no-op |
| V2-P6 | Stage 3P sample/attempt contract was underspecified | Minimum ten paired attempts/mode, counterbalanced separate runs, durable attempt fencing before parallel implementation |
| V2-P7 | Deploy code can update shared one-minute resources | Parameterize exact tagged tick/Scheduler names and add cleanup guards before any provider proof |
| V2-P8 | Planned runtime harness does not exist | Implement/test FULL filtering, p95, pre-dispatch bytes, execution correlation, overlap, `97`, and exact cleanup before scheduled replay |

#### V2 execute-agent instructions and hard stops

| # | Instruction | Trigger |
|---|---|---|
| V2-E1 | Change only simulation epoch ownership; do not replace lease/audit/authorization `self.now()` calls with fixture time. | Clock implementation |
| V2-E2 | Require a real `tests/test_simulation_clock.py` with non-zero tests; zero-test discovery is failure. | Local verification |
| V2-E3 | Keep legacy/live scoring SQL unchanged and prove the new time assertions precede simulation mutations/forecast dispatch. | Scoring guard |
| V2-E4 | Correct frozen-control authorization before provider execution; boundary-test expiry at, before, and after `input_frozen_at`. | Pre-provider |
| V2-E5 | Do not run Stage 3P until corrected serial p95 triggers it and a durable scoring-attempt/fencing design is implemented locally. | Stage 3P |
| V2-E6 | Do not create/update any Scheduler until deploy names/cadence are parameterized, tests reject legacy/shared targets, and exact-target cleanup is proven locally. | Stage 5 deploy |
| V2-E7 | Do not start the 20-FULL or `96+1` probe until the benchmark harness and its deterministic unit tests are green. | Stage 5 Hybrid |
| V2-E8 | Keep production Scheduler `PAUSED`; recurring enablement still requires complete evidence and a separate explicit confirmation. | All stages |

#### V2 scenario and high-risk prediction review

**Scenario dimensions:** timing, state transitions, environment/timezone, error
cascades, authorization, data integrity, integration, and business logic.

Critical/high cases locked into tests or hard stops:

- caller scenario does not match fixture manifest;
- BigQuery UTC normalization represents the same instant but a different
  timezone object;
- checkpoint scenario/seed/epoch/minute differs from the expected predecessor;
- lease expiry must still follow injected wall clock after the epoch fix;
- a control expires after freeze but before commit/retry;
- a clock assertion is placed after partial scoring mutations;
- a late parallel branch belongs to a lost scoring attempt;
- deployment updates the shared legacy Scheduler;
- test discovery reports success with zero clock tests;
- byte accounting stops only after an over-budget query has run.

**PREDICT verdict: CAUTION.** Architect, security, performance, product/UX, and
devil's-advocate views agree on the narrow clock fix and serial fallback.
Unresolved high-risk items are parallel finalization, frozen authorization,
disposable resource targeting, and pre-dispatch budget reservation. Their
mitigations are V2-E4 through V2-E8; none may be converted into an assumption.

#### V2 local EXECUTE evidence — 25-07-2026

The locally fixable portion of the CONDITIONAL gate is complete:

- fixture-owned run/tick clock, checkpoint identity/clock fallback, and
  pre-scoring/pre-forecast clock assertions are implemented;
- control authorization is frozen once at `input_frozen_at` and is reused
  across retry;
- deploy contracts use exact 14-digit tagged jobs/Schedulers, accelerated
  `*/2` and separately named PAUSED real-operations `*/15` profiles, with no
  Cloud Run Service/RAM-cache path;
- the runtime evidence evaluator covers FULL-only filtering, nearest-rank
  p95, budget reservation before dispatch, execution correlation,
  overlap/duplicate rejection, terminal invocation 97, exact-tag cleanup, and
  conditional paired parallel selection.

Evidence:

| Gate | Result |
|---|---|
| Named Phase 5R suites | PASS — 80 tests |
| Full repository discovery | PASS — 186 tests in 300.424s |
| `compileall` | PASS |
| `pip check` | PASS — no broken requirements |
| deploy script syntax | PASS |
| `git diff --check` | PASS |
| strict plan artifact validator | PASS — 0 failures / 0 warnings |

The remaining gate is Hybrid/provider evidence. This local result does not
authorize network access, GCP mutation, recurring Scheduler enablement, or
Stage 3P. Stage 3P remains serial unless the corrected provider p95 is above
90 seconds and the complete paired/fenced experiment passes every V2 gate.

### Phase 5R amendment validation

**Validation date:** 24-07-2026
**Net Gate:** **PASS** for Stage 0R Research entry; EXECUTE still requires the
user's explicit `ENTER PHASE 5R EXECUTE MODE` authorization.
**Mode:** Deep validation because the amendment crosses GCS checkpointing,
BigQuery transaction/schema contracts, Cloud Run IAM, deterministic replay,
latency SLOs, and operator-visible forecast lineage.

#### Phase 5R V1 pre-check evidence

- Target/worktree resolved to this single authoritative plan on `main`;
  unrelated branch synchronization was not attempted.
- `process/context/all-context.md` and `process/context/tests/all-tests.md` are
  absent; current source, tests, Phase 5 evidence, and plan were used as truth.
- Strict plan validation passed with `0 failures` and `0 warnings`.
- Local baseline passed: 140 unittests, `compileall`, and `pip check`.
- VALIDATE was read-only with respect to GCP: no provider query, bucket, IAM,
  job, Scheduler, or shared-dataset mutation was performed. The simulation
  Scheduler remains `PAUSED`.

#### Phase 5R validation strategy

The strategy score was `5/7`, so parallel read-only validation lanes were
appropriate: infrastructure/contracts, tests/early stages, late
stages/breaking changes, and root security/synthesis. Implementation remains a
single-writer staged stream.

#### Phase 5R Layer 1 dimensions

| Layer 1 dimension | Initial status | Final status |
|---|---|---|
| Infrastructure fit | FAIL | PASS |
| Test coverage | FAIL | PASS |
| Breaking changes | FAIL | PASS |
| Security surface | PASS | PASS |

#### Phase 5R Layer 2 sections

| Layer 2 section | Initial status | Final status |
|---|---|---|
| Stage 0 — Baseline and contract research | CONCERN | PASS |
| Stage 1 — Codec, store, and deterministic oracle | PASS | PASS |
| Stage 2 — Incremental publication hot path | CONCERN | PASS |
| Stage 3 — BigQuery and TimesFM optimization | CONCERN | PASS |
| Stage 4 — Risk-adaptive execution and lineage | CONCERN | PASS |
| Stage 5 — Deployed proof, rollback, and closeout | CONCERN | PASS |

**Initial totals: 3 FAILs / 5 CONCERNs / 2 PASSes**
**Final re-validation totals: 0 FAILs / 0 CONCERNs / 10 PASSes**
**→ Phase 5R Net Gate: PASS**

#### Phase 5R plan fixes applied during VALIDATE

| # | Finding | Remediation now locked in the plan |
|---|---|---|
| P5R1 | Checkpoint retry could recompute with different controls or expiry | Freeze a write-once canonical input manifest under the tick lease/fencing token before compute; bind predecessor generation/checksum, effective controls, policy/codec/generator versions, and input checksum |
| P5R2 | Runtime IAM could not update control rows safely | Keep authorized control requests immutable; runtime reads `AUTHORIZED` (legacy `QUEUED`) and writes only separate `APPLIED`, `REJECTED`, or `EXPIRED` receipts |
| P5R3 | GCS permissions and corrupt-object handling were underspecified | Use object creator + viewer only, uniform bucket access, public-access prevention, lifecycle retention, generation preconditions, strict bounded decoding, read-back checksum verification, and no pickle/runtime delete |
| P5R4 | Stage 0 mixed research with mutating measurement | Split Stage 0R read-only Research from Stage 0E EXECUTE-only instrumentation/provider probes; name exact planned scripts, commands, resource guards, and cleanup |
| P5R5 | TimesFM tuning assumed a speedup without a quality contract | Pin simulation-only TimesFM 2.5; A/B `512/1024/2048` with 10 latency repeats and 21 replay-time quality folds; accept only measured latency, WAPE, coverage, peak-error, decision, selection, and FULL-tick gates |
| P5R6 | Low-risk skip could leave stale current features or ambiguous lifecycle | Refresh `driver_current_features` every tick; define `MONITOR` as zero inference; use direct `SNAPSHOT_READY → SUCCEEDED` with explicit skip/projection outcomes and atomic cursor handling |
| P5R7 | Forecast reuse could lose source identity or create circular IDs | Define an independent source-generation ID first, then derive a per-tick materialization ID; persist source tick/snapshot/time, replay-time age, reuse flag, and exact-reader predicates |
| P5R8 | Replay-wide model/context state could drift across retries | Freeze risk-model and forecast-context versions on the run; persist seed completion time/count; keep live TimesFM behavior byte-for-byte unchanged |
| P5R9 | Staging concurrency and partition pruning lacked safe fallbacks | Keep `workers=1` until provider proof, retain serial fallback, remove redundant expiry calls only after TTL read-back, and require partition predicates plus bounded byte evidence |
| P5R10 | Deployed proof and rollback were not sufficiently isolated | Use disposable named job/Scheduler/dataset/bucket/run, pinned image, cumulative byte cap, 20 fixed FULL ticks plus `96+1`, complete evidence manifest, `oracle|checkpoint` kill switch, and control-migration-compatible rollback image |

#### Phase 5R execute-agent instructions

| # | Instruction | Trigger |
|---|---|---|
| E5R1 | Run Stage 0R as research only; do not instrument code or query providers before explicit EXECUTE authority. | Research entry |
| E5R2 | Run Stage 0E and all Hybrid probes only after `ENTER PHASE 5R EXECUTE MODE`, with a pinned image, paused production Scheduler, disposable resources, byte caps, and `finally` cleanup. | EXECUTE entry |
| E5R3 | Keep `oracle` as the rollback path and production default until checkpoint equivalence, corruption/fallback, frozen-manifest, and recovery gates pass. | Stages 1–2 |
| E5R4 | Migrate immutable controls/receipts before enabling checkpoint mode; never roll back to a pre-migration image afterward. | Stage 2/deploy |
| E5R5 | Retain TimesFM `context_window=2048` and serial staging unless their provider experiments pass every acceptance gate. | Stage 3 |
| E5R6 | Historical V1 gate: do not enable recurring Scheduler execution until the fixed 20 FULL-tick sample meets p95 `<=45s`, tick 95/terminal behavior passes, and the user explicitly confirms. Superseded by E5R7 for V2. | Stage 5 V1 |
| E5R7 | For V2, correct and prove the fixture-owned replay clock first; then require 20 corrected FULL ticks with p95 `<=105s`, every dispatch-to-terminal interval `<120s`, tick 95/97, zero overlap, cumulative bytes `<=50 GB`, and explicit user confirmation before any two-minute recurring Scheduler. | Stage 5 V2 |
| E5R8 | Trigger Stage 3P only from the corrected serial baseline: defer at p95 `<=90s`, A/B at `90–105s`, require it above `105s`, and accept only with at least 15% complete-tick improvement plus identical outputs/lineage and unchanged safety/cost caps. | Stage 3P/5 V2 |

#### Phase 5R mandatory Hybrid gates

- GCS codec/checkpoint round-trip, generation precondition, corruption,
  missing/orphan checkpoint, nearest-valid recovery, and replay-oracle checksum.
- TimesFM 2.5 context-window A/B with replay-time folds and full-tick latency;
  “50% faster” remains a hypothesis, not a planning assumption.
- Bounded parallel-staging/TTL/partition-pruning experiment with serial
  fallback and bytes-processed assertions.
- Disposable deployed run covering the fixed 20 FULL ticks, tick 95,
  invocation 97 terminal no-op, UI source-age proof, kill switch, and rollback.

No Hybrid gate was executed during VALIDATE; they remain mandatory EXECUTE
evidence, not waived plan defects.

#### Phase 5R high-risk PREDICT review

| Persona | Primary risk | Locked response |
|---|---|---|
| Architect | Checkpoint state and external inputs diverge on retry | Write-once frozen manifest plus explicit `freeze → compute → store → commit` boundaries |
| Security | Runtime mutates authoritative controls or deletes evidence | Immutable requests, receipt-only runtime writes, creator/viewer bucket role, no runtime delete |
| Performance/cost | Skip-heavy samples hide slow FULL ticks | Fixed 20 FULL-tick SLO sample, per-query and cumulative byte caps, 2048/serial fallbacks |
| Product/UX | Reused forecast appears fresh or readers select wrong run | Independent source/materialization identity, replay-time age, reuse flag, exact predicates |
| QA/operations | Fast path passes locally but recovery/rollback fails | Oracle checksum sentinels, corruption/missing-object probes, 96+1 proof, compatible kill switch |

The original validation evidence below remains the historical contract for
Phases 1–5.

**Validation date:** 23-07-2026
**Net Gate:** **PASS** for plan entry; EXECUTE still requires the user's explicit phase authorization.
**Strategy:** One authoritative sequential implementation stream with phase gates. Parallel agents were appropriate for read-only VALIDATE only.

### V1 pre-check evidence

- Plan target and branch/worktree were resolved to this file on `main`.
- `process/context/all-context.md` and `process/context/tests/all-tests.md` are absent; source/tests were used as truth.
- Strict artifact validation: `0 failures`, `0 warnings`.
- Baseline: `48/48` unittests passed via `venv/bin/python`; compile passed; `venv/bin/python -m pip check` reported no broken requirements.
- Global `python`/`pip` are unavailable in the fresh shell, so every local command was corrected to the venv path.

### Initial Layer 1 dimensions

| Layer 1 dimensions | Initial status | Final status |
|---|---|---|
| Infra fit | CONCERN | PASS |
| Test coverage | CONCERN | PASS |
| Breaking changes | FAIL | PASS |
| Security surface | FAIL | PASS |

### Initial Layer 2 sections

| Layer 2 sections | Initial status | Final status |
|---|---|---|
| Phase 1 — Contract and Scenario Foundation | CONCERN | PASS |
| Phase 2 — Local Deterministic Engine | FAIL | PASS |
| Phase 3 — Persistence, Snapshots, Lease | FAIL | PASS |
| Phase 4 — Scoring and Closed-loop Intervention | FAIL | PASS |
| Phase 5 — Cloud Run Job, IAM, Scheduler | FAIL | PASS |
| Phase 6 — End-to-End Replay, UI Proof, Closeout | FAIL | PASS |

**Initial totals: 7 FAILs / 3 CONCERNs / 0 PASSes**
**Final re-validation totals: 0 FAILs / 0 CONCERNs / 10 PASSes**
**→ Net Gate: PASS**

### Plan fixes applied during VALIDATE

| # | Finding | Remediation now locked in the plan |
|---|---|---|
| P1 | `exposed_2h` was incorrectly redefined as exclusive | Preserve cumulative `>=120`; `exposed_4h` remains its subset; add optional exclusive `exposed_2_to_4h` |
| P2 | `active_drivers`/`TO_COOLSTOP` conflict | Active means available/working supply; add `online_drivers`; CoolStop is online but unavailable |
| P3 | Public unauthenticated audit approval became authoritative | Public audit remains evidence-only; add IAM-authenticated immutable control job plus separate simulator consumption receipts |
| P4 | Proposal/control lineage was replayable/mutable | Exact run/tick/snapshot/prediction checks, canonical payload checksum/count, dual clocks, caps, immutable requests, idempotent receipts |
| P5 | Wall-clock TimesFM and scenario-wide DELETE broke replay | Heatwave uses `simulation_time`; current features remain current-only; deterministic prediction/forecast history is retained 30 days |
| P6 | Cross-table publication and lease were underspecified | Precreated coordinator/ticks, fencing token, in-transaction owner recheck, commit ledger last, bounded conflict retry, expiring staging |
| P7 | Published and scored cursors were conflated | Separate published/completed/pending-score cursors; score retry blocks later simulation; targeted tick retry is explicit |
| P8 | Legacy full-schema MERGE could null lineage | Table-specific update-field policies, replay-isolated demand keys, explicit current-state replacement only |
| P9 | Scheduler/IAM/cost behavior was vague | Two exact jobs, pinned image, exact tasks/retries/timeouts, identity matrix, read-back/negative tests, terminal pause SLA, byte ceilings |
| P10 | Representative replay could not prove completion | Mandatory 96 ticks + invocation 97 no-op, continuity query, expanded manifest, log schema, UI evidence, targeted restore |
| P11 | Phase 1 lacked exact schema/source/identifier contract | Research section freezes types/modes, Láng 41.1°C anchor, ERA5 query/checksum/calibration/interpolation, zero zone offsets, and strict identifier grammar |

### High-risk PREDICT review

| Persona | Primary risk | Locked response |
|---|---|---|
| Architect | Partial/mixed state across BigQuery tables | Fenced multi-table transaction with cursor separation and disposable concurrency probe |
| Security | Public UI or broad runtime identity writes controls | Immutable control request table, separate receipt table, table-level IAM, public negative-auth proof |
| Performance/cost | One-minute cron overlaps or scans old partitions | 45-second measured SLO, lease no-op, byte caps, partition predicates, terminal pause/delete SLA |
| Product/UX | Demo implies real telemetry/medical certainty | Source/derivation/simulation labels, historical replay date, monitoring-only failures, no diagnosis claim |
| QA/operations | Tests pass but deployed behavior/rollback fails | Mandatory Hybrid 96+1 run, deployed refresh/auth/log evidence, run-tagged backup and checksum restore |

### Execute-agent instructions

| # | Instruction | Trigger |
|---|---|---|
| E1 | Re-run strict plan validation and the venv baseline before the first source edit; stop on drift. | Phase 1 entry |
| E2 | Implement only Phase 1 scope and stop at its proof/user-confirmation boundary. Do not infer later-phase authority. | Every phase |
| E3 | Run `--schema-only` only against an explicit disposable dataset; assert Storage is untouched and delete only that dataset after evidence capture. | Phase 1 Hybrid gate |
| E4 | Do not run any live BigQuery feasibility probe against the shared demo dataset. | Phases 3, 4, 6 |
| E5 | Do not allow public audit rows, caller-supplied actor names, or simulator runtime permissions to create immutable control requests. | Phase 4/5 |
| E6 | Scheduler creation remains default-off and requires explicit deployment approval after manual duration/byte/IAM gates. | Phase 5 |
| E7 | Pause Scheduler and preserve evidence before any restore/cleanup; never use `--seed-demo` as normal rollback. | Phase 6 |

### Open mandatory Hybrid feasibility gates

These are not plan defects and were not run because VALIDATE/RESEARCH did not authorize billed/mutating cloud work:

- [`bigquery_tick_publication_FEASIBILITY_23-07-26.md`](bigquery_tick_publication_FEASIBILITY_23-07-26.md) — `INCONCLUSIVE`; must prove concurrent lease fencing, transaction rollback, historical retry, byte bounds, and staging expiry.
- [`timesfm_replay_clock_FEASIBILITY_23-07-26.md`](timesfm_replay_clock_FEASIBILITY_23-07-26.md) — `INCONCLUSIVE`; must prove replay-relative ten-zone TimesFM behavior and live regression.
- [`full_replay_runtime_FEASIBILITY_23-07-26.md`](full_replay_runtime_FEASIBILITY_23-07-26.md) — `INCONCLUSIVE`; must prove 96+1 deployed runtime, log lineage, evidence preservation, and targeted restore.

No backlog artifact is used to waive these gates.

## Autonomous Goal Block

```text
SESSION GOAL
Complete Phase 5R Stage 5 by correcting the replay clock, proving the corrected Cloud Run Job path, conditionally evaluating parallel forecast versus feature/ML, and completing the two-minute 96+1 accelerated replay without weakening deterministic replay or current product contracts.

AUTONOMY RULES
- Follow the validated Phase 5R stages using one authoritative writer.
- Make informed in-phase implementation decisions that do not expand scope or weaken a contract.
- Preserve unrelated user changes and record any discovered drift in the plan before proceeding.
- Treat the completed Stage 0R/0E evidence and Stages 1–4 code as the current baseline; execute the V2 sequence only after its Validate Contract is PASS.

HARD STOPS
- No source edit, provider query, IAM/schema/job/bucket mutation, or deployment while the Phase 5R V2 Validate Contract remains a placeholder.
- No shared demo resource for disposable Hybrid probes and no recurring Scheduler enablement before the corrected two-minute FULL-tick SLO, zero-overlap 96+1 proof, and user confirmation.
- No runtime mutation of immutable control requests; use receipts only.
- No checkpoint activation before frozen-manifest, oracle-equivalence, recovery, security, and control-migration gates pass.
- No pre-control-migration rollback image, destructive migration, broad restore, or --seed-demo rollback.
- No Cloud Run Service/RAM-cache migration, Storage Write API rewrite, removal of BigQuery current projections, or asynchronous fire-and-forget forecast job in Phase 5R V2.

NEXT PHASE
Validate the V2 amendment. After explicit EXECUTE authority, implement the fixture-owned replay epoch and local drift gates; then deploy a new disposable immutable candidate with Scheduler paused and prove ticks 0–4 before continuing.

CONTRACT SUMMARY
Stages 1–4 are code-complete/local green, while Stage 5 is blocked by a ledger-versus-fixture clock mismatch found at tick 4. Preserve replay-from-zero as oracle/fallback; use the 26-05-2026 fixture epoch for every simulation-time surface; keep lossless GCS checkpoints, immutable controls/receipts, exact forecast lineage, and deterministic FULL/RECOVERY/MONITOR execution. The accelerated profile is two minutes with FULL p95 <=105s, every dispatch-to-terminal interval <120s, zero overlap, and a 50 GB replay cap. Stage 3P is conditional and needs at least 15% complete-tick improvement.

EXECUTE START COMMAND
After the V2 Validate Contract is PASS: ENTER PHASE 5R EXECUTE MODE — resume Stage 5 V2 in heatsafe-p0-stateful-replay_PLAN_23-07-26.md
```

## Next Step — Cursor Plan / RIPER-5

Validate the Phase 5R V2 amendment in this plan. The next valid implementation
transition, only after that gate is PASS and explicit EXECUTE authority is
received, begins with the replay-clock correction and its local tests. Provider
work then starts from a new disposable candidate at ticks 0–4 with production
Scheduler still paused.
