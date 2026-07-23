---
slug: full-replay-runtime
date: 2026-07-23
verdict: INCONCLUSIVE
originating-phase: pvl
---

## Hypothesis

The deployed Cloud Run production path can complete all 96 ticks within cost/time ceilings, make invocation 97 a true no-op, preserve exact prediction lineage, and support targeted restoration.

## Mechanism Under Test

End-to-end Cloud Run execution timing, terminal behavior, log correlation, retained BigQuery evidence, and restore procedure.

## Probe Family

4 — External API shape capture (deployed Google Cloud runtime variant).

## Probe Cost Class

`needs-live-provider`. The safety gate was not met: this RESEARCH turn does not authorize 97 deployed executions or cloud data mutation.

## Probe Method

The probe was not run. The Phase 6 Hybrid gate will pause Scheduler, execute an isolated tagged run through the deployed job path, capture all execution/log/data evidence, compare the pre/post invocation-97 manifest, and perform targeted current-state restoration.

## Evidence Captured

No live output was captured. Local tests cover only existing static behavior and cannot establish runtime duration, Scheduler/job interaction, or deployed UI refresh.

## Verdict

INCONCLUSIVE

## Resulting Design Constraint

- **What this licenses:** Retain the 96+1 production-path run as a mandatory Phase 6 gate with explicit ceilings and cleanup.
- **What this forbids:** Do not substitute a representative replay, local-only test, or `--seed-demo` for terminal/runtime/restore proof.
- **What remains uncertain (known-gap):** Real execution duration, total bytes/cost, exact log correlation, and targeted restore behavior.
