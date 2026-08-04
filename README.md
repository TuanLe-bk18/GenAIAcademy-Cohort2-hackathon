# HeatSafe AI Ops

HeatSafe AI Ops is a GCP-native decision-support prototype for two-wheel ride-hailing operators during extreme heat. It turns weather, driver exposure, demand, and model evidence into a city-wide **SafePause** plan that balances driver protection with platform cost, fulfillment, and pickup-delay guardrails.

> **Project boundary:** HeatSafe is a working hackathon prototype for operational planning and decision audit. Heat Index is a screening indicator, not a medical diagnosis. The app does not autonomously dispatch drivers or send notifications, and projected impact is not proof of reduced real-world incidents.

## Why HeatSafe

Heat risk creates a difficult fleet-operations tradeoff: intervening too late increases driver exposure, while pausing too much supply can reduce fulfillment, increase pickup time, and raise platform cost.

HeatSafe gives an operator one evidence-backed control surface to:

- identify districts and drivers that need attention now or are likely to need it soon;
- compare intervention timing before exposure crosses a hard policy threshold;
- protect mandatory cases first while keeping a shared city budget and service-level constraints visible;
- understand why an option was selected, deferred, or rejected;
- record the human decision together with its source snapshot, forecast, model, and proposal lineage.

The result is a safer and more explainable operating process without presenting AI as an autonomous safety authority.

## Monitor → Alert → Decide → Protect

| Stage | What HeatSafe does | Operator outcome |
|---|---|---|
| **1. Monitor** | Ingests weather and fleet-operating signals, calculates Heat Index, stores raw provider payloads in Cloud Storage, and maintains one coherent current snapshot in BigQuery. Snapshot freshness and component lineage are checked before planning. | A city map and operational KPIs grounded in a specific evidence point. |
| **2. Alert** | Combines current exposure, BigQuery ML driver-risk scores, TimesFM demand forecasts, and 64-path preventive projections. Drivers are classified as `MANDATORY_4H`, `PROJECTED_MANDATORY`, `WATCHLIST`, or model-eligible; districts receive separate severity, future-safety, and intervention-opportunity ranks. | Prioritized operational alerts instead of a single opaque risk score. |
| **3. Decide** | Evaluates SafePause start times, durations, and staggered waves per district, then searches city-wide portfolios under safety, cost, fulfillment, and ETA constraints. Gemini can explain the evidence through an allowlisted tool layer, but the recommendation remains deterministic and reviewable. | A recommended portfolio, feasible alternatives, near misses, and explicit rejection reasons. |
| **4. Protect** | Converts the selected portfolio into staged SafePause waves, estimated earnings support, capacity reallocation, and CoolStop routing context. The operator chooses **Activate** or **Continue**; HeatSafe records that choice and its projected outcomes for audit. | A human-approved protection plan with traceability. External dispatch remains an integration boundary. |

HeatSafe fails closed when evidence is stale, incomplete, or mismatched. States such as `MODEL_UNAVAILABLE`, `FORECAST_UNAVAILABLE`, `SNAPSHOT_MISMATCH`, `EVIDENCE_UNAVAILABLE`, `NO_FEASIBLE`, and `SAFETY_CAPACITY_BREACH` remain visible instead of being converted into a confident recommendation.

## GCP-native architecture

```text
Weather provider + fleet operating signals
                     |
                     v
             Cloud Run Jobs
          ingest / train / score
                     |
          +----------+-----------+
          |                      |
          v                      v
  Cloud Storage              BigQuery
  raw evidence          current operational state
  and lineage           history, forecasts, audit
          |                      |
          +----------+-----------+
                     |
          +----------+-----------+
          |                      |
          v                      v
 BigQuery ML classifier    TimesFM AI.FORECAST
 action-conditioned risk   demand + uncertainty
          |                      |
          +----------+-----------+
                     v
      Evidence and preventive projection layer
      snapshot matching · freshness · 64 paths
                     |
                     v
        Safety-first SafePause optimizer
   district candidates → city portfolio → guardrails
                     |
          +----------+-----------+
          |                      |
          v                      v
 Cloud Run operator app   Vertex AI Gemini Copilot
 map, evidence, action    allowlisted explanations
          |
          v
   Human decision → BigQuery audit trail
```

