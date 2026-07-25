# Phase 5R Incremental Checkpoint and Risk-Adaptive Latency Research

**Date:** 24-07-2026
**Mode:** Phase 5R Stage 0R — read-only research
**Verdict:** `PASS WITH EXECUTE GATES`
**Provider mutation:** none
**Source mutation:** none

## Context Envelope

| # | Field | Value |
|---:|---|---|
| 1 | `feature` | `heatsafe-p0-stateful-replay` |
| 2 | `phase` | `RESEARCH` |
| 3 | `session-goal` | Freeze the Phase 5R measurement, checkpoint, TimesFM, and risk-adaptive execution contracts before implementation |
| 4 | `branch` | `main` |
| 5 | `worktree` | `/Users/tuanle/CODE/my-project/heatsafe-hackathon` |
| 6 | `context-group` | `none` — `process/context/` is absent |
| 7 | `blast-radius-packages` | Research: this plan folder only; later EXECUTE: `heatsafe/simulation`, `infra`, deployment/config scripts, simulation tests |
| 8 | `active-plan` | `process/general-plans/active/heatsafe-p0-stateful-replay_23-07-26/heatsafe-p0-stateful-replay_PLAN_23-07-26.md` |
| 9 | `test-runner` | `venv/bin/python -m unittest discover -s tests` |
| 10 | `validate-contract` | Active plan `## Validate Contract` |

## Outcome

Phase 5R remains the right remediation direction, but Stage 0R found one
important boundary:

- the authoritative 41.1°C heatwave fixture is high-risk for the full replay;
- therefore risk-adaptive skipping cannot be credited for the Phase 5 latency
  SLO;
- checkpointing and BigQuery critical-path reduction must independently bring
  the fixed FULL-tick sample to p95 `<=45s`;
- risk-adaptive execution remains useful for lower-risk fixtures and future
  live telemetry, and must be proved with a separate bounded fixture without
  altering the authoritative demo.

The frozen contracts below are ready for Stage 0E instrumentation and
provider experiments after explicit `ENTER PHASE 5R EXECUTE MODE`.

## Evidence and Current-State Findings

### Latency boundary

- Phase 5 provider durations are `49.455s`, `87.044s`, and `96.568s`; observed
  p95 is `96.568s`.
- Tick 95 currently performs 96 calls to `advance_tick()` through
  replay-from-zero. Each call advances 15 simulated minutes for 6,230 drivers.
- The publisher performs nine sequential staging loads. Each non-empty load
  fetches the target schema, waits for the load job, fetches the staging table,
  and updates its expiry even though the staging dataset already has a default
  TTL.
- `driver_state_history` merges only by `state_id`, without a partition
  predicate.
- Simulation scoring re-merges 20,160 forecast-context rows every score:
  2,016 points per zone across ten zones.
- The runtime currently updates `simulation_control_events`, while the deployed
  runtime identity has viewer-only access to that table. Stage 2 must complete
  the validated immutable-request/receipt-only migration before checkpoint mode.

### State growth is bounded more strongly than previously assumed

A read-only local replay of the current engine produced:

| Tick | Local time | Heat tier | Drivers | Open orders | Tick events | Max driver minute tuple |
|---:|---|---|---:|---:|---:|---:|
| 0 | 00:00 | DANGER | 6,230 | 584 | 1,882 | 16 |
| 24 | 06:00 | EXTREME_CAUTION | 6,230 | 1,400 | 4,432 | 60 |
| 48 | 12:00 | DANGER | 6,230 | 1,627 | 5,140 | 60 |
| 95 | 23:45 | DANGER | 6,230 | 918 | 2,930 | 60 |

Observed day-wide maxima were 3,653 open orders and 8,785 events. The engine
already:

- removes terminal orders during minute advancement;
- clears the event tuple at the start of every public tick;
- caps distance, earnings, and contribution histories at 60 values per driver;
- prunes completed/cancelled interventions after their short retained window.

Therefore “serialize only open orders” is already the current logical
behavior. Phase 5R must preserve that behavior and add decoder ceilings, but it
does not need a second terminal-order pruning design.

### The heatwave fixture legitimately forces FULL mode

The 96-tick local replay contains:

| Heat tier | Tick count |
|---|---:|
| EXTREME_CAUTION | 24 |
| DANGER | 60 |
| EXTREME_DANGER | 12 |

