# Predictive City-Wide SafePause Plan

**Date**: 26-07-26
**Status**: 🧪 PHASE 4 RUNTIME CHECKED — browser visual sign-off pending
**Complexity**: COMPLEX — standard complex, one execution stream; no umbrella plan
**Selected plan**: `process/general-plans/active/predictive-city-safepause_26-07-26/predictive-city-safepause_PLAN_26-07-26.md`

> **TL;DR:** Build one cloud-first preventive SafePause engine for all ten
> districts and render it through one shared UI in both `Current operations` and
> `Accelerated Production`. Cloud Run executes the engine; BigQuery remains the
> operational source of truth; BigQuery ML/TimesFM provide risk and demand
> evidence; GCS holds replay/checkpoint payloads. The modes share forecast,
> portfolio, control and visualization contracts, but Current uses only
> history/current evidence while Accelerated advances a durable simulation
> actual/shadow clock. One operational tick is always 15 minutes; accelerated
> 2/3/5-second playback is best-effort wall-clock presentation, not a timing SLA.

## Quick Links

- [1. Context and Goals](#1-context-and-goals)
- [2. Scope](#2-scope)
- [3. Architecture Decisions](#3-architecture-decisions)
- [4. Functional Requirements](#4-functional-requirements)
- [Public Contracts](#public-contracts)
- [Execution Brief](#execution-brief)
- [Acceptance Criteria](#acceptance-criteria)
- [Implementation Checklist](#implementation-checklist)
- [Touchpoints](#touchpoints)
- [Blast Radius](#blast-radius)
- [Verification Evidence](#verification-evidence)
- [Risks and Controls](#risks-and-controls)
- [Resume and Execution Handoff](#resume-and-execution-handoff)
- [Validate Contract](#validate-contract)

## 1. Context and Goals

The current UI has three connected credibility problems:

1. district Heat Index values can be identical while map colors differ;
2. a fixed or opportunity-led top-three selection can hide districts with higher
   raw safety risk;
3. the accelerated mode has a fuller stateful experience than Current
   operations, although the product intent is one operational visualization.

The corrected product model is one city planner with two evidence/clock
adapters:

| Concern | Current operations | Accelerated Production |
|---|---|---|
| Host | Cloud Run | Cloud Run |
| Authoritative data | BigQuery history/current snapshot and current BQML outputs | BigQuery simulation run/tick plus GCS checkpoint/replay |
| Future evidence | Projection from history/current only; no future fixture or oracle | Versioned synthetic scenario forecast |
| Clock | Observation-driven; Refresh on new/current data, no full-day auto-run | Exact 15-minute operational ticks; 2/3/5 seconds per tick best effort |
| Activate result | `SIMULATED_PROJECTED`: intervention projection versus no-intervention projection | Scheduled controls affect simulated actual; shadow remains no-action |
| City scope | All ten districts | All ten districts |
| UI | Shared city table, map, rankings, cost and detail panels | The same renderer and fields |

Historical `Heatwave replay` remains read-only. It is not one of the two
operational control modes.

### Goals

- Use all ten districts in every coherent city plan.
- Create preventive pauses before projected 4-hour exposure breaches and before
  demand peaks where feasible.
- Keep safety, service and cost trade-offs explicit rather than hiding them in
  one rank.
- Keep the city forecast P95 reserve within the configured budget cap, while
  clearly disclosing that P95 is not an absolute realized-cost guarantee.
- Keep cloud lineage and simulation timing deterministic without adding
  hackathon-inappropriate user authentication or real dispatch.

## 2. Scope

### In scope

- One shared `PredictiveCityPlan`, portfolio policy, control contract and UI for
  both operational modes.
- Two evidence adapters with honest semantics:
  `CurrentEvidenceAdapter` and `AcceleratedEvidenceAdapter`.
- BigQuery as source of truth; GCS checkpoint/replay for accelerated state;
  Cloud Run RAM only as a disposable cache.
- Current, +60-minute and +120-minute forecast evidence for all ten districts.
- Current mandatory, projected mandatory, watchlist, expected crossers,
  baseline risk, prevented risk and residual risk.
- Actionable SafePause starts at `0/15/30/45` minutes and durations of `15/30`
  minutes; forecast outcomes continue through +120 minutes.
- One bounded proposal per district and exhaustive city subset selection
  (`<= 2^10 = 1,024` portfolios).
- City expected cost and aligned-path city P95 reserved cost.
- Exact 15-minute simulation-clock invariants, stale-plan handling and
  idempotent Activate/advance.
- Shared all-zone table/map/detail visualization and transparent ranking.
- Simulated BigQuery audit with `dispatch_status=NOT_APPLICABLE`.

### Out of scope

- Real driver dispatch, notification, Pub/Sub command publication or payroll.
- User login, IAM redesign, approval gateway or sandbox system.
- New GCP resources, schema migration, scheduler or production deployment in
  this implementation slice.
- New weather provider, CMDP/RL, MILP or driver-level global solver.
- Causal claims about incidents prevented.
- Mutation of historical replay.

### Constraints

- Use the existing Cloud Run service identity and existing BigQuery/GCS
  resources; do not add end-user authentication for this hackathon demo.
- Current operations must never read `load_scenario`, future fixtures,
  accelerated `SimulationState`, or known future actual/shadow data.
- Current operations must not auto-run a full day. A new plan is produced only
  for a fresh observation/manual Refresh.
- Accelerated state must not rely on Streamlit session state as its sole source
  of truth.
- A provider failure is explicit and fail-closed; Current must not fall back to
  an accelerated fixture.
- All stored timestamps are UTC; UI operational time is
  `Asia/Ho_Chi_Minh`.

## 3. Architecture Decisions

### AD-1 — Cloud-first runtime, not a local-only engine

Cloud Run hosts the shared planner/control runtime. BigQuery holds current
evidence, prediction lineage, simulation run/tick cursors and audit evidence.
GCS holds replay/checkpoint payloads. Streamlit session state may cache a loaded
plan or view model, but losing that cache must not change authoritative
simulation lineage.

No real dispatch is introduced. The public hackathon action remains simulated;
the existing Cloud Run service account is sufficient for permitted BigQuery
reads/writes.

### AD-2 — Shared engine contracts, different truth semantics

Both modes use the same projection, portfolio, `PauseControl` and rendering
contracts. They do not pretend to have identical future truth:

- **Current:** fork the current observation into projected-with-SafePause and
  projected-without-intervention branches. The result is labelled
  `SIMULATED_PROJECTED`; current observed state is never overwritten.
- **Accelerated:** load the durable simulation tick, apply controls to simulated
  actual, and advance a control-free shadow with identical exogenous inputs.

This makes Current actionable for the demo without fabricating future observed
data.

### AD-3 — Operational interval is authoritative

- One tick equals exactly 15 operational minutes.
- Published ticks must start on a 15-minute boundary.
- Control starts are restricted to `0/15/30/45` minutes after the next
  actionable boundary; durations remain `15/30` minutes.
- `run_id + tick_id + tick_index` and a deterministic control/portfolio checksum
  make advance and Activate idempotent.
- BigQuery cursor updates remain fenced/transactional. A delayed worker/UI rerun
  processes the next missing tick sequentially; it never skips, compresses or
  duplicates operational time.
- Accelerated `2/3/5 seconds per tick` is best-effort wall-clock playback. UI
  delay may occur, but the displayed simulation time still advances exactly 15
  minutes per committed tick.
- Current plans expire at the earlier of the next coherent snapshot or 15
  minutes after creation.

### AD-4 — Lightweight forecast, not full engine rollouts

Use a versioned pure feature projection with 64 deterministic common-random
paths. All district/window candidates share the same path IDs. Do not call
`advance_tick` for every forecast path in the UI.

For Current:

- demand uses the exact-snapshot TimesFM forecast;
- current BQML risk stays the current raw-risk source;
- future screening uses a transparent `ProjectedRiskScorerV1` over projected
  features;
- heat uses current per-zone weather when available; otherwise the current city
  observation plus a versioned, explicitly labelled
  `MODELED_MICROCLIMATE_OFFSET`;
- weather is held constant across the short horizon unless an actual
  snapshot-matched weather forecast exists.

For Accelerated, future weather/demand comes from the versioned scenario and is
labelled `SIMULATED_FORECAST`.

### AD-5 — Safety tiers and ranking are separate

- **Mandatory now:** continuous exposure `>= 240` minutes. This is a hard safety
  cohort.
- **Projected mandatory at horizon:** currently below 240 minutes and
  `P(cross 240 minutes before valid recovery) >= 0.50`. This is preventive
  priority, not a claim that the driver is already in breach.
- **Watchlist:** crossing probability is greater than zero but below `0.50`.
- **Expected crossers:** `sum(driver crossing probabilities)`, displayed
  separately from integer cohort counts.
- A valid recovery requires at least 15 continuous paused minutes.

Expose separate severity, future-safety and opportunity ranks. Portfolio
selection follows the safety-first objective below, not one display rank.

### AD-6 — City P95 is calculated after aggregation

For every common forecast path, sum costs across selected districts, then take
the empirical nearest-rank P95:

```text
city_cost[path] = sum(selected district cost[path])
city_p95 = sorted(city_cost)[ceil(0.95 * 64) - 1]  # index 60
```

Never sum district P95 values. Filter out portfolios whose city P95 exceeds the
shared cap. If no cap-compliant portfolio covers the safety floor, return the
best feasible portfolio with `SAFETY_CAPACITY_BREACH`.

### AD-7 — Heat color and intervention status use different channels

The displayed district Heat Index is the value used for map fill color. Current
and forecast heat carry provenance. Portfolio state uses a border/marker/badge,
not a conflicting red intensity. Selection of a detail district never changes
heat color or city-plan membership.

## 4. Functional Requirements

### FR-1 — Coherent all-zone evidence

Load all ten configured districts from one snapshot/run/tick lineage. Each
district must appear as `SELECTED`, `DEFERRED`, `NO_ACTION` or `UNAVAILABLE`.
Missing evidence is not interpreted as zero risk.

### FR-2 — Preventive forecast

For every unique active driver and district, calculate:

- Mandatory now;
- projected mandatory and expected crossers at +60/+120 minutes;
- preventive pauses scheduled;
- expected mandatory after plan;
- baseline and residual risk at +60/+120;
- best actionable window;
- expected and P95 cost.

Deduplicate the multiple action rows for each driver before raw-risk and exposure
aggregation. Preserve current BQML risk separately from projected screening
risk.

### FR-3 — Heat realism

District Heat Index must be derived from district-specific weather evidence or
an explicitly labelled modeled microclimate adjustment. It must not show ten
identical values while encoding unexplained color differences.

### FR-4 — Window selection

Evaluate starts at `0/15/30/45` minutes, score outcomes through +120 minutes and
re-plan every new observation/tick. Prefer the earliest feasible lower-demand
window that improves the safety objective. In Accelerated, `K` is the final
fallback/decision pause, not a hard-coded only intervention point.

### FR-5 — Safety-first city portfolio

Among cap- and SLA-compliant portfolios, compare lexicographically:

1. maximize coverage of Mandatory-now drivers;
2. minimize projected mandatory drivers at +120 minutes;
3. minimize worst district residual risk;
4. maximize expected risk prevented;
5. minimize city P95 cost, then expected cost/ETA;
6. break ties by stable district ID.

If complete Mandatory-now coverage is infeasible, retain the best compliant
plan, show uncovered counts/reasons and return `SAFETY_CAPACITY_BREACH`.

### FR-6 — Mode behavior

**Current operations**

- read history/current and snapshot-matched cloud predictions only;
- expose Refresh, Activate and Continue; no Start/Pause/Advance/Reset clock;
- Activate creates a deterministic simulated control receipt, projected
  intervention/no-action comparison and BigQuery simulated audit;
- never present the projected result as observed actual.

**Accelerated Production**

- restore the selected durable BigQuery/GCS simulation checkpoint;
- expose Start, Pause, Advance 15 min, Reset and speed controls;
- Activate schedules exact-tick controls on simulated actual only;
- shadow remains no-action;
- handle heartbeat/manual advance duplication idempotently.

### FR-7 — One visualization

Both modes render the same:

- all-ten-district city table;
- heat map and heat provenance;
- severity/future-safety/opportunity ranks;
- Mandatory now, projected +60/+120, watchlist and expected crossers;
- preventive waves and expected mandatory after plan;
- horizon baseline/prevented/residual risk;
- best actionable window;
- expected/P95 cost, cap utilization and breach status;
- selected/deferred/unavailable reason.

The dropdown changes only the district detail panel.

### FR-8 — Audit and Copilot truth

- Store simulated choices with deterministic IDs and
  `dispatch_status=NOT_APPLICABLE`.
- Do not import/call a Pub/Sub dispatcher or real platform command.
- Copilot must explain the authoritative `PredictiveCityPlan`; if it cannot
  consume that contract, disable its independent ranking for these modes.

## Public Contracts

```text
CurrentForecastInput
  snapshot_id
  observed_at
  zones[10]
  current_driver_features
  current_bqml_predictions
  demand_forecast
  heat_provenance

AcceleratedForecastInput
  simulation_run_id
  tick_id
  tick_index
  simulation_time
  checkpoint_checksum
  scenario_version
  zones[10]

ForecastHorizon
  minutes_ahead: 0 | 60 | 120
  heat_index_c
  heat_provenance
  demand_median
  demand_upper
  mandatory_count
  projected_mandatory_count
  watchlist_count
  expected_crossers
  baseline_risk
  residual_risk

PredictiveZonePlanRow
  zone
  horizons
  current_raw_risk
  expected_risk_prevented
  best_window
  preventive_pauses
  severity_rank
  future_safety_rank
  opportunity_rank
  portfolio_status
  portfolio_reason
  path_costs[64]

PredictiveCityPlan
  portfolio_id
  mode
  rows[10]
  selected_zone_ids
  expected_cost_vnd
  p95_reserved_cost_vnd
  budget_cap_vnd
  status: READY | SAFETY_CAPACITY_BREACH | EVIDENCE_UNAVAILABLE
  evidence_lineage
  forecast_version
  created_at
  expires_at

SimulatedControlReceipt
  portfolio_id
  evidence_lineage
  selected_proposal_checksums
  controls
  status:
    SIMULATED_QUEUED | SIMULATED_PROJECTED | SIMULATED_APPLIED |
    CONTINUED | STALE_PLAN | FAILED
  dispatch_status: NOT_APPLICABLE
```

Service boundary:

```text
load_current_forecast_input(...) -> CurrentForecastInput
load_accelerated_forecast_input(...) -> AcceleratedForecastInput
forecast_city(input, horizons=(0, 60, 120), paths=64) -> CityForecast
build_predictive_city_plan(city_forecast, constraints) -> PredictiveCityPlan
activate_simulated_plan(plan, mode) -> SimulatedControlReceipt
render_operational_workspace(view_model, mode_capabilities)
```

`PredictiveCityPlan.selected_zone_ids` is authoritative. Any fixed
`ProductionWindow.selected_zone_ids` remains fixture metadata only and must not
drive business selection.

## Execution Brief

### Phase 1 — Evidence, heat and projection — 🔨 CODE DONE

**What happens:** Add the two evidence adapters, zone heat provenance and the
lightweight 64-path current/+60/+120 projection.

**Integration points:** BigQuery repository, BQML/TimesFM outputs, accelerated
scenario/checkpoint input.

**Test:** Unique-driver aggregation, prior recovery, projected threshold,
zone-specific heat, no-future-leakage and deterministic path tests.

**Done when:** Both adapters create the same normalized forecast contract for all
ten districts without Current reading future fixtures.

**Execution evidence (26-07-26):**

- Added immutable Current/Accelerated evidence, heat, demand, driver and
  projection contracts.
- Added one exact-snapshot BigQuery feature batch and explicit snapshot-mode
  failure.
- Added `CurrentEvidenceAdapter` behavior through
  `build_current_forecast_input`; a spy proves it never reads scenario weather.
- Added explicit `AcceleratedEvidenceAdapter` behavior through
  `build_accelerated_forecast_input`; input state remains unchanged.
- Added 64 deterministic common paths, current/+60/+120 projection, mandatory,
  projected-mandatory, watchlist, expected-crossers and empirical online
  continuation.
- Added observed/modeled/simulated heat provenance and versioned Hanoi
  microclimate fallback for identical city-station values.
- Focused: `7/7` tests passed.
- Full regression: `230/230` tests passed in `585.823s`.
- `compileall`, `pip check` and `git diff --check` passed.
- Phase remains `CODE DONE`, not `VERIFIED`, until later phases connect the
  contract to the operational UI/runtime.

### Phase 2 — Preventive windows and city portfolio — 🔨 CODE DONE

**What happens:** Evaluate `0/15/30/45` starts, calculate aligned path costs and
select the safety-first portfolio under the city P95 cap.

**Integration points:** Existing per-zone SafePause proposal generator and
`PauseControl` policy.

**Test:** Early-before-peak selection, city-cost-before-P95, no fixed top-three,
breach status and stable ranking tests.

**Done when:** Every district has an explicit result/reason and the selected
portfolio is deterministic and cap-compliant.

**Execution evidence (26-07-26):**
- Extended the existing SafePause proposal generator to evaluate genuine
  `0/15/30/45` start windows while retaining its cost, fulfillment and ETA
  guardrails.
- Added projected-driver action evidence, per-driver horizon projections,
  preventive-priority assignment and `PredictiveCityPlan`/zone/window
  contracts shared by Current and Accelerated inputs.
- Added 64 aligned district cost paths and exhaustive selection across at most
  `2^10` district subsets. City P95 is calculated only after path-wise district
  aggregation.
- Added stable severity/future-safety/opportunity ranks and explicit
  `SELECTED`, `DEFERRED`, `NO_ACTION` or `UNAVAILABLE` reasons for all ten
  districts. The new planner has no fixed top-three rule.
- Focused Phase 1+2: `13/13` tests passed.
- Core decision plus Phase 1+2: `49/49` tests passed.
- Full regression: `236/236` tests passed in `583.558s`.
- `compileall`, `pip check` and `git diff --check` passed.
- Phase remains `CODE DONE`, not `VERIFIED`, until Phase 3/4 wire the contract
  into operational activation and the shared UI.

### Phase 3 — Cloud runtime and interval correctness — 🔨 CODE DONE

**What happens:** Replace accelerated session-local authority with the existing
BigQuery/GCS simulation lineage, add Current projected activation, and share one
simulated control receipt contract.

**Integration points:** `BigQuerySimulationRepository`, checkpoint store,
simulated BigQuery audit and Cloud Run session cache.

**Test:** Exact 15-minute boundaries, duplicate Activate, heartbeat/manual
advance collision, stale snapshot, sequential catch-up, mode isolation and
dispatch-boundary spy.

**Done when:** Current produces only projected outcomes; Accelerated persists
actual/shadow tick progression; neither relies on local RAM as authority.

**Execution evidence (26-07-26):**
- Added one `SimulatedControlReceipt` contract for Current projected activation,
  Accelerated queued activation, Continue, stale and failed outcomes. Every
  receipt remains `dispatch_status=NOT_APPLICABLE`.
- Current Activate records only idempotent simulated audit evidence and returns
  intervention/no-action projections; it never creates a simulation clock or
  queues accelerated controls.
- Added a durable accelerated runtime over the existing repository lease,
  checkpoint, scoring and frozen-control boundaries. Actual state comes from
  the durable run; shadow is the deterministic no-control replay of the same
  run/tick.
- Added `refresh_status()` so the BigQuery adapter discards Cloud Run process
  cache before decisions/advances and reloads the durable run/tick cursor and
  controls.
- Added caller-observed `expected_tick_index`: duplicate heartbeat/manual calls
  for the same cursor become `NO_OP_ALREADY_ADVANCED`; gaps, off-boundary ticks
  and stale lineage fail before publication.
- Added atomic city control queueing: validate every selected district proposal
  first, then commit all control events in one BigQuery transaction.
- Focused Phase 3 runtime: `8/8` tests passed.
- Runtime plus control contract: `16/16` tests passed.
- Repository/decision/forecast/runtime regression: `80/80` tests passed.
- Full regression: `246/246` tests passed in `636.375s`.
- `compileall`, `pip check` and `git diff --check` passed.
- Phase remains `CODE DONE`, not live UI proof: the existing
  `ProductionSession` compatibility renderer is retained until Phase 4 routes
  both operational modes through the new shared runtime/view path.

### Phase 4 — Shared UI and regression proof

**What happens:** Route both modes through one view model/renderer and expose all
district forecast, rank, cost, reason and provenance fields.

**Integration points:** `app.py`, operational controls, evidence tabs, map and
Copilot.

**Test:** Streamlit AppTest asserts exactly ten rows and the same required
columns/status vocabulary in both modes; manual screenshots verify heat color
and portfolio marker separation.

**Done when:** The user can operate both modes in the same visualization and
confirm the expected interval/control behavior.

#### Execution update — 26-07-2026

- Added one `CityPlannerView`/renderer for both modes. It always shows all ten
  districts with the same 19 planning columns, map, rank detail, cost/P95 and
  portfolio reason contract.
- Map fill now comes only from the displayed Heat Index and its provenance.
  Green outlines carry portfolio membership, so heat severity and intervention
  state cannot conflict.
- Current builds a plan only from exact snapshot-matched BigQuery current
  features, predictions and demand. If those inputs are absent (including the
  local snapshot demo), it renders all ten districts as explicit
  `EVIDENCE_UNAVAILABLE` monitoring-only rows; it never reads an accelerated
  fixture or future actuals.
- Accelerated builds the same `PredictiveCityPlan` directly from its current
  tick. The shared action bar consumes its selected city portfolio; legacy
  fixed `selected_zone_ids` no longer determine SafePause controls or map
  emphasis.
- Copilot now explains the authoritative city plan and does not independently
  rank districts.
- AppTest proves both modes expose exactly ten rows with identical required
  columns and the common `SELECTED`/`DEFERRED`/`UNAVAILABLE` vocabulary. The
  focused production test proves controls come from the shared portfolio.
- Hardened the Accelerated Start rerun: `Playback speed` now defaults to
  3 seconds/tick and an old session with no selected speed falls back to that
  cadence instead of coercing `None` with `int()`.
- Hardened Activate for the shared predictive portfolio: opaque durable tick
  IDs now use the session's authoritative numeric tick index to schedule exact
  15-minute controls; legacy `tick-<n>` proposals remain supported.
- Restarted the local Streamlit process after this module-level change. Its
  previous process retained an older imported `production_mode` function even
  though the traceback displayed new source comments; the fresh process on
  port 8501 is serving the fallback implementation.
- Local runtime proof (snapshot mode): the server returned HTTP 200; Current
  rendered ten monitoring-only `UNAVAILABLE` rows with distinct observed Heat
  Index values (33.8–49.8°C), while Accelerated rendered the same ten-row
  contract with distinct forecast Heat Index values (45.0–50.0°C) and only
  `SELECTED`/`DEFERRED` portfolio state. One manual `Advance 15 min` changed
  tick 37 to 38, i.e. exactly 15 operational minutes.
- Pending: browser screenshot proof for map heat fill versus portfolio outline
  and the user's visual confirmation of the two-mode interval behavior. The
  browser connector was unavailable in this execution environment, so no
  screenshot is claimed as evidence.

### Expected Outcome

- One cloud-first city engine and one UI across both operational modes.
- Honest Current projections without a future oracle or full-day clock.
- Durable Accelerated lineage with exact 15-minute operational time.
- Preventive all-zone scheduling, transparent safety ranks and bounded cost.
- No real dispatch and no new authentication system.

## Acceptance Criteria

1. Cloud Run executes the engine; BigQuery/GCS, not Streamlit session state, are
   authoritative for cloud evidence and accelerated lineage.
2. Both modes render exactly ten configured districts through the same city
   view contract.
3. Current uses only history/current input and never loads a future scenario or
   runs a full-day clock.
4. Current Activate returns `SIMULATED_PROJECTED`; Accelerated Activate changes
   only simulated actual from the scheduled tick.
5. One committed tick always equals 15 operational minutes; duplicate
   heartbeat/manual advance cannot create two ticks.
6. A late accelerated wall-clock refresh never skips or compresses operational
   intervals.
7. Candidate start delays are `0/15/30/45` minutes and durations are `15/30`
   minutes.
8. Every plan/control is rejected after its snapshot changes or 15-minute expiry.
9. District Heat Index values and map colors use the same values and expose
   observed/modeled/simulated provenance.
10. Mandatory now, projected mandatory, watchlist and expected crossers are
    separate metrics with the specified threshold semantics.
11. Raw risk counts one baseline probability per unique driver; prevented risk
    compares matched baseline/action paths; residual is the post-plan estimate.
12. No fixed top-three rule affects the city portfolio or map emphasis.
13. City P95 is calculated after summing selected district costs within each
    common path and never exceeds the configured cap for a `READY` plan.
14. Infeasible full safety coverage returns `SAFETY_CAPACITY_BREACH` with
    uncovered counts and reasons.
15. Changing the selected detail district does not alter the city plan.
16. Simulated audits are idempotent and always retain
    `dispatch_status=NOT_APPLICABLE`; no external dispatch is emitted.

## Implementation Checklist

- [x] Add normalized Current/Accelerated evidence and forecast contracts.
- [x] Batch-load current driver features/predictions/demand for all ten zones.
- [x] Add zone heat provenance and the shared Heat Index evidence consumed by
  the later map/view integration.
- [x] Implement deterministic lightweight 64-path projection and threshold
  cohorts.
- [x] Add actionable window evaluation for `0/15/30/45` minute starts.
- [x] Implement aligned-path city cost and exhaustive safety-first portfolio
  selection.
- [x] Remove fixed top-three selection from production business logic.
- [x] Add cloud-backed accelerated runtime adapter and keep RAM cache
  non-authoritative.
- [x] Add Current projected Activate/Continue and shared simulated receipt.
- [x] Enforce tick/plan idempotency, expiry and sequential no-skip behavior.
- [x] Route both modes through one all-zone view model/renderer.
- [x] Route Copilot to the shared plan or disable its independent rank.
- [x] Add focused AppTest/production tests and run local regression commands.
- [x] Run two-mode local runtime proof for the shared view and 15-minute advance.
- [ ] Capture browser screenshots and obtain user visual sign-off.

## Touchpoints

| File | Planned change |
|---|---|
| `heatsafe/models.py` | Forecast, city-plan, lineage and simulated receipt contracts |
| `heatsafe/repository.py` | Batch current features/predictions/demand and heat provenance |
| `heatsafe/services/preventive_planning.py` | New projection, windows, ranks and portfolio selector |
| `heatsafe/services/decision_service.py` | Build one all-zone plan instead of independent selected-zone/top-three flow |
| `heatsafe/services/__init__.py` | Export shared planning service |
| `heatsafe/ai_decision.py` | Align projected mandatory and executable delay semantics |
| `heatsafe/production_mode.py` | Replace pinned local authority with accelerated cloud adapter; keep compatibility surface |
| `heatsafe/simulation/repository.py` | Reuse durable tick/cursor/checkpoint and idempotent control semantics |
| `heatsafe/audit.py` | Store simulated portfolio evidence without dispatch |
| `heatsafe/operational_runtime.py` | New Current/Accelerated activation adapter and mode capabilities |
| `app.py` | One planning/view path with mode-specific controls |
| `heatsafe/ui/decision_workspace.py` | Shared Activate/Continue and plan status |
| `heatsafe/ui/evidence_tabs.py` | All-zone horizons, ranks, costs and reasons |
| `heatsafe/ui/production_mode.py` | Accelerated clock controls only; no separate reduced visualization |
| `heatsafe/ui/__init__.py` | Export shared renderer/runtime UI |
| `heatsafe/copilot.py` | Consume shared plan or disable independent ranking |
| `scripts/build_production_window.py` | Remove fixed selected-zone business assumption |
| `tests/test_preventive_planning.py` | Projection/window/portfolio/quantile tests |
| `tests/test_operational_runtime.py` | Mode, interval, expiry, idempotency and cloud-boundary tests |
| `tests/test_production_mode.py` | Durable accelerated compatibility and clock tests |
| `tests/test_refinement.py` | Heat/risk/rank semantics |
| `tests/test_app.py` | Same-UI all-ten-zone AppTest coverage |

No provisioning, schema, IAM, scheduler or deployment file is authorized in
this slice.

## Blast Radius

- **Decision semantics:** fixed top-three becomes all-zone preventive portfolio.
- **Risk semantics:** current raw risk is separated from projected screening and
  residual risk.
- **Simulation:** Accelerated authority moves from pinned session-local state to
  existing BigQuery/GCS lineage.
- **Current mode:** remains observation-driven but gains a real projected
  activation comparison.
- **UI:** one renderer; map heat and intervention status are separated.
- **Cloud:** existing BigQuery/GCS/Cloud Run contracts are reused; no resource or
  IAM expansion.

Rollback is code-only: retain existing tables/resources and restore the current
selected-zone flow plus pinned accelerated window. No data migration is planned.

## Verification Evidence

| Gate / Scenario | Strategy | Proves SPEC criterion |
|---|---|---|
| Current adapter spy forbids scenario/future-state loaders | Fully-Automated | AC3 |
| Exactly ten coherent zone rows | Fully-Automated | AC2 |
| Same displayed Heat Index drives map fill | Fully-Automated + Agent-Probe | AC9 |
| Unique driver has multiple action rows | Fully-Automated | AC11 |
| Prior 15-minute recovery resets continuous exposure | Fully-Automated | AC10 |
| Crossing probabilities produce mandatory/watchlist/expected counts | Fully-Automated | AC10 |
| Common 64 path IDs survive district reordering | Fully-Automated | AC13 |
| City costs are summed before nearest-rank P95 index 60 | Fully-Automated | AC13 |
| Ten-zone subset contains no fixed top-three filter | Fully-Automated | AC12 |
| Safety coverage exceeds budget | Fully-Automated | AC14 |
| Tick begins off a 15-minute boundary | Fully-Automated rejection test | AC5 |
| Heartbeat and manual advance target the same tick | Fully-Automated | AC5 |
| Delayed wall-clock refresh | Fully-Automated | AC6 |
| Stale snapshot/expired plan Activate | Fully-Automated | AC8 |
| Current Activate double-click | Fully-Automated | AC4, AC16 |
| Accelerated actual/shadow after scheduled start | Fully-Automated | AC4, AC7 |
| Mode switch retains isolated state | Fully-Automated | AC2, AC4 |
| Dispatch/PubSub spy remains unused | Fully-Automated | AC16 |
| Both modes render the same required columns | Hybrid AppTest + screenshots | AC2, AC15 |

Execution test commands:

```bash
venv/bin/python -m unittest -v \
  tests.test_preventive_planning \
  tests.test_operational_runtime \
  tests.test_production_mode \
  tests.test_refinement \
  tests.test_app
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m compileall -q app.py heatsafe tests
venv/bin/python -m pip check
git diff --check
```

Cloud runtime verification is a separate, explicitly authorized step. Local
green tests prove adapter/contracts, not the currently deployed Cloud Run
revision.

## Test Infra Improvement Notes

- Add only the two focused modules listed above.
- Reuse existing deterministic scenario/checkpoint builders and Streamlit
  AppTest.
- Use repository spies for no-future-leakage and no-dispatch assertions.
- Do not add Playwright, a cloud emulator or a new test framework.

## Risks and Controls

| Risk | Control |
|---|---|
| Current projection is mistaken for observed reality | Always label `SIMULATED_PROJECTED`; never overwrite observed state |
| Cloud Run RAM is mistaken for durable authority | BigQuery cursor/source and GCS checkpoint remain authoritative |
| Wall-clock playback drifts | Promise only exact operational ticks; show lag and never skip ticks |
| Heat differences appear fabricated | Expose per-zone observed/modeled/simulated provenance |
| Forecast ensemble is slow | Use lightweight feature projection, common paths and bounded cache; no full engine rollout ensemble |
| P95 is presented as an absolute guarantee | Show expected/P95 separately and disclose tail risk |
| One proposal per district misses a better global assignment | State bounded P0 limitation and show breach honestly |
| Current silently falls back to replay data | Fail closed and test provider-unavailable behavior |
| Copilot contradicts the city plan | Consume the shared plan or disable independent ranking |
| Public demo creates real commands | Audit-only simulated receipt; `NOT_APPLICABLE`; dispatch spy |

## Integrations

- Existing BigQuery current snapshot, driver feature/prediction, demand forecast
  and intervention audit tables.
- Existing BigQuery ML risk classifier and TimesFM demand forecast.
- Existing `BigQuerySimulationRepository`, simulation tick lineage and
  GCS/checkpoint support.
- Existing `PauseControl` timing and recovery rules.
- Existing Cloud Run service identity.

No new external integration is required.

## Change Management

- **Modified:** two modes share product contracts/UI, not identical evidence
  sources or clock behavior.
- **Removed:** local-only authority, Current full-day clock, fixed top-three
  business selection and hard-coded `K-4`.
- **Added:** preventive forecast cohorts, all-zone portfolio, city P95,
  cloud-backed accelerated state and explicit interval guarantees.
- **Scope guard:** if execution requires a schema migration, new IAM, scheduler,
  deployment or real dispatch, stop and amend the plan before proceeding.

## Cursor / RIPER Guidance

- Use this file as the sole execution anchor.
- Execute Phases 1–4 sequentially with one writer; preserve unrelated dirty
  worktree changes.
- Perform the focused source/test check at each phase boundary. Stop only for a
  material contract deviation or an external cloud write requiring authority.
- Do not commit or deploy unless the user separately authorizes it.
- Next instruction: enter EXECUTE at Phase 4 using the Implementation Checklist.

## Phase Completion Rules

A phase is not complete until:

1. its focused integration tests pass;
2. state/data lineage is inspected;
3. error behavior is exercised;
4. visible behavior is manually checked when the phase affects UI;
5. final two-mode behavior is confirmed by the user before marking the whole
   plan `✅ VERIFIED`.

Status meanings:

- ⏳ PLANNED — not started
- 🔨 CODE DONE — written, not proven end-to-end
- 🧪 TESTING — verification in progress
- ✅ VERIFIED — tested and user-confirmed
- 🚧 BLOCKED — cannot continue without changing the contract

## Resume and Execution Handoff

- **Selected plan:** this file.
- **Last completed step:** Phase 3 code and automated verification completed;
  Phase 4 not started.
- **Validate-contract status:** skipped by explicit user request on 26-07-26.
- **Supporting context loaded:** current app routing, accelerated
  `ProductionSession`, simulation timing/control/repository code, audit store,
  decision service, README cloud architecture and current unittest surface.
- **Next step:** start Phase 4 shared-UI and regression-proof work only
  when requested; do not re-run VALIDATE.
- **Execution boundary:** code and local verification only. Cloud deployment or
  live GCP mutation requires a separate explicit instruction.

## Validate Contract

**Status:** SKIPPED

The user explicitly requested that this plan be adjusted without running
VALIDATE. EXECUTE should use the acceptance criteria and verification table in
this file directly. Do not add an auth/IAM/sandbox phase unless scope changes to
real dispatch or a non-demo production system.