### Why this infrastructure structure is strong

| GCP component | Role in HeatSafe | Technical value |
|---|---|---|
| **Cloud Storage** | Write-once landing area for raw provider payloads and evidence artifacts. | Preserves source lineage independently of transformed operational tables; uploads use generation preconditions to avoid accidental overwrite. |
| **BigQuery** | System of record for observations, current snapshots, driver features, forecasts, predictions, proposals, model evaluation, and decision history. | Separates append-only history from `zone_snapshots_current`; uses partitioning, clustering, parameterized queries, bounded scan budgets, and staging-table `MERGE` upserts. |
| **BigQuery ML** | Trains and evaluates the heat-risk classifier, scores baseline and SafePause actions, explains top factors, and produces demand forecasts with TimesFM. | Keeps model execution close to governed data and makes model/run/snapshot lineage queryable. |
| **Cloud Run** | Hosts the Streamlit operator console and separate ingestion, training, and scoring jobs. | Decouples interactive serving from batch work, uses one reproducible container image, and supports bounded scaling and retry policies. |
| **Vertex AI Gemini** | Evidence-bound operational Copilot. | Uses a fixed, allowlisted tool surface; recommendations come from the policy engine, while Gemini focuses on explanation and operator questions. |
| **Cloud Logging path** | Collects one-line structured JSON telemetry emitted to stdout. | Makes ingestion, model, fallback, Copilot, and decision events observable without coupling the domain layer to a logging vendor. |

The main deploy path uses a dedicated runtime service account and regional BigQuery, Cloud Storage, and Cloud Run resources in `asia-southeast1`. Ingestion, training, and scoring are explicit Cloud Run Jobs in the current setup, so data refresh and model changes remain controlled operations.

## Technical architecture

The codebase separates evidence acquisition, forecasting, policy, execution boundary, and presentation:

```text
Repository adapters
  └─ coherent snapshots, forecasts, features, predictions, audit history

Forecast and evidence services
  └─ lineage validation, 0/15/60/120-minute projection, driver safety tiers

Decision services
  └─ district candidates, city portfolio optimization, unavailable-state handling

Operational boundary
  └─ plan-expiry check, snapshot revalidation, Activate/Continue audit receipt

Operator experience
  └─ city map, decision card, guardrails, evidence/history, Copilot
```

This separation provides four important properties:

1. **Evidence before recommendation.** Forecasts and driver predictions must match the active snapshot and model lineage.
2. **Deterministic decision policy.** The same evidence and constraints produce the same forecast paths, rankings, and selected portfolio.
3. **Human-in-the-loop control.** AI explains evidence; it does not bypass the policy engine or operator approval.
4. **Fail-closed behavior.** Missing models, stale plans, mixed snapshots, unavailable forecasts, or insufficient safety capacity are explicit states.

## Core algorithms

### 1. Heat screening

Weather ingestion calculates Heat Index from temperature and relative humidity using the NOAA-style regression and humidity adjustments. Heat Index remains a screening signal used alongside operational exposure and workload features.

### 2. Action-conditioned driver risk

The BigQuery ML boosted-tree classifier uses features including:

- Heat Index and humidity;
- continuous exposure duration;
- recent trips and distance;
- recent rest and hydration gap;
- route heat load and workload intensity;
- candidate SafePause delay and duration.

For each driver, the scoring pipeline evaluates a no-action baseline and SafePause choices. `ML.EXPLAIN_PREDICT` materializes top contributing factors so the operator can inspect why risk is high and why an action changes it.

### 3. Demand forecasting

TimesFM through BigQuery `AI.FORECAST` produces 15-minute demand points with 90% prediction intervals. HeatSafe uses median and upper-demand paths separately so service guardrails are tested under expected and stressed demand rather than relying on one point estimate.

### 4. Preventive exposure projection

The projection layer evaluates common deterministic paths across every district at `0`, `15`, `60`, and `120` minutes:

- **64 aligned paths** preserve the same city-level uncertainty IDs across zones;
- online continuation depends on the driver state and the district demand ratio;
- projected risk updates from current model risk, exposure change, heat change, and recovery;
- a 15-minute recovery resets continuous exposure in the policy model;
- path-level cost produces an aligned city P95 reserve rather than summing unrelated district percentiles.

Driver safety tiers are policy-first:

- `MANDATORY_4H`: continuous exposure is at least **240 minutes**;
- `PROJECTED_MANDATORY`: probability of crossing 240 minutes before adequate recovery is at least **50%**;
- `WATCHLIST`: crossing probability is above zero but below 50%;
- model-eligible: baseline risk and expected action benefit pass the model thresholds.

There is no single driver leaderboard. Mandatory cases are handled first; within each tier, ordering uses baseline risk, exposure, predicted cost of waiting, expected risk reduction, and a stable driver ID tie-break.

### 5. SafePause candidate search

For every eligible district, the planner evaluates:

- start delay: `0`, `15`, `30`, or `45` minutes;
- duration: `15` or `30` minutes;
- `1` to `4` staggered waves;
- median-demand and upper-demand service conditions.

Mandatory drivers are assigned to the earliest available waves. Remaining capacity prioritizes projected-mandatory drivers and then model-eligible drivers with the highest cost of waiting and expected risk reduction.

Each candidate estimates:

- drivers covered and exposure minutes avoided;
- residual risk at 60 and 120 minutes;
- fulfillment and pickup-delay impact;
- earnings support, lost platform contribution, partner support, and net platform cost;
- P95 reserved cost across aligned forecast paths.

### 6. City-wide portfolio optimization

HeatSafe evaluates combinations of feasible district plans under one shared P95 budget cap. The safety-first objective is lexicographic:

1. maximize coverage of currently mandatory drivers;
2. minimize projected 240-minute threshold crossings at 120 minutes;
3. minimize the worst district residual risk;
4. maximize expected risk reduction;
5. minimize P95 reserved cost, expected cost, and service impact.

The app also exposes cheapest-feasible, highest-protection, lowest-service-impact, Pareto-frontier, and near-miss options. Operators can therefore inspect the tradeoff surface instead of receiving only one unexplained answer.

## Decision guardrails

A SafePause recommendation is actionable only when:

1. all required evidence belongs to the active snapshot and compatible model/forecast lineage;
2. all currently mandatory drivers in the selected scope are covered;
3. city P95 reserved cost stays within the configured budget;
4. upper-demand fulfillment degradation is no more than **2 percentage points**;
5. upper-demand pickup-delay increase is no more than **2 minutes**;
6. the proposal is still within its 15-minute validity window when the operator acts.

If mandatory safety demand cannot be covered within available capacity and budget, HeatSafe reports `SAFETY_CAPACITY_BREACH`; it does not silently optimize mandatory cases away.

## Operator experience

The Streamlit console combines:

- a Hanoi district map with heat, demand, exposure, and SafePause coverage;
- city and district KPIs with explicit evidence time and provenance;
- priority-area rankings and projected risk horizons;
- a decision card with recommendation, alternatives, guardrails, and rejection reasons;
- optimization evidence, model lineage, forecast details, and decision history;
- an evidence-bound Copilot for operational questions;
- explicit **Activate** and **Continue** controls at the human decision point.

This helps an operator move from fragmented monitoring to a repeatable operating process: detect early, understand the tradeoff, decide with guardrails, and retain an auditable record.

## Project structure

```text
app.py                         Streamlit entry point and operator workflow
heatsafe/config.py             validated runtime configuration
heatsafe/repository.py         BigQuery and local evidence adapters
heatsafe/services/             forecast, preventive planning, decision services
heatsafe/ai_decision.py        driver policy and SafePause candidate search
heatsafe/cloud_bundle.py       fail-closed cloud evidence loader
heatsafe/operational_runtime.py decision validation and audit boundary
heatsafe/copilot.py            allowlisted Copilot tools and Gemini integration
heatsafe/ui/                   operator console and evidence surfaces
infra/                         GCP provisioning and BigQuery ML pipeline
scripts/                       deploy, launch, and operational tooling
tests/                         unit and contract tests
Dockerfile                     Python 3.12 Cloud Run image
```
