# HeatSafe Ops

GCP-native decision-intelligence platform for protecting two-wheel ride-hailing drivers during extreme heat while keeping platform cost, fulfillment and ETA within explicit guardrails.

HeatSafe uses Heat Index as a screening indicator. Operational priority and intervention estimates are not medical diagnoses or proven reductions in health incidents.

## Architecture

```text
Open-Meteo -> Cloud Run Job -> Cloud Storage raw JSON -> BigQuery weather
Demand history ---------------------------------------> BigQuery AI.FORECAST
Simulated fleet operations ---------------------------> BigQuery zone operations
                                                           |
                                      Cloud Run app + SafePause optimizer
                                                           |
                                          Vertex AI Gemini function calling
                                                           |
                                  BigQuery decision audit + Pub/Sub command
```

The services have concrete responsibilities:

- **Cloud Storage** stores immutable provider payloads and replay scenarios. BigQuery rows retain the `raw_gcs_uri` lineage.
- **BigQuery** is the operational source of truth for weather, fleet aggregates, demand history, partners and interventions.
- **BigQuery ML** uses TimesFM `AI.FORECAST` to predict zone demand without a managed training pipeline.
- **Vertex AI Gemini** selects from allowlisted decision functions; it never writes SQL or approves actions.
- **Pub/Sub** receives an `ACTIVATE_SAFEPAUSE` command only after human approval.
- **Cloud Run** hosts Streamlit and a separate weather-ingestion job; structured stdout events flow to Cloud Logging.

## GCP resources

`infra/provision_gcp.py` creates and seeds:

- Bucket: `${GOOGLE_CLOUD_PROJECT}-heatsafe-raw`
- BigQuery: `weather_observations`, `zone_operations`, `demand_history`, `coolstop_partners`, `intervention_proposals`, `intervention_events`
- Views: `zone_snapshots_live`, `zone_snapshots_heatwave`
- Pub/Sub topic: `heatsafe-dispatch-commands`

Provisioning replaces only demo source tables and preserves intervention history.

```bash
source venv/bin/activate
pip install -r requirements.txt
export GOOGLE_CLOUD_PROJECT=cohort2track2
python infra/provision_gcp.py
python generate_data.py
```

`generate_data.py` obtains one real Open-Meteo observation per zone, writes the raw provider response to GCS, then appends the normalized observation to BigQuery.

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

Tool selection uses Gemini function calling with an explicit allowlist. Destructive requests are blocked before the tool layer. Final responses are generated only from verified tool output.

## Deploy

The deployment script enables required APIs, provisions data, deploys the Streamlit service and creates a Cloud Run weather-ingestion job:

```bash
chmod +x scripts/deploy_gcp.sh
./scripts/deploy_gcp.sh
```

Use a least-privilege Cloud Run service account with BigQuery data/query, Storage object, Vertex AI user and Pub/Sub publisher permissions. Public access is suitable only for the hackathon demo; production approval endpoints require authenticated users.

## Verify

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py heatsafe infra
pip check
```
