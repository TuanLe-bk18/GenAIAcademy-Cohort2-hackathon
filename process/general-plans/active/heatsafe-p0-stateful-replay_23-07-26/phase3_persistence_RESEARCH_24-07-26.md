# Phase 3 Research and Innovation — BigQuery Persistence

**Date:** 24-07-2026
**Decision:** Proceed with a local/fake-client implementation and test boundary;
do not mutate the shared demo dataset or claim live BigQuery proof.

## Research findings

- The checked schema already has coordinator, run, tick, driver-state, order,
  intervention, and current-snapshot tables. Existing `merge_rows()` is a
  short-lived, single-table helper and cannot be widened into the multi-table
  publisher.
- BigQuery multi-statement transactions provide atomic commit/rollback, while
  concurrent mutating DML can still conflict on the same partition. Therefore a
  transaction alone is insufficient for single-winner publication: the active
  fencing token and unexpired lease must be rechecked inside the transaction.
- `maximum_bytes_billed` is supported per query job. Every repository query is
  bounded, labelled, and must use partition predicates for historical tables.
- The Phase 2 engine is replay-deterministic from scenario version, seed, and
  tick index. Until Phase 4 introduces persisted controls, a process restart can
  reconstruct the next state rather than serialising an undocumented opaque
  engine blob.

## Innovation decision

Use a deliberately narrow `SimulationRepository` boundary:

1. `start()` pre-creates exactly one coordinator and all 96 immutable tick
   identities.
2. `acquire_tick_lease()` returns a unique owner/fencing token only for the
   conditional winner; no mutation continues without read-back proof.
3. `publish_tick()` writes deterministic staging rows, performs the fenced
   multi-table transaction, and writes `SNAPSHOT_READY` last.
4. `finalize_score()` is separate from publication. A deterministic fake
   finalizer proves the cursor rule without importing Phase 4 ML behavior.
5. The CLI is only an adapter over this repository. It does not contain SQL,
   credential handling, or a public control path.

## Rejected alternatives

| Alternative | Reason rejected |
|---|---|
| Reuse `merge_rows()` for all Phase 3 writes | Independent MERGEs can expose mixed snapshot state. |
| Advance the completed cursor when a snapshot publishes | A later score failure would silently skip a required retry. |
| Serialize full engine state in an ad-hoc JSON field | It adds an undocumented schema dependency; deterministic replay already gives a Phase 3-safe resume path. |
| Run the probe against `heatsafe_data` | Violates the frozen no-shared-demo-mutation boundary. |

## Evidence boundary

Unit tests can prove SQL shape, row lineage, retry/no-op behavior, and CLI
routing through a fake client. They cannot prove BigQuery conflict timing,
processed bytes, or staging expiry. Those remain explicitly `INCONCLUSIVE`
until the existing disposable-dataset feasibility probe is authorized and run.
