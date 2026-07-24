# Phase 4 Review — Snapshot Scoring and Closed-Loop SafePause

**Verdict:** Implementation and disposable-provider evidence are green. Keep
Phase 4 in `🧪 TESTING` only until the user accepts this closeout.

## Implemented

- `score_snapshot()` now has an explicit `legacy|simulation` feature-source
  contract. Simulation scoring requires exact run/tick/snapshot/time lineage,
  reads `driver_simulation_state`, retains raw features, emits bounded model
  features plus clipping/OOD metadata, and marks only that tick `SCORED`.
- Replay TimesFM context is run-scoped, contains exactly 2,016 15-minute points
  per zone ending at `simulation_time`, and uses the provider-supported 2,048
  context window. Forecasts and predictions are deterministic MERGEs keyed by
  run/tick/snapshot/model/action lineage; later ticks do not delete them.
- Model inputs use the exact Phase 2 training envelope. The engine marks the
  tick `MODEL_INPUT_OOD`; predictions remain queryable for monitoring, while
  the trusted control writer rejects that source tick.
- `tick` now executes publish → score → `SCORED` → finalize `SUCCEEDED`.
  Scoring failure records `SCORE_FAILED`, retains the coherent snapshot and
  pending cursor, and an exact retry resumes scoring instead of republishing.
- Snapshot/proposal/audit rows carry scenario/run/tick/snapshot/expiry lineage.
  Existing audit approval remains non-authoritative.
- `queue-control` requires a trusted job execution identity and writes a
  deterministic immutable control event after checking proposal payload
  checksum, selected count, exact lineage, wall-clock expiry, simulation-time
  window, driver uniqueness, pause policy, and cap. No free-form actor exists.
- A new worker loads only trusted control events, revalidates the stored
  proposal checksum, replays controls deterministically, and publishes driver
  intervention events plus one applied/rejected/expired consumption receipt in
  the fenced tick transaction.
- Current driver and zone MERGEs now update changing lifecycle, heat, rest,
  economics, supply, exposure, demand, and fulfillment fields rather than only
  advancing lineage identifiers.

## Provider evidence

### TimesFM gate

Disposable dataset: `cohort2track2.heatsafe_phase4_probe_20260724b`

- 20,160 context rows = 2,016 × 10 zones.
- Context range: `2026-05-05T00:15:00Z` through
  `2026-05-26T00:00:00Z`.
- First forecast: `2026-05-26T00:15:00Z`, strictly after simulation time.
- 160 forecast rows, ten successful zones.
- Two-run maximum forecast deviation: `0.0`; recorded tolerance:
  `2.350047302246094`.
- Bytes processed: `745,920`; cap: `250,000,000`.
- Provider behavior and the
  [Google Cloud `AI.FORECAST` documentation](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-forecast)
  require enumerated TimesFM 2.5 context sizes; 2,048 is the correct window for
  2,016 inputs.
- Dataset deleted by `finally` cleanup.

### Exact scoring + trusted closed loop

Disposable dataset: `cohort2track2.heatsafe_phase4_probe_20260724g`

- Tick 0 and tick 1 materialized exact-lineage monitoring predictions and were
  marked `MODEL_INPUT_OOD`; neither was eligible as a trusted control source.
- Tick 2 was the first non-OOD source. A deterministic proposal selected two
  scored drivers; the trusted writer queued its exact checksum/lineage and a
  fresh repository process consumed it.
- Tick 3 published two intervention rows and exactly one consumption receipt.
  Control status became `CONSUMED`; both selected drivers carried a current
  intervention.
- Four retained prediction snapshots contained 27,522 prediction rows.
- Four retained forecast snapshots contained 640 rows over ten zones.
- Tick 3 current feature rows: 748; tick status: `SUCCEEDED`.
- Run cursors both advanced to index `3`; pending score is null.
- The Phase 3 publisher cap of 250 MB was empirically insufficient after
  adding intervention/control statements: the failed job billed 241,172,480
  bytes before needing another 10 MB statement. Publisher cap is now a bounded
  350 MB.
- The OOD-safe combined scoring/TimesFM script billed 230,686,720 bytes before
  needing one additional 20,971,520-byte statement. Its bounded cap is now
  300 MB; the standalone TimesFM probe remains capped at 250 MB.
- Dataset and copied model deleted by `finally` cleanup. Shared
  `heatsafe_data` was read only for model copy and was not mutated.

## Review outcome

The first review found that simulation SQL used broad physical clamps rather
than the frozen Phase 2 model envelope, which could make an OOD tick eligible
for trusted control. The final implementation uses all nine exact bounds,
preserves per-row clip reasons, carries the engine's aggregate
`MODEL_INPUT_OOD` marker, and excludes those source ticks in the trusted writer.
The corrected disposable provider run is the evidence reported above.

## Automated evidence

- `venv/bin/python -m unittest discover -s tests -v`: 129 tests, all green.
- Focused Phase 4 contracts cover legacy regression, persisted-state SQL,
  exact lineage, model failure/retry, OOD metadata, proposal checksum/count,
  both clocks, cap, duplicate identity, public non-authority,
  selected-versus-control behavior, recovery risk input, provider script safety,
  and target schema compatibility.
- Compile, dependency check, diff check, and strict plan validation pass.

## Boundaries

- No shared dataset mutation, service deployment, IAM change, Cloud Run Job, or
  Scheduler mutation was performed.
- The local ADC credential emits Google's standard missing quota-project
  warning. Both provider probes completed, but deployed jobs must use the
  Phase 5 service identities and billing/quota project.
- Predictions remain operational synthetic-risk estimates, not diagnosis or
  causal proof of health outcomes.
