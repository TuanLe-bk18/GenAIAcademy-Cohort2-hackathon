#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-cohort2track2}"
REGION="${GOOGLE_CLOUD_REGION:-asia-southeast1}"
DATASET="${HEATSAFE_DATASET:-heatsafe_data}"
RAW_BUCKET="${HEATSAFE_RAW_BUCKET:-${PROJECT_ID}-heatsafe-raw}"
GEMINI_MODEL="${HEATSAFE_GEMINI_MODEL:-gemini-3.1-flash-lite}"
PRODUCTION_BUNDLE_DATASET="${HEATSAFE_PRODUCTION_BUNDLE_DATASET:-heatsafe_event_replay_v2_20260729}"
PRODUCTION_BUNDLE_RUN_ID="${HEATSAFE_PRODUCTION_BUNDLE_RUN_ID:-8cf771e3c7d846128224504fa554885b}"
SERVICE_ACCOUNT_NAME="${HEATSAFE_SERVICE_ACCOUNT:-heatsafe-demo}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SEED_FLAG="${1:-}"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com aiplatform.googleapis.com bigquery.googleapis.com \
  storage.googleapis.com iam.googleapis.com \
  --project "${PROJECT_ID}"

if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SERVICE_ACCOUNT_NAME}" \
    --display-name "HeatSafe hackathon demo" --project "${PROJECT_ID}"
fi

for role in roles/bigquery.jobUser roles/bigquery.dataEditor roles/aiplatform.user roles/run.invoker; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${SERVICE_ACCOUNT}" --role "${role}" \
    --condition None --quiet >/dev/null
done

export GOOGLE_CLOUD_PROJECT="${PROJECT_ID}"
export GOOGLE_CLOUD_REGION="${REGION}"
export HEATSAFE_DATASET="${DATASET}"
export HEATSAFE_RAW_BUCKET="${RAW_BUCKET}"

if [[ "${SEED_FLAG}" == "--seed-demo" ]] || \
  ! bq --project_id="${PROJECT_ID}" --location="${REGION}" show --model \
    "${PROJECT_ID}:${DATASET}.heat_risk_escalation_model" >/dev/null 2>&1; then
  python3 infra/provision_gcp.py --seed-demo
else
  python3 infra/provision_gcp.py
fi

gcloud storage buckets add-iam-policy-binding "gs://${RAW_BUCKET}" \
  --member "serviceAccount:${SERVICE_ACCOUNT}" --role roles/storage.objectAdmin \
  --quiet >/dev/null

RUNTIME_ENV="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION},GOOGLE_CLOUD_LOCATION=global,HEATSAFE_DATASET=${DATASET},HEATSAFE_RAW_BUCKET=${RAW_BUCKET},HEATSAFE_CURRENT_SNAPSHOT_TABLE=zone_snapshots_current,HEATSAFE_MODE=cloud,HEATSAFE_SCENARIO=heatwave,HEATSAFE_ENABLE_AI=1,HEATSAFE_GEMINI_MODEL=${GEMINI_MODEL},HEATSAFE_LIVE_FRESHNESS_MINUTES=30,HEATSAFE_PRODUCTION_BUNDLE_DATASET=${PRODUCTION_BUNDLE_DATASET},HEATSAFE_PRODUCTION_BUNDLE_RUN_ID=${PRODUCTION_BUNDLE_RUN_ID},HEATSAFE_PRODUCTION_BUNDLE_TICK_INDEX=40"

gcloud run deploy heatsafe-ops --source . --project "${PROJECT_ID}" --region "${REGION}" \
  --allow-unauthenticated --service-account "${SERVICE_ACCOUNT}" --max-instances 2 \
  --labels "app=heatsafe,env=demo,managed_by=scripts" --set-env-vars "${RUNTIME_ENV}"

APP_IMAGE="$(gcloud run services describe heatsafe-ops --project "${PROJECT_ID}" \
  --region "${REGION}" --format='value(spec.template.spec.containers[0].image)')"

gcloud run jobs deploy heatsafe-live-ingest --image "${APP_IMAGE}" --project "${PROJECT_ID}" \
  --region "${REGION}" --service-account "${SERVICE_ACCOUNT}" --max-retries 2 \
  --task-timeout 10m --labels "app=heatsafe,env=demo,managed_by=scripts" \
  --command python --args generate_data.py --set-env-vars "${RUNTIME_ENV}"

gcloud run jobs deploy heatsafe-train-models --image "${APP_IMAGE}" --project "${PROJECT_ID}" \
  --region "${REGION}" --service-account "${SERVICE_ACCOUNT}" --max-retries 1 \
  --task-timeout 30m --labels "app=heatsafe,env=demo,managed_by=scripts" \
  --command python --args=infra/ml_pipeline.py,--all,--scenario,heatwave \
  --set-env-vars "${RUNTIME_ENV}"

gcloud run jobs deploy heatsafe-score-snapshot --image "${APP_IMAGE}" --project "${PROJECT_ID}" \
  --region "${REGION}" --service-account "${SERVICE_ACCOUNT}" --max-retries 1 \
  --task-timeout 15m --labels "app=heatsafe,env=demo,managed_by=scripts" \
  --command python --args=infra/ml_pipeline.py,--score,--scenario,heatwave \
  --set-env-vars "${RUNTIME_ENV}"

if [[ "${SEED_FLAG}" == "--seed-demo" ]]; then
  gcloud run jobs execute heatsafe-train-models --project "${PROJECT_ID}" \
    --region "${REGION}" --wait
else
  gcloud run jobs execute heatsafe-score-snapshot --project "${PROJECT_ID}" \
    --region "${REGION}" --wait
fi

echo "HeatSafe deployed as one public demo app."
echo "Use './scripts/deploy_gcp.sh --seed-demo' only to refresh demo data explicitly."
echo "Run the heatsafe-live-ingest Cloud Run Job manually when live weather needs refreshing."
echo "Run heatsafe-train-models after changing training data; run heatsafe-score-snapshot after refreshing a snapshot."
