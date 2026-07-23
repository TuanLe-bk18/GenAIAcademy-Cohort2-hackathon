---
slug: timesfm-replay-clock
date: 2026-07-23
verdict: INCONCLUSIVE
originating-phase: pvl
---

## Hypothesis

BigQuery `AI.FORECAST` can consume run-scoped 15-minute history capped at a historical `@simulation_time` and return a horizon beginning after that replay time for all ten zones.

## Mechanism Under Test

TimesFM context filtering, replay-time forecast timestamps, multi-zone status, repeat behavior, and historical seed sufficiency.

## Probe Family

3 — tRPC / Prisma / DB query (cloud database query variant).

## Probe Cost Class

`needs-live-provider`. The safety gate was not met: this RESEARCH turn does not authorize billed BigQuery AI queries or disposable cloud mutations.

## Probe Method

The probe was not run. The execution gate will seed an isolated run-scoped 21-day history ending at `@simulation_time`, execute the same `AI.FORECAST` twice, and verify context/horizon timestamps, ten-zone status, result tolerance, partition pruning, and byte ceilings.

## Evidence Captured

No live output was captured. Source inspection proves both current TimesFM paths use `CURRENT_TIMESTAMP()` and therefore do not meet the replay-clock hypothesis today.

## Verdict

INCONCLUSIVE

## Resulting Design Constraint

- **What this licenses:** Implement an explicit heatwave replay-time branch while preserving the live wall-clock branch, subject to the Hybrid probe.
- **What this forbids:** Do not reuse the current wall-clock query or claim replay-relative forecasts from static SQL review.
- **What remains uncertain (known-gap):** Exact TimesFM timestamp behavior and repeat tolerance for the final run-scoped query.
