# HeatSafe Operator-First UI V2 Implementation Plan

**Date**: 28-07-26
**Status**: ✅ IMPLEMENTED — automated acceptance complete; browser visual sign-off pending
**Complexity**: COMPLEX UI RESTRUCTURE — one execution stream with visual and performance gates
**Selected plan**: `process/general-plans/active/heatsafe-operator-ui-v2_28-07-26/heatsafe-operator-ui-v2_PLAN_28-07-26.md`
**Planning method**: repository general-plan format plus local Streamlit/UI-UX guidance. The requested `adas-generate-plan` skill is not installed in this workspace, so this plan uses the closest available project conventions.

> **TL;DR:** Replace the current data-first Streamlit page with a minimal operator
> console centered on two questions: **“Do drivers need protection now?”** and
> **“Why is this plan the best safe trade-off?”** The default Operations surface shows
> only three city KPIs, one Hanoi heat map, one selected-area decision card, one
> recommendation, and one action above fold. Directly below it, one bounded
> `Why this plan` chart slot lets the operator switch between Timing, Trade-offs,
> Stress test, and post-decision Outcome; only one chart renders at a time. The UI does
> not show tick/K terminology, snapshot IDs, checksums, model lineage, district-count
> KPIs, ranking columns, or raw model probabilities. Operational time is displayed as
> Hanoi clock time. Tables move to Evidence & history. The optimizer gains additive,
> read-only diagnostics for timing options, top alternatives, and a bounded city
> portfolio frontier; its selection policy, guardrails, simulation, lineage, and
> fail-closed behavior remain unchanged.

## Quick Links