There are no NORMAL or CAUTION ticks. Cohorts with at least two and four hours
of exposure are already present at tick 0 because the existing operational
priors intentionally seed ongoing exposure. Clock hour alone must never
override these signals. On this extreme day, all 96 ticks are expected to be
`FULL`.

### TimesFM source contract

The current simulation query already supplies exactly:

```text
zone_id, interval_start, requests
```

There is no `SELECT *` optimization left to claim. The current query:

- relies on the provider default instead of passing `model`;
- uses `context_window=2048`;
- uses `horizon=16`;
- supplies 2,016 history points per zone, or 21 days;
- regenerates the complete context seed on every scoring call.

Current Google documentation says the default is now TimesFM 2.5 and recommends
2.5 for new tasks. Explicitly pinning it is still required for reproducibility.
TimesFM 2.5 supports context windows `64` through `15,360`; `512`, `1024`, and
`2048` are valid controlled candidates. See
[AI.FORECAST](https://cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-forecast).

## Frozen Contract 1 — Component Timing Events

### Event schema

Every component completion emits one JSON line:

```text
schema_version = "phase5r-component-v1"
event = "simulation_tick_component"
component = enum below
outcome = SUCCEEDED | FAILED | SKIPPED | NO_OP
recorded_at = UTC wall-clock timestamp
elapsed_ms = non-negative integer from time.monotonic_ns()
cloud_run_job
cloud_run_execution
task_index
task_attempt
attempt_id
component_attempt
simulation_run_id
tick_id
tick_index
snapshot_id
state_mode = oracle | checkpoint
execution_mode = FULL | RECOVERY | MONITOR
bigquery_job_id = nullable
slot_millis = nullable
total_bytes_processed = nullable
total_bytes_billed = nullable
row_count = nullable
object_bytes = nullable
error_code = nullable bounded enum/string
```

`attempt_id` is the deterministic composite of execution, task index, and task
attempt. `component_attempt` is one-based and increments only for an explicitly
retried component operation. Durations use a monotonic clock; `recorded_at` is
for log navigation only.

### Component enum

```text
run_load
lease_acquire
checkpoint_restore
checkpoint_replay_delta
controls_load
input_freeze
advance_tick
publication_projection
staging_schema_lookup
staging_load_driver
staging_load_zone
staging_load_order
staging_load_weather
staging_load_operation
staging_load_demand
staging_load_history
staging_load_intervention
staging_load_consumption
checkpoint_encode
checkpoint_upload
checkpoint_readback
publication_commit
feature_projection
timesfm_context_ensure
ai_forecast
ml_predict
ml_explain_predict
score_finalize
tick_total
```

Exactly one `tick_total` event is emitted from a top-level `finally` block for
every task attempt, including terminal/overlap no-ops and failures. It spans
the whole CLI attempt and never double-counts nested component time.

### Logging exclusions

Logs must never contain:

- checkpoint bytes or decoded checkpoint state;
- input/control/proposal payloads;
- driver IDs, driver rows, or selected-driver lists;
- BigQuery SQL containing embedded values;
- credentials, tokens, signed URLs, or object contents.

The deployed job can use its existing stdout JSON path. Cloud Run attaches job,
execution, task index, and task-attempt labels to job logs, while the container
contract also exposes `CLOUD_RUN_EXECUTION`, `CLOUD_RUN_TASK_INDEX`, and
`CLOUD_RUN_TASK_ATTEMPT`. See
[Cloud Run logging](https://cloud.google.com/run/docs/logging) and the
[container runtime contract](https://cloud.google.com/run/docs/container-contract).

## Frozen Contract 2 — Runtime and Provider APIs

### Runtime identity

Current truth is:

```text
deployed image digest:
  sha256:f30511403e41d386d499ccb0fbc2085c7f22721798a212318f0ebedcb878280c
Docker base family:
  python:3.12-slim
google-cloud-bigquery:
  3.42.1
google-cloud-storage:
  3.12.0
local validation Python:
  3.14.6 — not the checkpoint golden runtime
```

`python:3.12-slim` is a mutable tag and does not prove an exact Python patch or
base-image digest for a future rebuild. Before generating codec golden bytes,
Stage 0E must capture:

```text
image digest
base image digest
sys.version
platform.machine()
zlib.ZLIB_VERSION
google-cloud-storage version
google-cloud-bigquery version
codec version
```

Their canonical hash becomes `runtime_contract_id`. Codec golden evidence is
valid only for that ID. The final EXECUTE image must pin the base image by
digest or otherwise refuse checkpoint activation when the recorded runtime ID
does not match.

### GCS API

Static API verification confirms `google-cloud-storage==3.12.0` exposes:

- `Blob.upload_from_string(..., if_generation_match, timeout, checksum, retry)`;
- `Blob.download_as_bytes(..., if_generation_match, timeout, checksum, retry)`;
- `Blob.reload(..., if_generation_match, timeout, retry)`.

Upload uses `if_generation_match=0`. Google documents that this succeeds only
when no live object has that name and returns `412` otherwise. Conditional
retries are safe when the generation precondition is supplied. See
[request preconditions](https://cloud.google.com/storage/docs/request-preconditions),
[retry strategy](https://cloud.google.com/storage/docs/retry-strategy), and
the [Python Blob API](https://cloud.google.com/python/docs/reference/storage/latest/google.cloud.storage.blob.Blob).

The checkpoint bucket is regional `ASIA-SOUTHEAST1`, matching the current Cloud
Run/BigQuery region. It uses Standard storage, uniform bucket-level access,
public-access prevention, and a 35-day age lifecycle. Runtime receives
`roles/storage.objectCreator` plus `roles/storage.objectViewer`, which permits
create/read/list but not overwrite/delete. See
[bucket locations](https://cloud.google.com/storage/docs/bucket-locations) and
[Cloud Storage IAM roles](https://cloud.google.com/storage/docs/access-control/iam-roles).

### BigQuery API

`google-cloud-bigquery==3.42.1` exposes `QueryJob.job_id`, `slot_millis`,
`total_bytes_processed`, `total_bytes_billed`, `started`, and `ended`.
Instrumentation must retain the `QueryJob` before calling `.result()`; the
current repository/scoring code discards it. Fields remain nullable because
not every load/query/cache path reports every statistic. See the
[QueryJob API](https://cloud.google.com/python/docs/reference/bigquery/latest/google.cloud.bigquery.job.QueryJob).

Provider behavior, exact latency, retry counts, IAM readback, and bucket
lifecycle behavior remain Stage 0E/Stage 5 Hybrid evidence.

## Frozen Contract 3 — Checkpoint Codec and Store

### Envelope

The only accepted top-level shape is:

```text
format_version = "heatsafe-checkpoint-v1"
codec = "json-floathex-offset-gzip-v1"
runtime_contract_id
scenario_version
generator_version
run_id
tick_id
tick_index
input_checksum
state_checksum
state
```

`state` contains every `SimulationState` field and every nested field from:

- `DriverState`;
- `OrderState`;
- `InterventionState`;
- `OrderEvent`.

Encoding rules:

- UTF-8 JSON, sorted keys, compact separators, no NaN/Infinity;
- enums as their exact string values;
- datetimes encoded as a tagged canonical UTC instant with six microsecond
  digits and `Z` plus a bounded original UTC offset in minutes; decode restores
  the fixed offset because simulation demand uses local clock fields;
- floats as tagged `{"$float64_hex": "<float.hex()>"}` objects;
- ordinary bounded integers as JSON integers;
- `schedule_bits`, `rest_minute_bits`, and `trips_minute_bits` as tagged
  `{"$int10": "<unsigned decimal>"}` objects;
- tuples as arrays, rebuilt only by static typed constructors;
- exact required-field sets at every level; unknown/missing fields fail closed;
- booleans are rejected where an integer is required;
- no dynamic imports, pickle, object hooks, executable tags, or class names.

### Object name

```text
checkpoints/v1/
  scenario={scenario_id}/
  run={run_id}/
  tick={tick_index:02d}-{tick_id}/
  input={input_checksum}/
  state.json.gz
```

All identifiers must first pass the existing strict identifier grammar.
Checkpoint metadata records the exact object generation; reads use that
generation, not “latest”.

### Deterministic compression

- `gzip.GzipFile` over bytes;
- `filename=""`;
- no comment or extra field;
- `mtime=0`;
- compression level `6`;
- pinned `runtime_contract_id`;
- SHA-256 of the expanded canonical JSON;
- SHA-256 of the compressed object;
- CRC32C transport verification;
- repeated encoding in the pinned container must produce identical bytes.

### Decoder ceilings

```text
compressed object:        16 MiB
expanded JSON:            64 MiB
expanded/compressed ratio: <=100
nesting depth:            <=12
drivers:                  exactly 6,230 for hanoi_heatwave_v1; global cap 10,000
orders:                   <=20,000
events:                   <=50,000
interventions:            <=2,000
zone_shocks:              exactly 10 for hanoi_heatwave_v1; global cap 100
per-driver minute arrays: <=60 each
identifier/string length: <=256 UTF-8 bytes unless a narrower field grammar applies
effective selected drivers in one frozen tick manifest: <=250
```

Decompression is streamed and aborts before allocating beyond the expanded
ceiling. Count and string limits are checked while decoding, before building
the complete state.

### Retry and fallback

- upload: generation precondition `0`, CRC32C, connect/read timeout `(5s, 30s)`;
- reload/download: exact generation match, same timeout;
- retry: exponential `1s` initial, multiplier `2`, maximum delay `8s`, maximum
  five attempts, total deadline `60s`;
- `412` is not blindly retried: read the existing object and accept only exact
  compressed hash, expanded hash, input checksum, state checksum, and typed
  state equality;
- upload is followed by generation/size/hash readback and full typed
  round-trip before BigQuery metadata commit;
- unsupported format/runtime/codec, corrupt bytes, failed limits, or checksum
  mismatch rejects that checkpoint and tries the preceding committed
  checkpoint;
- if no supported checkpoint remains, replay from tick zero using each tick's
  frozen input manifest;
- no hot-path checkpoint migration is allowed. Any future migration is an
  offline, separately authorized operator action.

Stage 0E may tighten ceilings after measured evidence but may not relax them
without re-review.

## Frozen Contract 4 — TimesFM Evaluation

### Model and candidates

```text
model: TimesFM 2.5, explicitly supplied
horizon: 16
confidence level: 0.90
candidate context windows: 512, 1024, 2048
baseline: 2048
input columns: zone_id, interval_start, requests
query cache: disabled
```

### Evaluation corpus

The current 21-day seed is not sufficient by itself for seven held-out
evaluation days with a 2,048-point context. Stage 0E creates one disposable,
run-scoped corpus with:

```text
earliest_cutoff = first held-out day at 05:45 Asia/Ho_Chi_Minh
latest_cutoff   = seventh held-out day at 16:45 Asia/Ho_Chi_Minh
corpus_start    = earliest_cutoff - 2,047 * 15 minutes
corpus_end      = latest_cutoff + 16 * 15 minutes
zones           = the same ten run-scoped zones
source/version  = frozen deterministic 15-minute demand generator and checksum
```

The seven held-out dates are consecutive and end on the authoritative replay
date. Origins are `05:45`, `10:45`, and `16:45` local time, producing 21 folds.
For every fold:

- model input is `interval_start <= cutoff`;
- actuals are `cutoff < interval_start <= cutoff + 4 hours`;
- actual rows are never present in input;
- every candidate receives byte-identical rows apart from the intended context
  truncation;
- row counts, first/last timestamps, ten-zone coverage, and corpus checksum are
  asserted before a billed call.

### Latency protocol

- fixed cutoff: authoritative replay date at `10:45` local time;
- one unmeasured warm-up per candidate;
- ten measured calls per candidate;
- deterministic round-robin candidate ordering;
- nearest-rank p95 (`ceil(0.95*n)`) plus median/max;
- capture elapsed time, slot millis, processed/billed bytes, and complete FULL
  tick time;
- `AI.FORECAST` and full-tick results are separate metrics.

### Quality and decision metrics

For city-wide and every zone:

- WAPE with recorded non-zero actual denominator;
- MAE;
- 90% interval coverage;
- actual and predicted peak timestamps;
- absolute peak-timing error in 15-minute intervals.

For downstream behavior:

- identical driver features, budget, sponsorship, horizon, and risk-model
  version at each origin;
- recommendation status;
- `within_guardrails`;
- each guardrail result;
- selected-driver count.

### Acceptance

A smaller context is selected only when all conditions pass:

- AI.FORECAST nearest-rank p95 improves at least 20% versus 2,048;
- complete FULL-tick time improves materially and does not regress p95;
- city and every-zone WAPE are no more than 5% relatively worse;
- interval coverage is no more than five percentage points worse;
- no fold's peak-timing error is more than one 15-minute interval worse;
- no feasibility or guardrail result flips;
- selected-driver count changes at most 5% per comparable decision;
- no status, timestamp, ten-zone, lineage, or byte-cap assertion fails.

Otherwise retain 2,048. “50% faster” remains an unverified hypothesis.

## Frozen Contract 5 — Risk-Adaptive Execution Policy

### Inputs

`plan_tick_execution()` is pure and receives:

- current maximum city heat tier;
- maximum tier from the next two fixture ticks (30 minutes);
- city `exposed_2h` and `exposed_4h` totals;
- valid pending controls;
- active interventions;
- previous persisted execution mode and low-risk streak;
- forecast availability/age/failure/anomaly state;
- persisted scoring failure state.

Wall-clock hour is recorded for explanation only and is never a skip trigger.
Heat dose is not a v1 mode trigger because its current scale is an internal
simulation feature, not a validated clinical threshold.

### Mode precedence

`FULL` wins when any condition is true:

1. a persisted FULL tick is being retried after scoring failure;
2. a valid pending control or active intervention exists;
3. current or 30-minute look-ahead tier is `EXTREME_CAUTION`, `DANGER`, or
   `EXTREME_DANGER`;
4. any `exposed_4h` cohort remains;
5. the city demand anomaly rule fired on the preceding complete interval set;
6. the system is within the two-tick pre-warm window before forecasted DANGER.

Otherwise:

- remain `FULL` until two consecutive ticks have current and look-ahead tiers
  at most `CAUTION`, zero `exposed_4h`, and no control/intervention/anomaly;
- then enter `RECOVERY` for two ticks while continuing exact feature projection
  and audit publication but skipping ML inference;
- enter `MONITOR` after two clean RECOVERY ticks;
- any FULL trigger immediately resets the streak and returns to FULL.

Reason codes are persisted as a sorted set:

```text
PERSISTED_FULL_RETRY
CONTROL_PENDING
INTERVENTION_ACTIVE
CURRENT_HEAT_TIER
LOOKAHEAD_HEAT_TIER
EXPOSED_4H
DEMAND_ANOMALY
DANGER_PREWARM
FULL_EXIT_HYSTERESIS
RECOVERY_COOLDOWN
LOW_RISK_STABLE
```

Current features are projected every tick in every mode. Only ML inference is
skipped. Missing predictions keep the UI monitoring-only.

### Forecast cadence

- force generation on the first FULL tick, FULL escalation, expired/missing
  horizon, prior forecast failure, or anomaly;
- otherwise generate every fourth FULL tick;
- materialize only unexpired source points for intervening ticks with exact
  source/materialization lineage and replay-time age;
- MONITOR/RECOVERY may reuse a valid horizon but cannot produce a SafePause
  recommendation without exact-current predictions.

The authoritative heatwave fixture is expected to remain FULL for all 96 ticks.

## Frozen Contract 6 — Demand Anomaly

After two complete 15-minute intervals are available, calculate:

```text
city_wape =
  SUM(ABS(actual_requests - predicted_requests))
  / SUM(actual_requests)
```

over all ten zones and both intervals.

Trigger `DEMAND_ANOMALY` only when:

```text
all ten zones have actual and forecast rows
SUM(actual_requests) >= 200
city_wape > 0.30
```

The trigger is persisted on the completed tick and forces a refresh at the next
allowed tick. It does not retroactively change a frozen execution plan. Missing
or partial rows produce `ANOMALY_UNAVAILABLE`, not a percentage derived from
near-zero data and not an automatic SafePause recommendation.

## Stage 0E Entry Gates

Before any provider call or source change:

1. user explicitly authorizes `ENTER PHASE 5R EXECUTE MODE`;
2. production Scheduler remains paused;
3. the experiment names a disposable dataset, bucket, job, Scheduler, and run;
4. the image digest and `runtime_contract_id` are captured;
5. per-query and cumulative byte ceilings are configured;
6. cleanup is guaranteed in `finally`;
7. the active Phase 5 evidence run and current tables are excluded;
8. TimesFM corpus timestamps/count/checksum are asserted before billing.

Stage 0E must present instrumentation, codec, TimesFM, and component-profile
evidence and stop for implementation review.

## Research Boundary

This Research turn:

- inspected current source and existing evidence;
- ran only a local deterministic replay to count state/risk characteristics;
- consulted current official Google Cloud documentation;
- did not edit application source;
- did not run BigQuery, GCS, Cloud Run, IAM, Scheduler, or other provider calls;
- did not enable recurring execution.
