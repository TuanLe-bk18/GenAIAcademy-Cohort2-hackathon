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
                                      constrained optimizer -> Cloud Run UI
                                                         |
                                      Gemini explanation + BigQuery audit
```

The services have concrete responsibilities:

- **Cloud Storage** stores immutable provider payloads and replay scenarios. BigQuery rows retain the `raw_gcs_uri` lineage.
- **BigQuery** stores history, driver features, outcomes, forecasts, model evaluations, predictions and audits.
- **BigQuery ML** uses TimesFM for demand and a boosted-tree classifier for individual 60-minute operational heat-risk escalation.
- **Counterfactual scoring** evaluates no action and eight SafePause timing/duration actions per driver; without matching predictions the app provides no recommendation.
- **Vertex AI Gemini** explains allowlisted model evidence; it never writes SQL or approves actions.
- **Decision audit** records simulated interventions only. The demo never sends an operational command to drivers.
- **Cloud Run** hosts Streamlit and a separate weather-ingestion job; structured stdout events flow to Cloud Logging.

## Decision engine

SafePause combines learned risk with deterministic safety constraints. For every zone the engine:

1. loads snapshot-matched BigQuery ML risk predictions for every driver and action;
2. treats every driver with at least four hours of continuous exposure as mandatory, independent of estimated action benefit;
3. fills the earliest waves with mandatory drivers ordered by baseline risk and exposure, then uses predicted waiting cost and action benefit for the remaining slots;
4. enumerates pause duration, coverage and staggered-wave candidates, including a mandatory-only candidate;
5. simulates incremental supply, backlog, fulfillment and ETA against TimesFM median and upper demand;
6. returns a recommendation only when all mandatory drivers are covered and cost and incremental SLA guardrails pass; otherwise it reports the safety conflict.

The proposal retains before/after risk, feature attributions, model version,
prediction run, wave timeline, stress outcomes, costs and a deterministic proposal ID.
`MODEL_UNAVAILABLE` and `NO_FEASIBLE` states never contain a recommendation.
The four-hour rule is a demo policy threshold, not a medical or regulatory limit.

## GCP resources

`infra/provision_gcp.py` creates or migrates resources without changing data:

- Bucket: `${GOOGLE_CLOUD_PROJECT}-heatsafe-raw`
- BigQuery sources: `weather_observations`, `zone_operations`, `demand_history`, `driver_state_history`, `driver_intervention_outcomes`
- BigQuery AI outputs: `driver_current_features`, `driver_risk_predictions`, `zone_demand_forecasts`, `model_evaluations`
- BigQuery audit: `intervention_proposals`, `intervention_events`
- Current snapshot: `zone_snapshots_current`
- Views: `zone_snapshots_live`, `zone_snapshots_heatwave`

Demo data is opt-in and uses idempotent `MERGE`; provisioning never truncates live or intervention data.

```bash
source venv/bin/activate
pip install -r requirements.txt
export GOOGLE_CLOUD_PROJECT=cohort2track2
python infra/provision_gcp.py --seed-demo
python infra/ml_pipeline.py --all --scenario heatwave
```

`generate_data.py` obtains real Open-Meteo weather and combines it with clearly labelled simulated fleet operations in one coherent live `snapshot_id`. For the prototype, the deployed Cloud Run Job is run manually when the live snapshot needs refreshing; no recurring scheduler is required.

## Run

Cloud-first with heatwave replay selected by default:

```bash
HEATSAFE_MODE=cloud \
HEATSAFE_SCENARIO=heatwave \
HEATSAFE_ENABLE_AI=1 \
streamlit run app.py
```

The sidebar can switch between the GCS heatwave replay and current Open-Meteo weather. Offline mode is monitoring-only because AI recommendations fail closed:

```bash
HEATSAFE_MODE=snapshot HEATSAFE_ENABLE_AI=0 streamlit run app.py
```

## Gemini evidence tools

- `get_operational_snapshot`
- `rank_heat_hotspots`
- `explain_zone_risk`
- `forecast_zone_demand`
- `compare_safepause_options`
- `recommend_intervention`

Tool selection uses Gemini function calling with an explicit allowlist. Destructive requests are blocked before the tool layer. TimesFM reads a bounded 21-day context and forecast errors are surfaced instead of silently reused.

## Deploy

The deployment script provisions schema, deploys the public Streamlit demo and creates manually invoked ingestion, model-training and scoring jobs. It creates no recurring Scheduler.

```bash
chmod +x scripts/deploy_gcp.sh
./scripts/deploy_gcp.sh
```

To explicitly seed or refresh the heatwave replay during deployment:

```bash
./scripts/deploy_gcp.sh --seed-demo
```

The public action is intentionally labelled `SIMULATED` with `dispatch_status=NOT_APPLICABLE`. Public access is suitable only for the hackathon demo; production approval would require authenticated users and a real downstream command consumer.

## Verify

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py heatsafe infra
pip check
```
