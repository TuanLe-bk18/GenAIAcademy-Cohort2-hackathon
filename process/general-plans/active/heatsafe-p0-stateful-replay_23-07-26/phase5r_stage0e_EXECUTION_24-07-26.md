# Phase 5R Stage 0E Instrumentation and Provider Evidence

**Date:** 24-07-2026
**Mode:** Phase 5R EXECUTE — Stage 0E only
**Verdict:** `PASS WITH REQUIRED CODEC CONTRACT REVISION`
**Stage 1 authority:** not granted
**Production Scheduler:** `PAUSED`

## Context Envelope

| # | Field | Value |
|---:|---|---|
| 1 | `feature` | `heatsafe-p0-stateful-replay` |
| 2 | `phase` | `EXECUTE / Stage 0E` |
| 3 | `session-goal` | Instrument the unchanged oracle path, benchmark the checkpoint codec, profile disposable sentinel ticks, and evaluate TimesFM 2.5 context windows |
| 4 | `branch` | `main` |
| 5 | `worktree` | `/Users/tuanle/CODE/my-project/heatsafe-hackathon` |
| 6 | `context-group` | `none` — `process/context/` is absent |
| 7 | `blast-radius-packages` | `heatsafe/simulation`, `infra/ml_pipeline.py`, disposable probe scripts/tests, this plan folder |
| 8 | `active-plan` | `heatsafe-p0-stateful-replay_PLAN_23-07-26.md` |
| 9 | `test-runner` | `venv/bin/python -m unittest discover -s tests` |
| 10 | `provider-boundary` | Disposable datasets/job only; active model read-only; active run/current tables excluded |

## Outcome

Stage 0E proves that incremental checkpointing remains the correct Phase 5R
direction, but it found a correctness defect in the Stage 0R codec candidate:
normalizing `SimulationState.start_time` to UTC loses the original `+07:00`
offset. Python datetime equality still passes, but the engine derives local
demand hour from that field, so the following tick changes.

The accepted candidate is therefore
`json-floathex-offset-gzip-v1`: it stores the canonical UTC instant plus a
bounded source offset and reconstructs the fixed-offset datetime. The original
`json-floathex-gzip-v1` is rejected. Stage 1 may not implement the old codec.

TimesFM evidence rejects the proposed 512/1024 optimization and retains 2048.
The complete disposable component profile shows that tick 95 latency is
dominated by replay-from-zero, not BigQuery ML inference.

## Runtime Contract

The exact deployed Phase 5 image was executed in a disposable Cloud Run job:

| Field | Value |
|---|---|
| Image digest | `sha256:f30511403e41d386d499ccb0fbc2085c7f22721798a212318f0ebedcb878280c` |
| Base image digest | `python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de` |
| Python | `3.12.13`, GCC 14.2.0 |
| Architecture | `x86_64` |
| zlib | `1.3.1` |
| `google-cloud-bigquery` | `3.42.1` |
| `google-cloud-storage` | `3.12.0` |
| Codec | `json-floathex-offset-gzip-v1` |
| `runtime_contract_id` | `180e8ff762e4dc1fdc454972b41e88dc181e7ea39137d85e29d819add4537d89` |

Cloud Build `5ae6aa52-0698-44fb-9667-fd04c4426324` supplied the exact base
digest. Disposable job
`heatsafe-p5r-runtime-20260724165231` completed once and was deleted.

The local benchmark runtime was Python 3.14.6/arm64/zlib 1.2.12 and is not a
golden checkpoint runtime.

## Structured Component Instrumentation

The source now contains an opt-in
`HEATSAFE_SIMULATION_COMPONENT_TELEMETRY=1` path with:

- the frozen component enum and JSON-line schema;
- monotonic component spans and exactly one `tick_total`;
- Cloud Run execution/task lineage and deterministic attempt identity;
- retained BigQuery job ID, slot millis, processed bytes, and billed bytes;
- no checkpoint/control payloads, SQL values, driver IDs, or driver rows;
- `NO_OP` events for empty staging components;
- unchanged single-line legacy tick output when telemetry is not enabled.

The current scoring implementation is one BigQuery multi-statement script, so
its client monotonic span is `score_finalize`. Stage 0E decomposed the existing
script through BigQuery child-job metadata without splitting or optimizing the
production path.

## Disposable Sentinel Component Baseline

Run `ec4087ca8e2f4ab7b0126cc9d0f59d45` used disposable dataset
`cohort2track2.heatsafe_phase5r_probe_20260724173000` and its staging dataset.
It read the active heat-risk model only; it did not write active tables.

| Tick | Total | Replay prior ticks | Final advance | Schema lookups | Staging loads | Publication commit | Score finalize |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 74.033s | 0s | 0.653s | 1.354s | 17.564s | 17.446s | 24.531s |
| 24 | 93.688s | 20.864s | 0.993s | 1.112s | 14.292s | 19.365s | 26.536s |
| 48 | 121.944s | 48.555s | 1.123s | 1.084s | 17.382s | 17.506s | 26.066s |
| 95 | 177.887s | 102.165s | 0.949s | 1.244s | 16.957s | 19.136s | 26.412s |

