---
slug: fast-replay-history-batching
date: 2026-07-25
verdict: VIABLE
originating-phase: pvl
---

# Fast Replay History Reconstruction and Transport Batching

## Hypothesis

HeatSafe can reconstruct the public `ZoneSnapshot` contract from its existing
per-tick history and can buffer only `order_events` plus
`driver_state_history` in groups of four or eight without changing their rows.

## Mechanism Under Test

The probe executes the existing sequential in-memory repository, reconstructs
each public zone from `zone_operations`, `weather_observations`, and frozen zone
priors, and sends the same immutable order/driver-history rows through
transport collectors with batch sizes `1`, `4`, and `8`.

It does not change `advance_tick()`, scoring/control order, demand visibility,
checkpoints, tick ledger, current snapshots, or forecast boundaries.

## Probe Family

`2 — Unit/integration test harness`

## Probe Cost Class

`cheap-local`. The safety gate was met. The probe made no network, provider,
dataset, Scheduler, or deployment call.

## Probe Method

```bash
venv/bin/python -m unittest tests.test_phase6_fast_replay_probe -v
venv/bin/python scripts/probe_phase6_fast_replay.py --ticks 8
```

Probe implementation:

- `scripts/probe_phase6_fast_replay.py`
- `tests/test_phase6_fast_replay_probe.py`

## Evidence Captured

Targeted tests:

```text
Ran 3 tests in 3.402s
OK
```

Current repository baseline:

```text
Ran 194 tests in 286.296s
OK
```

Eight-tick probe:

```text
elapsed_seconds: 34.382
history_reconstruction.passed: true
history_reconstruction.failed_ticks: []
transport_batch_equivalent: true
provider_runtime_proven: false

driver_history_rows:
  rows: 49840
  sha256: 9ecd27e25d1ea9e6e6c2a9bb65a0399dfd55fdc9cb12e686131b261ae89f075c

order_rows:
  rows: 17533
  sha256: 5f176cc991d913441d748580a987102609d7d7d6bd9470ee4573101b2e736dbf
```

The row counts and SHA-256 values were identical for batch sizes `1`, `4`, and
`8`. All eight public snapshots reconstructed from history matched the current
projection contract.

## Verdict

VIABLE

The existing tables are sufficient for the public timeline read model, and
transport-only batching is locally equivalent for the two named heavy
append-only histories.

## Resulting Design Constraint

- **What this licenses:** implement exact-tick repository queries over existing
  history; prototype batch sizes `4` and `8` only for `order_events` and
  `driver_state_history`.
- **What this forbids:** no new snapshot-history table by default; no batching
  of state transitions, controls, tick ledger, checkpoints, current snapshots,
  weather/operations visibility, demand/forecast barriers, or scoring lineage.
- **What remains uncertain (known-gap):** BigQuery transaction/load latency,
  Cloud Run memory, mid-batch provider failure recovery, and the claimed
  `96+1 <=30 minutes` runtime require the bounded provider ladder and are not
  proven by this local probe.
