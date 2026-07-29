# HeatSafe AI Ops

GCP-native predictive intervention platform for protecting two-wheel ride-hailing drivers during extreme heat while keeping platform cost, fulfillment and ETA within explicit guardrails.

HeatSafe uses Heat Index as a screening indicator. Operational priority and intervention estimates are not medical diagnoses or proven reductions in health incidents.

## Architecture

```text
Weather + driver telemetry + intervention outcomes -> BigQuery system of record
Demand history ------------------------------------> TimesFM AI.FORECAST
Driver state + action + outcome --------------------> BigQuery ML risk classifier
                                                         |
                                      action-conditioned counterfactual scores
                                                         |
                                      constrained optimizer -> Cloud Run UI (Streamlit)
                                                         |
                                      Gemini explanation + BigQuery audit log
```

### System Components

- **Cloud Storage**: Stores immutable provider payloads, replay scenarios, and checkpoint snapshots (`raw_gcs_uri` lineage).
- **BigQuery**: Stores operational history, driver features, intervention outcomes, demand forecasts, model evaluations, predictions, and decision audit logs.
- **BigQuery ML**: Uses TimesFM (`AI.FORECAST`) for demand forecasting and a boosted-tree classifier for individual 60-minute heat-risk escalation predictions.
- **Stateful Production Engine**: Simulates driver exposure, order fulfillment, and staggered SafePause intervention waves (Actual vs. Shadow counterfactual baseline).
- **SafePause Decision Engine**: Evaluates baseline risk against 8 SafePause timing/duration actions per driver under strict SLA, cost, and mandatory exposure constraints.
- **Vertex AI Gemini Copilot**: Explains allowlisted model evidence via structured function calling; operates under strict fail-closed security boundaries.
- **Cloud Run & Docker**: Hosts the Streamlit control panel and automated weather ingestion jobs.

## Operational & Interactive Modes

HeatSafe supports two primary operational views:

1. **Production Window (Checkpoint K=45)**: Stateful simulation mode allowing operators to advance ticks (15-min increments), observe actual vs. shadow counterfactual branches, and execute SafePause interventions.
2. **Heatwave Replay Snapshot**: Static cloud-first replay mode using pre-computed GCS & BigQuery snapshots for quick inspection and offline fallback.

## Decision Engine & Guardrails

SafePause combines machine-learned risk predictions with deterministic safety rules:

1. **Mandatory Protection Threshold**: Drivers with $\ge 4$ hours of continuous extreme heat exposure are flagged as mandatory for pause, regardless of estimated cost.
2. **Staggered Wave Allocation**: Fills initial waves with mandatory drivers (ordered by baseline risk/exposure), then allocates remaining slots based on predicted action benefit.
3. **Multi-Candidate Simulation**: Evaluates pause duration, coverage, and wave timelines against TimesFM median and upper-bound demand estimates.
4. **Constraint Enforcement**: Recommends an intervention only when mandatory coverage is 100% and incremental cost, fulfillment, and ETA guardrails pass.
5. **Fail-Closed Guarantee**: Returns `MODEL_UNAVAILABLE` or `NO_FEASIBLE` states with no recommendation whenever evidence is incomplete or constraints are violated.

## GCP Resources & Infrastructure

Provision GCP resources using `infra/provision_gcp.py`:

- **Bucket**: `${GOOGLE_CLOUD_PROJECT}-heatsafe-raw`
- **BigQuery Datasets**:
  - Raw Sources: `weather_observations`, `zone_operations`, `demand_history`, `driver_state_history`, `driver_intervention_outcomes`
  - ML Outputs: `driver_current_features`, `driver_risk_predictions`, `zone_demand_forecasts`, `model_evaluations`
  - Audit Trail: `intervention_proposals`, `intervention_events`
- **Views**: `zone_snapshots_current`, `zone_snapshots_live`, `zone_snapshots_heatwave`

```bash
source venv/bin/activate
pip install -r requirements.txt
export GOOGLE_CLOUD_PROJECT=cohort2track2
python infra/provision_gcp.py --seed-demo
python infra/ml_pipeline.py --all --scenario heatwave
```

## Running Locally

Run localhost with the current non-secret runtime configuration read directly
from the deployed `heatsafe-ops` Cloud Run service:

```bash
./scripts/run_local_like_cloud_run.sh
```

This requires an active `gcloud` login and Application Default Credentials. The
launcher mirrors only allowlisted app settings, including the pinned Production
bundle; it does not copy secrets or mutate Cloud Run. The application default
Production evidence is decision tick 40.

Run Offline Snapshot mode (monitoring-only, fail-closed):

```bash
HEATSAFE_MODE=snapshot HEATSAFE_ENABLE_AI=0 streamlit run app.py
```

## Gemini Evidence Tools

Allowlisted function tools for Gemini Copilot:

- `get_operational_snapshot`: Retrieves current weather, risk, and supply status across zones.
- `rank_heat_hotspots`: Ranks operational zones by Heat Index and driver exposure density.
- `explain_zone_risk`: Surfaces key risk drivers and feature attributions for a specific zone.
- `forecast_zone_demand`: Fetches TimesFM demand forecasts and uncertainty bounds.
- `compare_safepause_options`: Evaluates counterfactual SafePause timing and duration candidates.
- `recommend_intervention`: Formulates guardrail-validated intervention proposals.

## Deployment & Verification

Deploy to Google Cloud Run:

```bash
chmod +x scripts/deploy_gcp.sh
./scripts/deploy_gcp.sh --seed-demo
```

Run test suite and verification checks:

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py heatsafe infra
pip check
```
