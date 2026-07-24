# Phase 5 Cloud Run Job and Scheduler Report

**Date:** 24-07-2026
**Implementation:** complete
**Acceptance status:** `🧪 TESTING`
**Recurring cadence:** blocked by the approved latency gate

## Outcome

Phase 5 now has separate least-privilege simulation jobs, a dedicated expiring
staging dataset, an immutable image deployment, structured execution logs, and
an authenticated Scheduler dispatch path. The every-minute schedule exists but
remains `PAUSED`: measured tick p95 is `96.568s`, above the required `45s`.

No public service, legacy job, or legacy IAM role was migrated or revoked.
`heatsafe-live-ingest-15m` remains `PAUSED` and unchanged.

## Implemented

- Added `HEATSAFE_SIMULATION_STAGING_DATASET` and routed publisher load tables
  to `cohort2track2.heatsafe_sim_staging`.
- Added a one-hour default expiration and runtime-only staging writer grant.
- Added an explicit `--schema-only-current` additive migration that never
  accesses Cloud Storage or seeds data and preserves existing table layout.
- Replaced scoring `MERGE ... INSERT ROW` statements with explicit column/value
  lists so nullable lineage works with legacy column order.
- Added successful terminal and lease-overlap no-op behavior.
- Added structured Cloud Run lineage logs with execution, task attempt,
  duration, run, tick, snapshot, prediction, and checksum.
- Added `scripts/deploy_simulation_gcp.sh` with digest pinning, separate service
  accounts, exact table IAM, guarded schedule enablement, and legacy isolation.
- Added deployment/run/rollback instructions in
  `phase5_cloud_run_scheduler_RUNBOOK_24-07-26.md`.

## Provider deployment

Final runtime image:

```text
asia-southeast1-docker.pkg.dev/cohort2track2/cloud-run-source-deploy/heatsafe-simulation@sha256:f30511403e41d386d499ccb0fbc2085c7f22721798a212318f0ebedcb878280c
```

Cloud Build: `5ae6aa52-0698-44fb-9667-fd04c4426324`.

Deployed jobs:

| Job | Identity | Tasks / parallelism | Retry | Timeout | CPU / memory |
|---|---|---:|---:|---:|---:|
| `heatsafe-simulation-tick` | `heatsafe-sim-runtime` | `1 / 1` | `1` | `300s` | `1 / 1Gi` |
| `heatsafe-simulation-control` | `heatsafe-sim-control-writer` | `1 / 1` | `0` | `60s` | `1 / 512Mi` |

Scheduler:

- `heatsafe-simulation-every-minute`
- v2 Cloud Run Job `:run` URI, HTTP `POST`, OAuth scheduler identity
- `* * * * *`, `Asia/Ho_Chi_Minh`, attempt deadline `30s`
- retry attempts configured as zero; provider omits the default-zero field on
  readback
- final state `PAUSED`

## IAM evidence

Project readback contains only:

- runtime: `roles/bigquery.jobUser`;
- runtime: conditional `roles/bigquery.dataViewer` for exactly
  `heat_risk_escalation_model`;
- control writer: `roles/bigquery.jobUser`;
- scheduler: no project role.

Job policy grants Scheduler `roles/run.invoker` only on
`heatsafe-simulation-tick`.

All 15 runtime editor-table bindings and three runtime viewer-table bindings
were read back. Negative boundary readback confirms:

- runtime is viewer, not editor, on `simulation_control_events`;
- control writer is editor on `simulation_control_events`;
- runtime is editor on `simulation_control_consumptions` and
  `driver_simulation_state`;
- control writer has no binding on either runtime-owned table.

The current operator cannot mint an impersonated runtime token because it lacks
`iam.serviceAccounts.getAccessToken`; no broader Token Creator grant was added
just to run a negative test.

## Runtime evidence

Active provider run:

```text
simulation_run_id = 454bffa67d9846d7adfa743b7f35c868
last_published_tick_index = 2
last_completed_tick_index = 2
pending_score_tick_id = NULL
```

Tick 0 first exposed a legacy schema-order defect. Execution
`heatsafe-simulation-tick-8wjtd` failed after two attempts with:

```text
Value has type STRING which cannot be inserted into column interval_start,
which has type TIMESTAMP
```

After the explicit-column fix, the same pending tick resumed successfully;
publication was not duplicated.

| Evidence | Result |
|---|---|
| Tick 0 retry `cm5wv` | success; `49.455s` container duration |
| Overlap loser `g2scz` | success no-op `NO_OP_LEASE_HELD`; `4.718s` |
| Overlap winner `q6k86` | tick 1 success; `87.044s` |
| Scheduler OAuth `p477c` | created by `heatsafe-sim-scheduler`; tick 2 success; `96.568s` |
| Logical advancement | exactly one `SUCCEEDED` row each for ticks 0, 1, and 2 |
| Scheduler after proof | `PAUSED`; no autonomous tick after the one force-run |

Cloud Logging and BigQuery match for tick 0:

```text
tick_id      = 990e0e07d2dd781469dbccc84e7476b1
snapshot_id  = 67960033df0c6dc1687377934c958738
checksum     = 63cbe080d0202a6db68f0c23d203a1ba66460079de067a13b5a9314de06267f3
status       = SUCCEEDED
```

Observed successful container durations are `49.455s`, `87.044s`, and
`96.568s`. Nearest-rank p95 is therefore `96.568s`; schedule enablement is
correctly refused.

## Cost evidence

Provider readback for successful ticks:

- maximum scoring query billed bytes: `262,144,000` under the `300,000,000`
  query cap;
- maximum publisher query billed bytes: `193,986,560` under the `350,000,000`
  query cap;
- tick 1 aggregate top-level billed bytes: `560,988,160`;
- tick 2 aggregate top-level billed bytes: `550,502,400`;
- successful r2 executions used one task attempt; the pre-fix r1 failure used
  the configured maximum of two attempts.

Scheduler remains paused, so recurring cost is zero.

## Validation

```text
python compileall: PASS
bash -n deploy_simulation_gcp.sh: PASS
unittest discover: 140 tests PASS
git diff --check: PASS
provider schema/IAM/job readback: PASS
manual tick lineage: PASS
concurrent dispatch fencing: PASS
Scheduler OAuth dispatch: PASS
one-minute latency gate: FAIL (96.568s > 45s)
```

Local tests cover post-completion terminal no-op and terminal signal. Provider
tick 95 remains part of the Phase 6 full replay rather than advancing 93 extra
ticks solely for this deployment proof.

## Exit state

Phase 5 implementation is complete but remains `🧪 TESTING`. It cannot become
`✅ VERIFIED`, and the schedule must not be enabled, until the tick path is
optimized below the accepted p95 gate and the user confirms recurring
execution.
