# Phase 4 Research — Snapshot Scoring and Closed-Loop SafePause

**Date:** 24-07-2026  
**Mode:** Research → Innovate → Plan → Execute authorized by the user  
**Provider boundary:** isolated disposable BigQuery evidence only; no shared
`heatsafe_data` mutation or deployment is implied.

## Current-state findings

1. `infra/ml_pipeline.score_snapshot()` still derives current drivers by
   expanding `zone_snapshots_current.active_drivers`. This loses the stable
   driver identity, exposure, rest, economics, and intervention effects already
   persisted in `driver_simulation_state`.
2. The current TimesFM query uses `CURRENT_TIMESTAMP()`. A historical replay
   therefore forecasts relative to wall clock instead of the tick's
   `simulation_time`.
3. Simulation prediction and forecast writes delete the scenario's previous
   result set. This conflicts with tick-level evidence retention and exact retry.
4. The engine already implements assignment, travel, pause, completion,
   recovery, delay cancellation, and supply/economics effects through
   `PauseControl`, but the repository never supplies a trusted control or
   publishes intervention lifecycle evidence.
5. `intervention_proposals` / `intervention_events` are intentionally audit
   only. They must not become simulator commands. The pre-provisioned
   `simulation_control_events` and `simulation_control_consumptions` tables are
   the correct authority/receipt boundary.
6. Existing proposals do not yet carry complete run/tick/snapshot/expiry
   lineage. The BigQuery audit writer also omits those nullable fields.
7. The repository finalizer only accepts `SNAPSHOT_READY`; Phase 4 needs an
   explicit `SCORED` state before the cursor may advance to `SUCCEEDED`.
8. The existing model feature names and nine `NONE`/`SAFEPAUSE` action choices
   are stable compatibility contracts and should not be renamed.
9. The official BigQuery `AI.FORECAST` interface supports grouped series,
   explicit horizon, confidence level, and context window. The live provider
   defaults to TimesFM 2.5 and accepts enumerated window sizes including 2,048;
   the P0 input contains exactly 2,016 15-minute points per zone and therefore
   uses the supported 2,048 window.

## Innovation decision

Use one phase-local orchestration contract:

```text
publish exact tick
  -> materialize persisted-driver features
  -> seed/read run-scoped TimesFM history ending at simulation_time
  -> append exact-lineage forecasts + predictions
  -> mark SCORED
  -> finalize SUCCEEDED

trusted queue-control
  -> verify immutable proposal checksum/count/lineage/clocks/caps
  -> write deterministic control event
  -> next tick replays all trusted controls deterministically
  -> atomically publish intervention state + one consumption receipt
```

The simulation scorer is fail-closed. Missing model, mixed zone snapshots,
lineage mismatch, severe OOD, or query failure marks `SCORE_FAILED` and does not
advance the completed cursor. Public audit approval remains evidence only.

## Frozen implementation plan

1. Add lineage to snapshot/proposal models and audit persistence.
2. Add strict `feature_source=legacy|simulation` to `score_snapshot`.
3. For simulation, source model features from `driver_simulation_state`, clip
   only model inputs while retaining raw values and OOD metadata.
4. Seed 21 days / 2,016 points per zone from simulation time and scope TimesFM
   history by run; retain later tick results with deterministic keys.
5. Add `SCORED` finalization semantics and integrate scoring into `tick`.
6. Add a trusted `queue-control` command with deterministic payload checksum;
   do not expose a free-form actor.
7. Replay valid controls through the existing engine and persist intervention
   lifecycle/consumption evidence in the fenced tick transaction.
8. Test lineage, payload mutation, expiry clocks, cap, duplication, public
   non-authority, OOD, score failure, selected/control divergence, and live
   regression.
9. Run the mandatory disposable TimesFM provider probe with byte caps and
   automatic cleanup, then write the review report.

## Known non-goals

- No Cloud Run Job, IAM, Scheduler, or public UI deployment in Phase 4.
- No training on replay output.
- No claim that counterfactual model output proves medical benefit.
- No ingestion of public audit rows as simulator authority.
