# Phase 3 Review — BigQuery Persistence and Snapshot Projection

**Verdict:** Provider evidence captured; keep the phase in `🧪 TESTING` until
the user accepts this evidence. No shared demo dataset was queried or changed.

## What is proven

- A scenario creates one active run and 96 deterministic ticks. A fresh lease
  excludes a competing caller; an expired or incorrect fencing token cannot
  publish. A `SNAPSHOT_READY` or `SUCCEEDED` retry recreates only the local,
  deterministic projection cache and does not republish rows.
- One publication produces 6,230 driver rows and 10 coherent zone rows with
  run/tick/snapshot lineage. It separately records the published cursor, then
  a scoring success advances the completed cursor exactly once.
- The BigQuery adapter creates the run + ledger, reloads durable state for a
  new process, uses conditional fenced leasing, stages all seven target-table
  projections with a one-hour expiry, and publishes them in one transaction.
- The isolated `cohort2track2.heatsafe_phase3_probe_20260724p` live run proved
  two independent clients yielded exactly one lease winner. Before automatic
  cleanup, read-back showed `1 SUCCEEDED` plus `95 PENDING`,
  `last_published_tick_index=0`, `last_completed_tick_index=0`, and no pending
  score. The probe creates and deletes only datasets named
  `heatsafe_phase3_probe_*`.
- Four provider defects were found and fixed: Python/BigQuery tick-ID casing
  and length mismatch; datetime JSON serialization; staging schema positional
  mismatch; and missing durable published/pending cursor update. A fifth
  cross-process reload defect (`simulation_run_id` versus `run_id`) was fixed,
  with a regression test for a restarted worker.
- `venv/bin/python -m unittest discover -s tests -q`, compile, dependency, and
  strict-plan validation pass (110 tests). The focused repository/probe suite
  has 14 tests.

## Remaining boundaries

- The probe now contains an injected failed BigQuery transaction and verifies
  the run remains `RUNNING`; its final executed evidence should be retained
  with the next approved run. Staging expiry is configured and fake-client
  covered, but observing the actual one-hour deletion is intentionally not a
  blocking wait in this prototype session.
- The authenticated local ADC credential has no quota project, producing the
  standard Google warning. The probe nevertheless completed against the
  isolated project/dataset; production deployment must use a service identity
  with a configured quota/billing project.
- Phase 4 remains blocked on explicit user confirmation of this Phase 3
  evidence, per the phase plan.