- [1. Operator Context and Jobs](#1-operator-context-and-jobs)
- [2. Product Scope](#2-product-scope)
- [3. Operator Vocabulary Contract](#3-operator-vocabulary-contract)
- [4. Information and Density Budget](#4-information-and-density-budget)
- [5. Target Experience](#5-target-experience)
- [Why This Plan and Scenario Story](#57-why-this-plan--optimization-and-scenario-story)
- [6. Functional Requirements](#6-functional-requirements)
- [7. UI Architecture](#7-ui-architecture)
- [8. Performance and Rerun Plan](#8-performance-and-rerun-plan)
- [9. Delivery Phases](#9-delivery-phases)
- [Acceptance Criteria](#acceptance-criteria)
- [Implementation Checklist](#implementation-checklist)
- [Touchpoints](#touchpoints)
- [Blast Radius](#blast-radius)
- [Verification Evidence](#verification-evidence)
- [Risks and Controls](#risks-and-controls)
- [Resume and Execution Handoff](#resume-and-execution-handoff)
- [Validate Contract](#validate-contract)

## 1. Operator Context and Jobs

### 1.1 Primary user

The primary user is a Hanoi operations coordinator monitoring heat exposure and
service conditions. They are not expected to understand simulation internals,
model lineage, optimizer vocabulary, or replay implementation details.

The UI must assume the operator has limited attention and is simultaneously
monitoring other operational channels. It must optimize for recognition, not
analysis of raw model output.

### 1.2 Top operator jobs

The default surface must help the operator complete five jobs in order:

1. **Recognize urgency:** Are drivers currently reaching the safety threshold?
2. **Locate the issue:** Which areas require attention?
3. **Understand the recommendation:** Who should take a break, when, and for how long?
4. **Check operational safety:** Does the plan stay within service and budget limits?
5. **Choose and observe:** Activate SafePause or continue, then see the effect.

Anything that does not support one of these jobs is secondary evidence and must
not occupy the default Operations surface.

### 1.3 Decisions the UI must support

The UI supports exactly these operator decisions:

- select an area for inspection;
- apply operational limits;
- start, pause, or step through the demonstration;
- activate the authoritative SafePause plan;
- continue without intervention;
- inspect supporting evidence when needed;
- reset or refresh the scenario.

It does not ask the operator to interpret ranks, choose model versions, compare
prediction runs, understand snapshot lineage, or reason about internal tick IDs.

### 1.4 Design principles

1. **One surface, one decision.** The default surface is for current operations and action.
2. **Recognition before detail.** Map, plain-language summary, and pass/fail guardrails come before tables.
3. **Three critical numbers maximum.** Additional metrics belong in context panels or evidence.
4. **Two columns maximum.** Avoid nested grids and dashboard-card walls.
5. **No unexplained technical language.** Internal terms may exist in tooltips or evidence only.
6. **Time means clock time.** Display `Asia/Ho_Chi_Minh` time, not tick/K notation.
7. **Status is actionable.** Every unavailable or blocked state explains what the operator can do next.
8. **Stable layout over decorative animation.** Avoid movement that competes with operational changes.
9. **Fail closed without looking broken.** Monitoring remains useful when recommendations are unavailable.
10. **Explain optimization through comparisons.** Show the selected plan against meaningful alternatives and stress cases, not raw model internals.

## 2. Product Scope

### In scope

- Full restructuring of `app.py` composition and the active Streamlit UI.
- One default `Operations` surface and one secondary `Evidence & history` surface.
- Persistent control sidebar for mode, limits, playback, refresh, and advanced metadata.
- Three city-level KPI cards only.
- Primary Hanoi heat map with area selection and a short top-priority list.
- One compact selected-area decision panel containing recommendation, key impacts, guardrails, and action.
- Clock-time presentation for current time, recommendation time, wave start time, and playback range.
- One `Why this plan` chart slot with Timing, Trade-offs, Stress test, and conditional Outcome views.
- Read-only optimization diagnostics: up to four start-time options, a bounded set of plan alternatives, a bounded Pareto-style city portfolio frontier, and counts of evaluated/feasible combinations.
- Explicit Expected demand, High-demand stress, Wait, No action, and Tight-budget stories using existing evidence/constraints where available.
- Post-decision `With SafePause` versus `Without SafePause` outcome comparison.
- Strict table row/column budgets and default column contracts.
- Plain-language empty, loading, stale, unavailable, no-feasible, pending, and completed states.
- Fragment/dynamic rendering work needed to avoid unnecessary full-page reruns.
- Streamlit AppTest updates and browser visual verification.
- Preservation of all existing decision, simulation, audit, and fail-closed behavior.

### Out of scope

- Migration to React, NiceGUI, Reflex, or another UI framework.
- Changes to the SafePause optimizer, guardrails, model scoring, or simulation semantics.
- New real dispatch, notification, payroll, or driver-facing integration.
- New authentication or role model.
- Changing the authoritative ten-district planning scope.
- Displaying medical diagnosis or causal incident-prevention claims.
- Replacing BigQuery/GCS/Vertex integrations.
- Making model or simulation internals editable from the UI.
- Changing the optimizer objective, candidate generation, guardrail thresholds, or selected result to make a chart look better.
- Sending unbounded candidate portfolios, driver lists, or 64-path cost arrays to the browser.
- Adding decorative gauges, radar charts, ticker tapes, or dashboard-card grids.

### Constraints

- Current domain and safety tests remain authoritative.
- Internal tick, snapshot, checksum, run, model, and prediction lineage must remain available to code and audit even when hidden from the default UI.
- All displayed operational times use `Asia/Ho_Chi_Minh` and include the date when a window crosses a day boundary.
- Color is never the only status indicator.
- The app remains explicit that it is synthetic and sends no real dispatch.
- Production and Accelerated Production continue to share one decision renderer.

## 3. Operator Vocabulary Contract

This contract is part of the UI acceptance criteria. Internal terms may not leak
onto the default Operations surface unless explicitly permitted below.

| Internal/domain term | Default operator copy | Placement rule |
|---|---|---|
| `current`, `PRODUCTION` | `Current plan` | Mode control and small header status |
| `accelerated-production` | `Simulation playback` | Mode control; keep simulation disclosure visible |
| Tick 37 / tick index | `09:15` or corresponding Hanoi time | Never show tick index on Operations |
| `K=45`, decision tick | `Decision available at 11:15` | Never show `K` |
| `K-8` / `K+8` | Actual start/end clock times | Never show K-relative notation |
| `snapshot_id` | `Updated at 11:15` | Full ID only in Advanced system details |
| replay/run/checkpoint ID | `Simulation session` | Identifier only in Advanced system details |
| checksum | No default copy | Evidence/debug only |
| `mandatory now` | `Drivers needing a break now` | KPI and area detail |
| projected mandatory +120m | `Expected to need protection by 13:15` | Area detail/outlook only |
| expected crossers | `Likely to reach the safety limit` | Round for operator display; raw value in evidence |
| `SafePauseProposal` | `SafePause break plan` | Recommendation card |
| selected drivers | `Drivers included` | Recommendation card |
| mandatory coverage | `Safety coverage` | Show `28 of 28` plus `All covered` |
| selected district count | No city KPI | Areas are visible on the map; do not show count |
| selected/deferred/unavailable | `Included` / `Watch` / `Data unavailable` | Map/list/detail status |
| severity/future/opportunity rank | No default copy | Evidence only if still needed for debugging |
| raw/baseline/residual risk | `Risk level` or `Expected risk reduction` | No raw probability on Operations |
| P95 reserve | `Budget reserved for a high-demand case` | Tooltip may explain estimate; never label `P95` by default |
| net platform cost | `Estimated plan cost` | Recommendation card |
| fulfillment degradation | `Orders completed` impact | Plain percentage-point impact and limit |
| ETA increase | `Expected pickup delay` | Minutes and pass/fail limit |
| actual branch | `With SafePause` | Outcome comparison |
| shadow branch | `Without SafePause` | Outcome comparison |
| `MODEL_UNAVAILABLE` | `Recommendation temporarily unavailable` | Explain that monitoring continues |
| `NO_FEASIBLE` | `No safe plan fits the current limits` | List the limiting constraints and next action |
| stale/mismatched evidence | `Data is updating; action is paused` | Do not expose lineage exception text by default |
| model evaluation | `System quality checks` | Secondary evidence only |
| Copilot | `Why this plan` | Plain-language explanation, not a top-level tab |
| candidate / portfolio | `Plan option` | Never show optimizer implementation terms |
| median demand | `Expected demand` | Timing and Stress test views |
| upper demand / stress | `High-demand case` | Avoid P90/P95 terminology on Operations |
| start delay 0/15/30/45 | Actual start time | Timing view |
| feasible / violation | `Within all limits` / `Blocked by …` | Trade-off and Stress test views |

### Time formatting rules

- Primary time format: `HH:mm`, e.g. `11:15`.
- Add `d MMM` only when needed, e.g. `28 Jul · 23:45`.
- Relative forecast copy uses both plain duration and endpoint when useful:
  `Next 2 hours · through 13:15`.
- Playback step label: `Next 15 min`, not `Advance tick`.
- Playback range: `09:15–13:15`, not `K-8–K+8`.
- Playback speed labels: `Slow`, `Normal`, `Fast`; a help tooltip may disclose the demo cadence.

## 4. Information and Density Budget

### 4.1 Surface budget

There are only two user-facing surfaces:

1. **Operations** — default; current situation, recommendation, action, and outcome.
2. **Evidence & history** — optional; supporting area, driver, audit, and system detail.

Do not create nested top-level tabs. Evidence uses expanders or one segmented
sub-view at a time.

### 4.2 Above-the-fold budget

Target viewport: `1440 × 900`.

Above the fold may contain only:

- one compact header/status strip;
- three KPI cards;
- one map;
- one short priority list with at most three areas;
- one selected-area decision card;
- one recommendation;
- up to four guardrail rows;
- up to two action buttons;
- one compact playback/time strip in Simulation playback mode.

It must not contain:

- a data table;
- model quality metrics;
- audit rows;
- snapshot/run/checkpoint/checksum values;
- rank columns;
- multiple recommendation alternatives;
- more than one chart in addition to the map;
- duplicated city and selected-area summaries.

### 4.3 KPI budget

Exactly three KPI cards on Operations:

1. **Drivers needing a break now** — city total.
2. **Safety coverage** — `covered / total`, with `All covered` or `N still uncovered`.
3. **Budget remaining after this plan** — cap minus high-demand reserve.

Do not show these as KPI cards:

- number of areas selected;
- plan status;
- snapshot ID;
- tick/time index;
- active driver count;
- expected crossers as a decimal;
- model version;
- risk rank;
- P95 terminology.

Contextual values such as Heat Index and active drivers belong in the selected-area
panel, not the city KPI strip.

### 4.4 Layout budget

- Maximum top-level columns: **2**.
- Primary desktop split: map/priority `~65%`, decision panel `~35%`.
- Maximum nested columns inside decision card: **2**, only for short paired values.
- Maximum simultaneously visible bordered panels above fold: **5**, including KPI cards as one logical group.
- At widths below `1100px`, stack map before decision card.
- No horizontal KPI row with more than three cards.
- No six-column or card-grid layouts.

### 4.5 Chart budget

Operations uses:

- one geographic map above fold;
- one `Why this plan` chart slot below the decision area;
- exactly one active chart inside that slot at a time.

The chart selector may expose at most four views:

1. `Timing` — default before action;
2. `Trade-offs` — selected plan versus bounded alternatives;
3. `Stress test` — Expected demand versus High-demand stress;
4. `Outcome` — only after Activate or Continue produces comparable histories.

Do not render four charts simultaneously. Switching the selector must replace the chart
in the same stable-height container and compute only the selected view.

Remove the current severity-versus-preventable-risk scatter plot. Replace it with the
operator-facing trade-off frontier defined in section 5.8; raw city risk is not an
operator axis.

Remove the default city-wide grouped mandatory cohort chart. Its operator insight is
covered by the KPI strip and Timing view. It may remain under Advanced evidence only.

### 4.6 Table contracts

No table is rendered on the default Operations surface.

#### Area overview table

- Surface: Evidence & history.
- Visible rows: all 10 configured districts; no pagination needed.
- Default columns: maximum **6**.

| Column | Operator label |
|---|---|
| District | `Area` |
| Heat tier/index | `Heat` |
| Mandatory now | `Need a break now` |
| Projected mandatory +120m | `By 13:15` or current endpoint time |
| Best window | `Recommended start` |
| Portfolio status | `Plan status` |

Do not show by default:

- heat source;
- watchlist;
- expected crossers decimal;
- severity/future/opportunity ranks;
- raw, prevented, or residual risk decimals;
- expected/P95 cost by district;
- verbose reason text.

Reason appears in a selected-row detail or tooltip. The complete 19-column dataset
may be downloadable or available under an `Advanced data` expander, but it is not the
default table.

#### Driver table

- Surface: Evidence & history → Drivers.
- Visible rows: maximum **20** at once; the dataframe may scroll for additional rows.
- Default sort: safety priority, then exposure duration.
- Default columns: maximum **6**.

| Column | Operator label |
|---|---|
| Masked driver ID | `Driver` |
| Priority | `Why included` |
| Exposure | `Heat exposure` |
| Risk category | `Risk level` |
| Start delay converted to time | `Break starts` |
| Pause duration | `Break length` |

Do not show raw baseline/action probabilities, wait-cost decimals, full factor arrays,
or internal hashes by default. Put them in row detail/advanced evidence if required.

#### Audit table

- Surface: Evidence & history → History.
- Visible rows: latest **10**.
- Default columns: maximum **5**.

| Column | Operator label |
|---|---|
| Recorded time | `Time` |
| Choice | `Action` |
| Protected driver count | `Drivers` |
| Result/status | `Result` |
| Area summary | `Coverage` |

Do not show checksum, proposal ID, intervention ID, snapshot ID, model version, or
prediction run in the default audit table.

#### Alternatives table

Only when no feasible plan exists:

- maximum **3** alternatives;
- maximum **4** columns: `Drivers`, `Break length`, `Estimated cost`, `Why blocked`.

### 4.7 Copy budget

- Header subtitle: maximum one line.
- Recommendation headline: maximum 70 characters.
- Recommendation explanation: maximum two short sentences.
- Guardrail label: maximum 28 characters.
- Empty-state body: maximum three bullets.
- Operator action labels: verbs first.
- No paragraph longer than three lines on the Operations surface.

## 5. Target Experience

### 5.1 Operations surface wireframe

```text
┌───────────────────────────────────────────────────────────────────────┐
│ HeatSafe AI Ops    Current plan ready    11:15    Updated just now   │
│ Synthetic Hanoi operations · No real dispatch                        │
├───────────────────────────────────────────────────────────────────────┤
│  68 need a break now  │  68/68 covered  │  $116 budget remaining    │
├───────────────────────────────────────────┬───────────────────────────┤
│                                           │ HAI BÀ TRƯNG             │
│                                           │ High heat · 42.8°C       │
│              HANOI HEAT MAP               │ 28 need a break now      │
│                                           │                           │
│                                           │ Protect 28 drivers        │
│  Priority areas                           │ starting at 11:30         │
│  1. Hai Bà Trưng · High                   │                           │
│  2. Đống Đa · High                        │ ✓ All covered             │
│  3. Cầu Giấy · Watch                      │ ✓ Orders within limit     │
│                                           │ ✓ Pickup delay +0.8 min   │
│                                           │ ✓ Cost $84 of $200        │
│                                           │                           │
│                                           │ [Activate SafePause]      │
│                                           │ [Continue monitoring]     │
├───────────────────────────────────────────┴───────────────────────────┤
│ Why this plan                                                        │
│ [Timing] [Trade-offs] [Stress test] [Outcome after decision]         │
│ One chart: selected plan highlighted, limits and alternatives shown  │
├───────────────────────────────────────────────────────────────────────┤
│ [View evidence & history]                                            │
└───────────────────────────────────────────────────────────────────────┘
```

### 5.2 Header

Display only:

- product name;
- mode (`Current plan` or `Simulation playback`);
- status (`Ready`, `Monitoring only`, `Decision needed`, `Running`, `Complete`);
- current Hanoi time;
- freshness (`Updated just now`, `Updated 6 min ago`);
- synthetic/no-dispatch disclosure.

Hide technical metadata behind `System details` in the sidebar.

### 5.3 Sidebar

The sidebar contains controls, not insight:

- mode;
- budget limit;
- support per driver;
- selected area fallback;
- playback controls when applicable;
- refresh/reset;
- collapsed system details.

Constraints use a form and one `Apply limits` button to avoid recomputation after
every number-input change.

### 5.4 Map and priority list

The map is the primary location interface. It displays heat severity and area
selection. The priority list is limited to three areas and provides keyboard/text
access to the same selection.

Map rules:

- fill encodes heat severity;
- cyan/blue outline encodes current selection or plan inclusion;
- green is reserved for safe/pass states;
- map tooltip shows only area, heat, drivers needing a break, and plan status;
- selection updates the decision panel but never changes the authoritative city plan;
- district polygons are preferred when a licensed, validated GeoJSON asset is available;
- the existing ten-point bubble map is an acceptable first delivery slice.

### 5.5 Decision card

The selected-area decision card must answer:

1. What is happening?
2. What does HeatSafe recommend?
3. Is the recommendation within limits?
4. What can the operator do?

Recommended state example:

```text
Hai Bà Trưng
High heat · 42.8°C · 28 drivers need a break now

Protect 28 drivers starting at 11:30
3 staggered groups · 30-minute breaks

✓ All drivers at the safety limit are covered
✓ Orders completed remain within the limit
✓ Expected pickup delay: +0.8 min
✓ Estimated plan cost: $84 of $200

[Activate SafePause]
[Continue monitoring]
```

The card does not display eligible-driver denominators, model probabilities, rank,
proposal ID, P95 terminology, or verbose guardrail notes.

### 5.6 Simulation playback

Replace tick language with clock time:

```text
Simulation playback · 09:15–13:15
Now 11:00 · Recommendation available at 11:15
[Play] [Pause] [Next 15 min] [Reset]
Speed: Slow | Normal | Fast
```

The operator never sees K, K-8, K+8, tick index, or simulation checksum.

At decision time:

```text
Decision needed at 11:15
Review the SafePause plan and choose an action.
```

After the choice, compare:

- `With SafePause`;
- `Without SafePause`.

Do not use `actual` and `shadow` on the default UI.

### 5.7 Why this plan — optimization and scenario story

The system's strongest differentiator is not the risk number alone. It evaluates action
timing, coverage, pause length, staggered groups, cost, demand stress, order completion,
pickup delay, and city budget together. The UI must make this visible without exposing
optimizer internals.

Use one stable-height chart slot and a segmented selector. The chart header includes a
short proof statement such as:

```text
Compared 384 valid plan combinations.
Selected the earliest plan that covers all urgent drivers and stays within every limit.
```

The evaluated count is evidence, not a KPI. Do not claim a count unless it is emitted by
the authoritative optimization run.

#### View A — Timing

**Operator question:** Why start at 11:30 instead of now or later?

Use one Plotly figure with two vertically aligned panels sharing Hanoi clock time:

- upper panel: Expected demand and High-demand estimate over the next two hours;
- lower panel: one bar/marker for each evaluated start option (`now`, `+15`, `+30`,
  `+45` converted to clock time), showing drivers still expected to reach the safety
  limit by the horizon;
- highlight the selected start time with the action color and `Selected` annotation;
- gray out infeasible timing options and show one plain-language rejection reason on hover;
- show pause windows as subtle time bands, not a dense wave diagram.

This view demonstrates that HeatSafe can act before exposure breaches while avoiding a
high-demand window where feasible.

#### View B — Trade-offs

**Operator question:** What was sacrificed or gained versus other plans?

Use a bounded scatter plot of city portfolio options:

- x-axis: `Estimated high-demand cost` in operator currency;
- y-axis: `Heat exposure avoided` in driver-hours;
- bubble size: drivers protected, with a bounded visual range;
- selected plan: orange/action-color marker with outline and `Selected` label;
- other feasible frontier options: cyan;
- near-miss/rejected options: gray, only when they explain a real constraint;
- vertical budget limit line;
- hover: protected drivers, start time summary, worst-area pickup-delay impact,
  coverage, and plain-language rejection reason.

Do not plot every one of up to 1,024 portfolios. Materialize a deterministic maximum of
12 informative points containing:

- the selected plan;
- lowest-cost feasible plan;
- highest-protection feasible plan;
- lowest-service-impact feasible plan;
- remaining non-dominated frontier points;
- up to three closest rejected/over-budget near misses.

The chart subtitle states the safety-first ordering: urgent-driver coverage is optimized
before cost. The scatter must not imply that a cheaper plan is preferable when it leaves
urgent drivers uncovered.

#### View C — Stress test

**Operator question:** Does the selected plan remain safe when demand is higher than expected?

Use one Plotly small-multiple/bullet figure, not KPI cards, for the selected area and city
cost evidence that actually exists:

- `Safety coverage`: selected/required, same plan under both demand cases;
- `Orders completed`: No action versus SafePause under Expected and High demand;
- `Expected pickup delay`: Expected and High demand versus the configured limit;
- `Plan cost`: expected city cost and high-demand budget reserve versus budget cap.

Each row includes a limit marker and pass/fail label. Do not aggregate a city-wide
fulfillment rate unless the domain adds an authoritative aggregation. Existing per-area
proposal rates may be shown for the selected area; city-level cost and coverage come from
the city plan.

#### View D — Outcome

**Operator question:** What changed because of the decision?

This view appears only when histories exist. Use Hanoi clock time and compare:

- `With SafePause`;
- `Without SafePause`.

Default metric: city heat-exposure burden over time. A small selector may switch to risk
or service only if both branches expose coherent comparable series. Show the intervention
start time and a short summary annotation. Do not show branch checksums or internal names.

#### Required decision scenarios

The demo/implementation must make these stories reproducible:

1. **Act now versus wait:** later action overlaps more demand or leaves more drivers near
   the safety threshold; the selected timing is visibly justified.
2. **Expected versus high demand:** the selected plan still passes service, pickup-delay,
   and budget limits under the existing upper-demand stress evidence.
3. **Selected versus cheaper/stronger alternatives:** the frontier shows why a cheaper
   option protects less or why a stronger option exceeds a limit.
4. **Tight budget:** applying a lower cap visibly changes/degrades the feasible frontier,
   while the system keeps urgent-driver coverage first or fails closed with a clear reason.
5. **Activate versus continue:** post-decision histories show With SafePause diverging from
   Without SafePause using the same exogenous scenario.

No new synthetic scenario fixture is required for the first delivery. These stories use
existing start delays, median/upper demand, configurable budget, alternatives, and
actual/shadow histories. Add a new fixture only if a required story cannot be reproduced
deterministically from current evidence.

### 5.8 Optimization evidence contract

The current engine already computes useful evidence but discards part of it after
selection:

- `RecommendationResult.alternatives` retains up to five per-area options;
- `SafePauseProposal` contains expected/high-demand fulfillment, ETA, cost, coverage, and
  risk outcomes;
- `build_predictive_city_plan()` evaluates four start delays and up to `2^10` city
  portfolios, but returns only each area's best window and the selected city portfolio.

Add bounded, read-only diagnostics without changing the selection score or result:

```text
CityOptimizationEvidence
  evaluated_portfolio_count
  budget_compliant_portfolio_count
  selected_portfolio_id
  portfolio_options[<=12]
  zone_options[<=10]

PortfolioTradeoffPoint
  option_id
  label
  selected
  feasible
  selected_zone_ids
  protected_drivers
  urgent_drivers_covered
  urgent_drivers_required
  exposure_hours_avoided
  projected_drivers_at_limit_120m
  expected_cost_vnd
  high_demand_reserved_cost_vnd
  worst_area_pickup_delay_minutes
  rejection_reasons[<=2]

ZoneOptimizationOptions
  zone_id
  selected_proposal_id
  timing_options[<=4]
  proposal_alternatives[<=8]

TimingOption
  proposal_id
  start_delay_minutes
  start_time
  pause_minutes
  waves
  drivers_protected
  projected_drivers_at_limit_120m
  residual_risk_120m
  expected_cost_vnd
  high_demand_reserved_cost_vnd
  expected_fulfillment_rate
  high_demand_fulfillment_rate
  expected_pickup_delay_minutes
  high_demand_pickup_delay_minutes
  feasible
  rejection_reasons[<=2]
```

Rules:

- diagnostics are deterministic and derived during the authoritative candidate pass;
- selected result and score tuple remain unchanged;
- no driver-level decisions or 64-path arrays are copied into UI diagnostics;
- rejected points are included only to explain a real limit;
- old snapshots/plans remain loadable through optional/default-empty fields;
- view models convert risk/exposure metrics to operator-friendly labels and units;
- diagnostics are never accepted back from the browser as an action payload.

### 5.9 Evidence & history surface

This surface is opened intentionally. It contains one selected sub-view at a time:

- `Areas`;
- `Drivers`;
- `History`.

A short text summary may reference the selected plan, but the interactive `Why this plan`
charts remain on Operations and are not duplicated here. `System quality checks` and
lineage live under `Advanced system evidence`, collapsed by default.

No nested four-tab layout is permitted.

### 5.10 State designs

#### Recommendation temporarily unavailable

```text
Recommendation temporarily unavailable

City heat and driver monitoring are still available.
HeatSafe has paused action until the latest data is verified.

[Retry recommendation] [View system details]
```

#### No safe plan fits current limits

```text
No safe plan fits the current limits

The available options would exceed at least one service or budget limit.
• Expected pickup delay would exceed the configured limit.
• High-demand cost would exceed the current budget.

[Adjust limits] [Continue monitoring]
```

#### Before recommendation time

```text
Monitoring conditions
The next recommendation will be available at 11:15.
```

#### Loading

Keep the page frame stable and show the task being performed:

- `Loading current conditions…`
- `Preparing the two-hour outlook…`
- `Checking service and budget limits…`

#### Complete

```text
Simulation complete
SafePause reduced exposure while service remained within the configured limits.

[Review outcome] [Reset simulation]
```

## 6. Functional Requirements

### FR-1 — One operator-first Operations surface

Production and Simulation playback render the same map, KPI, decision, outlook, and
evidence contracts. Playback controls appear only in Simulation playback.

### FR-2 — Three KPIs only

The default surface renders exactly the three KPI cards defined in section 4.3. No
area-count or technical-status KPI may be added without updating this plan.

### FR-3 — Clock-time presentation

All operator-facing event, recommendation, wave, playback, and forecast times are
converted to Hanoi clock time. Tick/K language is absent from the default surface and
operator action copy.

### FR-4 — Plain-language recommendation

The recommendation card presents protected drivers, start time, group/wave summary,
break duration, coverage, service impact, pickup delay, and cost using the vocabulary
contract. It does not expose raw optimizer structures.

### FR-5 — Decision controls remain authoritative

Activate and Continue still operate on the exact authoritative city plan. UI
simplification must not create a second plan, re-rank areas, submit browser-edited
proposal data, or bypass existing lineage and stale-plan validation.

### FR-6 — Progressive evidence

No city, driver, audit, or model table renders until the user opens Evidence & history
and selects the corresponding sub-view. Hidden content must not perform expensive
rendering on every heartbeat.

### FR-7 — Table density limits

All default tables comply with the row/column contracts in section 4.6. Advanced/full
data remains available for judges/developers without occupying operator attention.

### FR-8 — Consistent semantic states

- Red: immediate protection need/critical condition.
- Amber: approaching limit/warning.
- Green: safe or passed guardrail.
- Cyan/blue: selection, current plan, and neutral action context.
- Gray: unavailable/unknown.

Every color state includes a text label or icon.

### FR-9 — Fail-closed remains useful

A missing recommendation never blanks the map or current monitoring information.
Activate remains disabled/absent until exact evidence is ready. The state panel explains
what remains available and what can be done next.

### FR-10 — Stable decision state

After Activate or Continue, replace pending actions with the recorded decision and
outcome state. Prevent duplicate clicks while an action is being recorded.

### FR-11 — Simulation controls use operator language

Use Play, Pause, Next 15 min, Reset, and Slow/Normal/Fast. Display the operational time
range and current time. Do not show tick counters on Operations.

### FR-12 — Synthetic disclosure

The header and confirmation dialog retain a concise disclosure that the environment is
synthetic and sends no real dispatch.

### FR-13 — Optimization must be visually explainable

The Operations surface provides one `Why this plan` chart slot. It must show the selected
plan against timing options, bounded portfolio alternatives, and stress limits using
operator language. The selected marker and constraint lines must be visible without hover.

### FR-14 — Optimization diagnostics are additive and bounded

The authoritative candidate pass emits counts and bounded summary points needed for the
charts. It must not change candidate generation, lexicographic score ordering, selected
portfolio, guardrails, or action payload. UI diagnostics contain no driver list or raw
path-cost arrays.

### FR-15 — Scenario stories are reproducible

Act-versus-wait, expected-versus-high-demand, selected-versus-alternatives, tight-budget,
and activate-versus-continue stories have deterministic test fixtures or setup steps and
browser evidence.

## 7. UI Architecture

### 7.1 Proposed module structure

Build UI v2 in parallel with the existing renderers until cutover:

```text
heatsafe/ui/operator_console/
├── __init__.py
├── shell.py
├── sidebar.py
├── operations.py
├── city_map.py
├── decision_card.py
├── decision_insights.py
├── evidence.py
├── state_panels.py
├── view_models.py
├── vocabulary.py
└── styles.py
```

### 7.2 Pure view models

`view_models.py` must not import Streamlit. Proposed contracts:

```text
OperatorConsoleView
  mode_label
  operational_time_label
  updated_label
  readiness_state
  synthetic_disclosure
  city_kpis
  map_areas
  priority_areas[<=3]
  selected_area
  recommendation
  outcome_comparison
  decision_insights
  optimization_evidence
  evidence_summary

OperatorCityKpis
  drivers_needing_break_now
  covered_drivers
  total_drivers_requiring_coverage
  budget_remaining_usd
  coverage_state

OperatorAreaView
  zone_id
  name
  heat_index_c
  heat_state_label
  drivers_needing_break_now
  expected_needing_protection_by_label
  recommended_start_label
  plan_status_label
  selected
  included_in_plan

OperatorRecommendationView
  state
  headline
  driver_count
  start_time_label
  group_summary
  break_length_label
  coverage_summary
  order_impact_summary
  pickup_delay_summary
  cost_summary
  can_activate
  blocking_reason

OperatorDecisionInsightsView
  selected_view: TIMING | TRADE_OFFS | STRESS_TEST | OUTCOME
  timing_options[<=4]
  portfolio_options[<=12]
  stress_scenarios: EXPECTED | HIGH_DEMAND
  outcome_available
  evaluated_option_label

OperatorOutcomeView
  with_safepause
  without_safepause
  exposure_delta
  risk_delta
  service_delta
  pickup_delay_delta
```

Raw domain models remain authoritative. Operator view models only transform labels,
format values, and select the approved subset of fields.

### 7.3 App entry point

Refactor `app.py` toward:

1. page configuration and styles;
2. initialize session/control state;
3. load/build authoritative domain evidence;
4. build `OperatorConsoleView`;
5. render sidebar controls;
6. render selected surface;
7. execute authoritative commands and rebuild the view.

Target: `app.py` contains orchestration, not HTML templates, chart construction, or
large evidence tables.

### 7.4 Styles and theme

- Re-enable and theme the Streamlit sidebar.
- Prefer `.streamlit/config.toml` tokens for base colors, fonts, borders, radii, and
  chart palette.
- Use one body font family consistently; monospace is reserved for hidden technical IDs.
- Use an 8px spacing system.
- Reduce dependence on unstable internal selectors in `heatsafe/ui/styles.py`.
- Preserve visible keyboard focus and `prefers-reduced-motion` behavior.
- No emoji as operational icons; use Streamlit Material Symbols consistently.

## 8. Performance and Rerun Plan

### 8.1 Current problems to remove

- `production_heartbeat()` is a fragment but calls unscoped `st.rerun()`, causing a full app rerun.
- Map selection calls `st.rerun()` after updating selection.
- Current tabs render hidden content.
- Full area and driver tables can be rebuilt during unrelated interactions.
- `session.advance()` is expensive and can block visible rendering.

### 8.2 Live Operations fragment

Use one coherent live-workspace fragment containing:

- status/header values that change with operational time;
- three KPIs;
- map and priority list;
- selected-area decision card;
- playback/time strip;
- two-hour outlook and outcome comparison when visible.

Sidebar controls and collapsed evidence remain outside the automatic heartbeat path.
Avoid many cross-dependent fragments.

### 8.3 Evidence rendering

Use a selected evidence sub-view or dynamic tabs with `on_change="rerun"`. Render only
the open sub-view. Do not build hidden tables or model charts.

### 8.4 Constraint updates

Budget and support inputs live in a form. Recompute the plan only after `Apply limits`.
Do not rerun expensive planning for every number-input increment.

### 8.5 Tick computation

Do not overlap advances. Introduce a bounded runner or equivalent guard that:

- allows at most one advance in progress;
- exposes `Updating conditions…` while work runs;
- stores the latest completed immutable result;
- prevents Play heartbeat and `Next 15 min` from advancing the same interval twice;
- cancels or invalidates stale work on Reset;
- records errors without blanking the monitoring UI.

### 8.6 Stable rendering

- Preserve map view and chart ranges when data updates where possible.
- Avoid changing panel height between loading, ready, and error states.
- Do not use animation longer than 300ms.
- No flashing or color pulsing.
- Respect reduced-motion preferences.

## 9. Delivery Phases

### Phase 0 — Operator contract and baseline

**Goal:** Freeze what the operator sees before code restructuring.

Tasks:

- capture screenshots for Current plan and Simulation playback at start, decision, and complete states;
- record render/rerun timing and current widget inventory;
- add the vocabulary contract to test fixtures/helpers;
- define actual Hanoi clock labels for the verified production window;
- confirm which three KPIs derive reliably from current data;
- confirm target viewport `1440 × 900` and secondary viewport `1280 × 800`.

Gate:

- product owner accepts the wireframe, three KPI contract, two-surface model, and removed-field list.

### Phase 1 — Pure operator view models

**Goal:** Create a stable operator presentation contract without changing domain logic.

Tasks:

- create `heatsafe/ui/operator_console/`;
- implement vocabulary/time formatters;
- implement operator KPI, area, recommendation, outcome, and state-panel view models;
- add unit tests for all internal-to-operator terminology conversions;
- prove that view-model construction does not mutate the authoritative plan.

Gate:

- all operator labels and values can be generated without importing Streamlit.

### Phase 2 — Shell, sidebar, KPIs, and map

**Goal:** Deliver the new default visual hierarchy.

Tasks:

- re-enable sidebar and move mode/limit/playback/reset controls into it;
- replace the current header/status pills with the compact operator status strip;
- render exactly three KPI cards;
- enlarge the map and add a maximum-three priority list;
- synchronize map, list, and sidebar area selection;
- apply semantic color rules;
- remove default city table and scatter plot from Operations.

Gate:

- map, three KPIs, and area selection fit above fold at `1440 × 900`.

### Phase 3 — Decision card and action lifecycle

**Goal:** Make the operator decision understandable without scrolling or technical evidence.

Tasks:

- implement selected-area summary;
- implement plain-language recommendation headline and timing;
- implement four compact guardrail rows;
- implement Activate/Continue confirmation and disabled/loading states;
- replace action controls with a receipt after choice;
- implement no-feasible, unavailable, stale, and pre-decision states;
- verify authoritative proposal and lineage checks remain server-side.

Gate:

- an operator can explain the recommendation, impact, and available action from one screenshot.

### Phase 4 — Optimization evidence and Why this plan

**Goal:** Make timing, trade-offs, stress robustness, and candidate selection visually obvious.

Tasks:

- add bounded additive optimization-evidence contracts;
- retain up to four timing options per area;
- derive and retain a deterministic maximum-12 city portfolio frontier/near-miss set;
- record evaluated and budget-compliant option counts;
- implement Timing, Trade-offs, and Stress test Plotly views in one chart slot;
- highlight selected plan and visible budget/service limits;
- add deterministic Act now vs wait, Expected vs High demand, Selected vs alternatives,
  and Tight budget test setups;
- prove diagnostics do not change the selected plan or authoritative checksum inputs.

Gate:

- from the charts, a reviewer can explain why the selected option was chosen and which
  constraint blocks at least one meaningful alternative.

### Phase 5 — Clock-time simulation and outcomes

**Goal:** Remove tick/K terminology and make playback understandable.

Tasks:

- replace tick/K captions with Hanoi clock time and range;
- rename controls to Play/Pause/Next 15 min/Reset;
- map speed values to Slow/Normal/Fast;
- implement decision-available time copy;
- implement `With SafePause` versus `Without SafePause` comparison;
- implement the bounded advance runner/overlap guard;
- isolate the live workspace rerun path.

Gate:

- no tick, K, snapshot, checksum, actual, or shadow terminology appears on Operations.

### Phase 6 — Decision insights and Evidence & history

**Goal:** Preserve judge/developer evidence without increasing operator cognitive load.

Tasks:

- integrate the two-hour outlook into the Timing view rather than adding another chart;
- add the conditional Outcome view using With SafePause versus Without SafePause histories;
- add the Areas table with 10 × 6 default contract;
- add the Drivers table with maximum 20 visible rows × 6 columns;
- add the History table with maximum 10 × 5 default contract;
- move detailed explanation provenance, model quality, lineage, IDs, and full 19-column data into collapsed/advanced evidence;
- ensure only the selected evidence sub-view renders.

Gate:

- no default table exceeds its row/column contract and no hidden evidence renders on heartbeat.

### Phase 7 — Accessibility, responsive behavior, tests, and cutover

**Goal:** Validate the new UI as an operational product surface.

Tasks:

- update `tests/test_app.py` around operator contracts rather than old widget order;
- add visual browser checks/screenshots for all critical states;
- test keyboard selection and visible focus;
- verify color-independent state labels;
- verify reduced motion;
- test `1440 × 900`, `1280 × 800`, `1024 × 768`, and narrow smoke behavior;
- remove or archive obsolete active renderers after parity;
- update `HEATSAFE_CURRENT_APP_GUIDE.md` and `README.md` screenshots/copy if applicable.

Gate:

- automated tests pass and user visual sign-off is complete.

## Acceptance Criteria

### Operator comprehension

- [ ] A first-time operator can identify current urgency, affected areas, recommendation, constraints, and action without opening Evidence & history.
- [ ] The default UI contains no unexplained tick/K/snapshot/checksum/model-lineage terms.
- [ ] All operational scheduling is shown as Hanoi clock time.
- [ ] The recommendation headline states who, when, and what action in plain language.
- [ ] `With SafePause` and `Without SafePause` replace actual/shadow labels.

### Optimization story

- [ ] One `Why this plan` chart slot appears below the primary decision area.
- [ ] Only one of Timing, Trade-offs, Stress test, or Outcome renders at a time.
- [ ] Timing compares up to four actual start times and highlights the selected time.
- [ ] Trade-offs compares no more than 12 deterministic portfolio points.
- [ ] Selected plan, budget limit, and rejected/near-miss reasons remain visible without relying only on hover.
- [ ] Trade-off y-axis uses operator-friendly avoided exposure, not raw risk probability.
- [ ] Stress test compares Expected and High-demand evidence against visible limits.
- [ ] Outcome appears only when coherent With SafePause/Without SafePause histories exist.
- [ ] Evaluated/feasible option counts come from the authoritative run, not hard-coded copy.
- [ ] Read-only diagnostics do not alter candidate ordering, selected plan, or action payload.
- [ ] Act-vs-wait, high-demand, alternatives, tight-budget, and activate-vs-continue stories are reproducible.

### Density

- [ ] Exactly three KPI cards appear on Operations.
- [ ] Number of selected areas is not a KPI.
- [ ] No table appears on Operations.
- [ ] No top-level layout uses more than two columns.
- [ ] Priority list shows at most three areas.
- [ ] Recommendation shows at most four guardrail rows.
- [ ] Areas table defaults to 10 rows × 6 columns.
- [ ] Driver table defaults to no more than 20 visible rows × 6 columns.
- [ ] History table defaults to no more than 10 rows × 5 columns.
- [ ] Full 19-column area data is advanced/download evidence only.

### Visual hierarchy

- [ ] At `1440 × 900`, three KPIs, map, selected-area recommendation, guardrails, and action are visible without scrolling.
- [ ] Map is the dominant visualization.
- [ ] Red, amber, green, cyan/blue, and gray retain one semantic meaning each.
- [ ] Green is not used for plan selection.
- [ ] Color is paired with text/icon state.
- [ ] Loading/error states do not collapse the primary layout.

### Interaction and performance

- [ ] Automatic playback does not issue an unscoped full-app rerun on every refresh.
- [ ] Only one interval advance can run at a time.
- [ ] Hidden evidence content is not rendered during heartbeat.
- [ ] Constraint edits recompute only after Apply limits.
- [ ] Map/list/sidebar selection stays synchronized.
- [ ] Action buttons disable while recording and cannot double-submit.
- [ ] Reset invalidates stale in-progress playback work.

### Safety and truthfulness

- [ ] Domain optimizer and guardrail calculations are unchanged.
- [ ] Activate/Continue use the authoritative city plan and existing stale/lineage checks.
- [ ] Missing recommendation remains fail-closed.
- [ ] Monitoring remains visible during model/planning failures.
- [ ] Synthetic/no-real-dispatch disclosure remains visible.
- [ ] UI copy does not claim medical diagnosis or proven incident reduction.

### Accessibility

- [ ] All interactive controls have labels and visible focus.
- [ ] Priority areas are accessible without using the map.
- [ ] Error and blocked states use Streamlit status semantics and explanatory text.
- [ ] Reduced-motion preference is respected.
- [ ] No flashing/pulsing status animation is used.

## Implementation Checklist

### Operator contract

- [ ] Add vocabulary formatter module.
- [ ] Add Hanoi time formatter and internal-time conversion tests.
- [ ] Add forbidden-default-term checks for `tick`, `K=`, `snapshot`, `checksum`, `shadow`, and `P95`.
- [ ] Add three-KPI contract tests.
- [ ] Add table shape/column contract tests.

### Layout

- [ ] Remove CSS that hides the sidebar.
- [ ] Move controls to sidebar forms/groups.
- [ ] Add compact operator status strip.
- [ ] Add three KPI cards.
- [ ] Build two-column map/decision composition.
- [ ] Add stack behavior below 1100px.
- [ ] Remove default raw table/scatter from Operations.

### Map and area selection

- [ ] Keep or simplify current ten-point map for first slice.
- [ ] Add three-item priority list.
- [ ] Use cyan/blue for selection, not green.
- [ ] Limit tooltip fields.
- [ ] Evaluate licensed Hanoi district GeoJSON as a separate enhancement gate.

### Decision

- [ ] Add plain-language recommendation view model.
- [ ] Add four guardrail summaries.
- [ ] Add confirmation dialog.
- [ ] Add blocked/loading/receipt states.
- [ ] Preserve exact authoritative action flow.

### Optimization evidence and charts

- [ ] Add optional/backward-compatible optimization diagnostics contracts.
- [ ] Record evaluated and budget-compliant portfolio counts.
- [ ] Retain up to four timing options per area.
- [ ] Retain a deterministic maximum-12 portfolio frontier/near-miss set.
- [ ] Exclude driver lists and raw path-cost arrays from diagnostics.
- [ ] Add Timing chart with shared clock-time axis and selected start annotation.
- [ ] Add Trade-offs scatter with selected plan and budget line.
- [ ] Add Stress test small multiples with visible service/cost limits.
- [ ] Add conditional Outcome comparison.
- [ ] Add tests proving diagnostics do not change selected portfolio/proposals.
- [ ] Add deterministic tests for all five required scenario stories.

### Playback

- [ ] Remove tick/K labels from default UI.
- [ ] Show current and decision clock times.
- [ ] Rename playback controls.
- [ ] Implement bounded/serialized advance handling.
- [ ] Replace actual/shadow display labels.

### Evidence

- [ ] Add Areas 10 × 6 table.
- [ ] Add Drivers ≤20-visible × 6 table.
- [ ] Add History 10 × 5 table.
- [ ] Add advanced full-data/download path.
- [ ] Move system quality and lineage into collapsed evidence.
- [ ] Render one evidence sub-view at a time.

### States and polish

- [ ] Add reusable loading state.
- [ ] Add recommendation-unavailable state.
- [ ] Add no-safe-plan state.
- [ ] Add stale/updating state.
- [ ] Add simulation-complete state.
- [ ] Validate focus, contrast, text alternatives, and reduced motion.

## Touchpoints

Expected primary files:

- `app.py`
- `.streamlit/config.toml`
- `heatsafe/ui/__init__.py`
- `heatsafe/ui/styles.py`
- `heatsafe/ui/state.py`
- `heatsafe/ui/production_mode.py`
- `heatsafe/ui/city_planner.py`
- `heatsafe/ui/decision_workspace.py`
- `heatsafe/ui/evidence_tabs.py`
- `heatsafe/models.py` — additive, optional optimization diagnostics only
- `heatsafe/services/preventive_planning.py` — materialize bounded diagnostics during the existing candidate pass
- `tests/test_preventive_planning.py`
- `tests/test_app.py`
- `HEATSAFE_CURRENT_APP_GUIDE.md`

Expected new files:

- `heatsafe/ui/operator_console/__init__.py`
- `heatsafe/ui/operator_console/shell.py`
- `heatsafe/ui/operator_console/sidebar.py`
- `heatsafe/ui/operator_console/operations.py`
- `heatsafe/ui/operator_console/outcomes.py`
- `heatsafe/ui/operator_console/city_map.py`
- `heatsafe/ui/operator_console/decision_card.py`
- `heatsafe/ui/operator_console/decision_insights.py`
- `heatsafe/ui/operator_console/evidence.py`
- `heatsafe/ui/operator_console/state_panels.py`
- `heatsafe/ui/operator_console/view_models.py`
- `heatsafe/ui/operator_console/vocabulary.py`
- `heatsafe/ui/operator_console/styles.py`

Potential optional asset:

- a licensed, source-documented Hanoi district GeoJSON under `data/` or `static/`.

## Blast Radius

### High

- `app.py` composition and widget order.
- Streamlit session keys tied to old widgets.
- `tests/test_app.py` assumptions about tabs, selectboxes, number inputs, and table presence.
- CSS selectors and visual layout.

### Medium

- Chart construction and map selection callbacks.
- Production heartbeat/rerun behavior.
- Evidence table formatting.
- `heatsafe/models.py` additive diagnostic dataclasses/default-empty fields.
- `heatsafe/services/preventive_planning.py` bounded diagnostic materialization during the existing candidate pass.
- Documentation and screenshots.

### Low / must remain behaviorally unchanged

- `heatsafe/services/decision_service.py`;
- optimizer candidate generation and lexicographic score order;
- selected city portfolio and selected per-area proposals;
- simulation engine and guardrails;
- repository contracts;
- audit lineage and idempotency semantics.

## Verification Evidence

### Implementation result — 28 Jul 2026

Implemented the operator-console cutover, bounded optimization diagnostics, clock-time
playback, one-slot decision charts, outcome adapter, confirmation lifecycle, and bounded
evidence tables. Automated evidence recorded during implementation:

- `tests.test_app + tests.test_operator_console + tests.test_preventive_planning`: **36 passed**;
- `tests.test_production_mode`: **7 passed**;
- remaining simulation modules after the full-suite timeout point: **69 passed**;
- full discovery ran for 10 minutes with no failure before timing out during the long
  simulation section; the remaining section was then run separately and passed;
- `python -m compileall -q app.py heatsafe infra`: passed;
- `pip check`: passed;
- `git diff --check`: passed;
- Streamlit AppTest confirms exactly three Operations KPIs, no Operations table, one active
  explanation chart, bounded evidence tables, tight-budget fail-closed copy, clock-language
  playback, and confirmed single-decision lifecycle.

Browser screenshots, responsive viewport review, and manual operator comprehension remain
manual sign-off items; they were not claimed by automated validation.

### Smooth playback remediation — 28 Jul 2026

Post-execution review found that the original playback acceptance was structurally green
but behaviorally incomplete: `Play`, `Next 15 min`, and the heartbeat still reached
unscoped `st.rerun()` paths, while every interval rebuilt the live workspace. A local
benchmark measured roughly `2.1s` for one engine advance plus `3.7–6.8s` for predictive
planning per frame.

The remediation separates the judge-facing Simulation playback from Current plan:

- `operator_presentation_timeline.json` precomputes 9 pre-decision frames and 8 frames
  for each post-decision display branch from the verified Production window;
- the presentation artifact uses fixed `$500` budget and `$0.32` support-per-driver
  limits, yielding a real `READY` plan with `275 / 275` urgent coverage and `$99`
  remaining; Current plan limits remain independent and authoritative;
- one Streamlit Custom Component v2 receives an approximately 125 KB compact payload;
- Play, Pause, Next 15 min, Reset, speed, map selection, and branch choice run entirely
  in browser JavaScript;
- KPI values, SVG bubble map, and line/area timeline update in one stable DOM with
  bounded transitions and `prefers-reduced-motion` handling;
- the component never calls `setStateValue` or `setTriggerValue`, so playback controls
  cannot initiate a Python or Streamlit rerun;
- Current plan keeps the authoritative optimizer, guardrails, action recording, Plotly,
  PyDeck, and evidence surfaces.

Remediation evidence:

- `tests.test_app + tests.test_operator_console + tests.test_production_mode`:
  **27 passed**;
- `tests.test_preventive_planning`: **18 passed**;
- JavaScript module syntax check: passed;
- AppTest measured the Simulation playback mount at **0.011s** after mode selection,
  with no `ProductionSession` created;
- `compileall`, `pip check`, `git diff --check`, and `/_stcore/health`: passed;
- component contract test confirms no `ProductionSession` is created in Simulation
  playback and verifies the bounded artifact/branch payload.

The connected browser-control surface was unavailable during this remediation session.
Responsive screenshots and hands-on transition sign-off therefore remain pending and
are not claimed by the evidence above.

### Automated commands

Run targeted UI tests first:

```bash
python -m unittest tests.test_app -v
python -m unittest tests.test_production_mode -v
```

Then broader validation:

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py heatsafe infra
pip check
```

### Required browser evidence

Capture screenshots or recordings for:

1. Current plan ready.
2. Simulation playback at start time.
3. Before recommendation time.
4. Decision needed.
5. SafePause activated.
6. Continue monitoring chosen.
7. Recommendation temporarily unavailable.
8. No safe plan fits current limits.
9. Simulation complete.
10. Evidence Areas/Drivers/History views.
11. Timing view showing selected versus wait options.
12. Trade-offs view showing selected, cheaper, stronger, and blocked alternatives.
13. Stress test showing Expected versus High-demand limits.
14. Tight-budget state with a changed frontier or explicit fail-closed result.
15. Outcome view showing With SafePause versus Without SafePause.

Viewports:

- `1440 × 900` — primary acceptance.
- `1280 × 800` — laptop acceptance.
- `1024 × 768` — stacked-layout acceptance.
- narrow viewport — smoke test; not the primary ops target.

### Manual operator test

Give a reviewer no explanation of simulation internals and ask:

1. How many drivers need a break now?
2. Where is the highest-priority area?
3. What does HeatSafe recommend?
4. When does the break start?
5. Are service and cost within limits?
6. Why was this start time chosen instead of waiting?
7. What does the selected plan gain or give up versus a cheaper alternative?
8. Does the plan still pass under high demand?
9. What actions can you take?
10. What happened after activation?

Acceptance requires correct answers from the default Operations surface without opening
Advanced system details.

## Risks and Controls

### R-1 — Simplification hides safety-relevant context

**Control:** Keep coverage, service impact, pickup delay, cost, timing, and synthetic
disclosure on the decision card. Preserve full evidence in the secondary surface.

### R-2 — UI labels drift from domain semantics

**Control:** Centralize vocabulary/time formatting and add explicit mapping tests. Do not
format operator copy ad hoc in renderers.

### R-3 — Clock time becomes ambiguous across dates

**Control:** Use Hanoi timezone consistently and show date when a window crosses midnight.
Keep internal tick lineage untouched.

### R-4 — Sidebar control changes trigger expensive recomputation

**Control:** Batch limits in a form and apply once. Separate controls from live heartbeat.

### R-5 — Streamlit reruns still make the new design feel unstable

**Control:** Implement the live-workspace fragment and dynamic evidence rendering before
animation. Stable layout is a phase gate.

### R-6 — Operator and judge needs conflict

**Control:** Default to operator simplicity; preserve advanced/download evidence for judges
and developers. Do not reintroduce raw data above fold.

### R-7 — GeoJSON adds licensing or mapping risk

**Control:** Ship the bubble-map first. Add choropleth only after source, license, district
IDs, geometry, and offline asset behavior are verified.

### R-8 — Existing AppTest assertions encourage retaining the old UI

**Control:** Rewrite tests around operator contracts and safety behavior, not old widget
order or tab count.

### R-9 — Technical terminology leaks through exception strings

**Control:** Log full exception details server-side; map known failures to operator states.
Show raw exception/type only under Advanced system details where appropriate.

### R-10 — Trade-off charts misrepresent the safety-first objective

**Control:** Label urgent-driver coverage as the first constraint, keep selected and blocked
states explicit, and never present the scatter as a generic cost-benefit score. Include
only metrics emitted by the authoritative candidate pass.

### R-11 — Diagnostics accidentally change optimization behavior or payload size

**Control:** Build summaries during the existing pass, preserve the current score tuple,
add regression tests for selected IDs/proposals, cap points/options, and exclude driver
lists and path arrays from UI diagnostics.

### R-12 — Too many scenario charts recreate cognitive overload

**Control:** Use one stable chart slot and render one selected view. Default to Timing;
make Trade-offs and Stress test intentional choices; show Outcome only after a decision.

## Resume and Execution Handoff

Implementation should resume in this order:

1. Read this plan and the active app guide.
2. Run the current UI tests and capture baseline screenshots.
3. Implement Phase 1 view models and vocabulary tests before rendering changes.
4. Build the new operator console in parallel with existing renderers.
5. Complete Phase 2 and Phase 3 visual gates before playback/performance work.
6. Add bounded optimization diagnostics and prove selected outputs are unchanged.
7. Complete Timing, Trade-offs, and Stress test evidence before polishing transitions.
8. Complete clock-time and bounded-advance behavior, then add Outcome comparison.
9. Move table evidence only after the operator and optimization-story surfaces are stable.
10. Cut over `app.py` only after automated and visual acceptance evidence is recorded.
11. Remove obsolete renderers only in the cleanup step; do not delete them early.

Do not change optimizer/simulation behavior to make UI implementation easier. If an
operator metric cannot be derived honestly from current domain evidence, omit it or mark
it unavailable instead of inventing a value.

## Validate Contract

The plan is complete only when all of the following remain true:

```text
DEFAULT SURFACES = 2
OPERATIONS KPIS = 3
TOP-LEVEL COLUMNS <= 2
OPERATIONS TABLES = 0
ACTIVE WHY-THIS-PLAN CHARTS = 1
TIMING OPTIONS <= 4
PORTFOLIO TRADE-OFF POINTS <= 12
PRIORITY LIST ITEMS <= 3
DECISION GUARDRAILS <= 4
AREA TABLE = 10 rows x <= 6 default columns
DRIVER TABLE = <= 20 visible rows x <= 6 default columns
HISTORY TABLE = <= 10 rows x <= 5 default columns
OPERATOR TIMEZONE = Asia/Ho_Chi_Minh
DEFAULT UI TICK/K/SNAPSHOT/CHECKSUM TERMS = 0
AUTHORITATIVE PLAN / GUARDRAIL / LINEAGE BEHAVIOR = unchanged
OPTIMIZATION DIAGNOSTICS = read-only, bounded, deterministic
REQUIRED STORIES = act-vs-wait + demand-stress + alternatives + tight-budget + outcome
REAL DISPATCH = absent
```

Any implementation that violates these limits requires an explicit plan amendment and
operator-value rationale before merge.