Observed billed bytes were 1,751,121,920 against the 5,000,000,000-byte cap.
The tick-95 child jobs further separate scoring:

| Child operation | Tick-95 provider time |
|---|---:|
| Current-feature delete + insert | 2.652s |
| Full TimesFM context MERGE | 1.295s |
| `AI.FORECAST` | 6.431s |
| Action-feature projection | 0.796s |
| `ML.PREDICT` | 0.819s |
| `ML.EXPLAIN_PREDICT` | 1.278s |
| Prediction-row projection | 0.768s |

This falsifies “BigQuery ML network I/O is the main reason tick 95 is slow.”
Replay alone is 57% of tick 95 and exceeds the 45-second SLO by itself.
Sequential staging plus publication is the second structural cost. The
checkpoint hot path must therefore be implemented before smaller BigQuery
optimizations can make the SLO green.

## Checkpoint Codec Benchmark

The frozen UTC-only candidate produced exact typed equality and deterministic
bytes at tick 0, but the next-tick checksum differed. It is rejected.

The offset-preserving alternative passed typed round-trip, repeated-byte
determinism, and next-tick checksum equality at every applicable sentinel:

| Tick | Expanded | Compressed | Encode | Decode | Next tick |
|---:|---:|---:|---:|---:|---|
| 0 | 8,097,173 B | 486,790 B | 335.824ms | 313.188ms | equal |
| 24 | 17,930,936 B | 872,791 B | 849.462ms | 741.047ms | equal |
| 48 | 18,285,149 B | 1,036,283 B | 874.445ms | 761.713ms | equal |
| 95 | 17,426,878 B | 800,722 B | 826.796ms | 735.115ms | terminal |

All compressed/expanded/ratio/count ceilings passed. Local encode + decode is
below 2 seconds at every sentinel, leaving bounded headroom inside the
3-second checkpoint budget for GCS upload/readback in Stage 1/5.

## TimesFM 2.5 Experiment

The disposable corpus was strictly replay-capped and contained 26,840 rows,
ten zones, and checksum
`aaece4c62c74790be3f94206eccf7726b279c3a74b68a1ec9b39a93e1cabd716`.
Its local range was 28-04-2026 22:00 through 26-05-2026 20:45. The input
subquery exposed exactly `zone_id`, `interval_start`, and `requests`.

The provider ran 117 cache-disabled jobs and billed 1,216,348,160 bytes against
a 2,000,000,000-byte cumulative cap. The per-query cap remained 250,000,000.

| Context | p50 | nearest-rank p95 | Delta vs 2048 | Quality gate | Decision gate |
|---:|---:|---:|---:|---|---|
| 512 | 6.476s | 7.995s | 21.193% faster | fail across city/zone WAPE, coverage, and peak gates | fail closed |
| 1024 | 5.851s | 10.266s | 1.191% slower | fail across city/zone WAPE, coverage, and peak gates | fail closed |
| 2048 | 6.583s | 10.145s | baseline | pass | pass |

Result: retain `context_window=2048`, `horizon=16`, and explicit
`model => 'TimesFM 2.5'`. The “approximately 50% faster” hypothesis is rejected
for this corpus. The useful optimization remains seed-once/reuse and narrower
source filtering, not reducing the accepted context window.

The first full-protocol attempt intentionally stopped at 251,658,240 bytes when
an overly strict 250,000,000 cumulative cap was reached; cleanup succeeded.
The final run used a separately recorded 2 GB cumulative cap derived from 117
minimum-billed jobs. No per-query cap was relaxed.

## Validation

- `venv/bin/python -m unittest discover -s tests -v` — 148 passed.
- `venv/bin/python -m compileall -q app.py heatsafe infra scripts` — passed.
- `venv/bin/python -m pip check` — no broken requirements.
- Both checkpoint and TimesFM feasibility artifacts passed the installed
  feasibility validator via its absolute script path.
- Provider cleanup readback showed only `heatsafe_data` and
  `heatsafe_sim_staging`; no Phase 5R Cloud Run job remained.
- Scheduler readback remained `PAUSED`.

## Cleanup and Production Boundary

- All `heatsafe_phase5r_probe_*` datasets and staging datasets were deleted.
- The disposable runtime Cloud Run job was deleted.
- No Phase 5R bucket or Scheduler was created.
- Only `heatsafe_data` and `heatsafe_sim_staging` remain.
- The active Phase 5 run/current tables were not written.
- The active heat-risk model was read-only.
- Production Scheduler `heatsafe-simulation-every-minute` remains `PAUSED`.
- Deleted disposable resources are not recoverable by design; their evidence is
  captured here and in BigQuery/Cloud Logging job history.

## Decision and Stop Gate

Stage 0E is complete. Stage 1 remains unexecuted.

The next authorized implementation must:

1. use `json-floathex-offset-gzip-v1`, not the rejected UTC-only codec;
2. keep TimesFM context 2048 and explicitly pin TimesFM 2.5;
3. implement checkpoint restore/one-tick advance first;
4. then remove staging TTL round trips, parallelize independent loads, prune
   publication scans, and seed TimesFM context once;
5. preserve `replay_to_tick()` as oracle/fallback;
6. keep Scheduler paused until the later representative FULL p95 gate passes.
