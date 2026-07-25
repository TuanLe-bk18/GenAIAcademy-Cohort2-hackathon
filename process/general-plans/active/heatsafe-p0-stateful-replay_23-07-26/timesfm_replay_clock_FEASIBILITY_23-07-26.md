---
slug: timesfm-replay-clock
date: 2026-07-23
verdict: VIABLE
originating-phase: pvl
---

## Hypothesis

BigQuery `AI.FORECAST` can consume run-scoped 15-minute history capped at a historical `@simulation_time` and return a horizon beginning after that replay time for all ten zones.

## Mechanism Under Test

TimesFM context filtering, replay-time forecast timestamps, multi-zone status, repeat behavior, and historical seed sufficiency.

## Probe Family

3 — tRPC / Prisma / DB query (cloud database query variant).

## Probe Cost Class

`needs-live-provider`. Phase 5R EXECUTE explicitly authorized a disposable
dataset, cache-disabled billed calls, fixed byte ceilings, and `finally`
cleanup while the production Scheduler remained paused.

## Probe Method

Created a 26,840-row, ten-zone deterministic corpus spanning the full 2,048
point context and seven held-out days. Ran explicit TimesFM 2.5 with horizon 16
for context windows 512/1024/2048, one warm-up plus ten cache-disabled latency
calls per candidate, then 21 leakage-free quality folds. Every input ended at
its replay cutoff and every output timestamp was strictly after it.

## Evidence Captured

The corpus checksum was
`aaece4c62c74790be3f94206eccf7726b279c3a74b68a1ec9b39a93e1cabd716`.
All ten zones returned 16 future points with empty provider status. The run used
117 jobs, billed 1,216,348,160 bytes under a 2 GB cumulative cap, and deleted
its dataset. Nearest-rank p95 was 7.995s/10.266s/10.145s for
512/1024/2048. The 512 and 1024 candidates failed the frozen WAPE, interval
coverage, and peak-timing gates.

## Verdict

VIABLE

## Resulting Design Constraint

- **What this licenses:** Implement the simulation replay-time branch with
  explicit TimesFM 2.5, horizon 16, exactly three input columns, and context
  2048; seed once/reuse may optimize the recurring path.
- **What this forbids:** Do not reduce context to 512 or 1024, rely on the
  provider default model, or represent a wall-clock forecast as replay-relative.
- **What remains uncertain (known-gap):** The complete optimized FULL-tick
  latency and downstream forecast-reuse decision equivalence remain Stage 3/5
  Hybrid gates.
