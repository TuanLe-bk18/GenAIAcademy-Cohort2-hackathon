# HeatSafe Ops

GCP-native decision-intelligence platform for protecting two-wheel ride-hailing drivers during extreme heat while keeping platform cost, fulfillment and ETA within explicit guardrails.

HeatSafe uses Heat Index as a screening indicator. Operational priority and intervention estimates are not medical diagnoses or proven reductions in health incidents.

## Architecture

```text
Open-Meteo -> Cloud Run Job -> Cloud Storage raw JSON -> BigQuery history
Simulated fleet operations ---------------------------> current live snapshot
Demand history ---------------------------------------> BigQuery AI.FORECAST
                                                           |
                                      One Cloud Run Streamlit demo
                                      SafePause + Vertex AI Gemini
                                                           |
                                      BigQuery simulated decision audit
```

The services have concrete responsibilities:

- **Cloud Storage** stores immutable provider payloads and replay scenarios. BigQuery rows retain the `raw_gcs_uri` lineage.
- **BigQuery** stores append-only history plus a small scenario-safe `zone_snapshots_current` table used by the dashboard.
- **BigQuery ML** uses TimesFM `AI.FORECAST` to predict zone demand without a managed training pipeline.
- **Vertex AI Gemini** selects from allowlisted decision functions; it never writes SQL or approves actions.
- **Decision audit** records simulated interventions only. The demo never sends an operational command to drivers.
- **Cloud Run** hosts Streamlit and a separate weather-ingestion job; structured stdout events flow to Cloud Logging.

## Decision engine

SafePause is a deterministic digital-twin simulation; Gemini never invents or
approves an action. For every zone the engine:

1. separates the eligible pool into drivers active 4+ hours and 2–4 hours;
2. enumerates pause duration, cohort coverage, and staggered-wave candidates;
3. simulates supply, demand, backlog, fulfillment, and ETA every five minutes;
4. validates each candidate against median and upper-bound demand;
5. ranks feasible actions safety-first, then by P90 SLA impact and platform cost.

The selected proposal retains the full wave timeline, eligible-versus-selected
counts, P50/P90 outcomes, reason codes, and a deterministic proposal ID. BigQuery
stores this structured audit payload; TimesFM `AI.FORECAST` supplies the demand
distribution used by the simulator. Gemini receives only verified forecast and
proposal objects through allowlisted tools and acts as the explanation layer.

## GCP resources

`infra/provision_gcp.py` creates or migrates resources without changing data:

- Bucket: `${GOOGLE_CLOUD_PROJECT}-heatsafe-raw`
- BigQuery: `weather_observations`, `zone_operations`, `demand_history`, `coolstop_partners`, `intervention_proposals`, `intervention_events`
- Current snapshot: `zone_snapshots_current`
- Views: `zone_snapshots_live`, `zone_snapshots_heatwave`

Demo data is opt-in and uses idempotent `MERGE`; provisioning never truncates live or intervention data.

```bash
source venv/bin/activate
pip install -r requirements.txt
export GOOGLE_CLOUD_PROJECT=cohort2track2
python infra/provision_gcp.py --seed-demo
python generate_data.py
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

The sidebar can switch between the GCS heatwave replay and current Open-Meteo weather. For a fully offline fallback:

```bash
HEATSAFE_MODE=snapshot HEATSAFE_ENABLE_AI=0 streamlit run app.py
```

## Gemini decision tools

- `get_operational_snapshot`
- `rank_heat_hotspots`
- `explain_zone_risk`
- `forecast_zone_demand`
- `compare_safepause_options`
- `recommend_intervention`

Tool selection uses Gemini function calling with an explicit allowlist. Destructive requests are blocked before the tool layer. TimesFM reads a bounded 21-day context and forecast errors are surfaced instead of silently reused.

## Deploy

The deployment script enables required APIs, provisions schema, deploys one public Streamlit demo and creates its scheduled ingestion job:

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
