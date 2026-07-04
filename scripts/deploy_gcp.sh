#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-cohort2track2}"
REGION="${GOOGLE_CLOUD_REGION:-asia-southeast1}"
DATASET="${HEATSAFE_DATASET:-heatsafe_data}"
RAW_BUCKET="${HEATSAFE_RAW_BUCKET:-${PROJECT_ID}-heatsafe-raw}"
SERVICE_ACCOUNT_NAME="${HEATSAFE_SERVICE_ACCOUNT:-heatsafe-demo}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SEED_FLAG="${1:-}"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com aiplatform.googleapis.com bigquery.googleapis.com \
  storage.googleapis.com cloudscheduler.googleapis.com iam.googleapis.com \
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

if [[ "${SEED_FLAG}" == "--seed-demo" ]]; then
  python3 infra/provision_gcp.py --seed-demo
else
  python3 infra/provision_gcp.py
fi

gcloud storage buckets add-iam-policy-binding "gs://${RAW_BUCKET}" \
  --member "serviceAccount:${SERVICE_ACCOUNT}" --role roles/storage.objectAdmin \
  --quiet >/dev/null

RUNTIME_ENV="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION},GOOGLE_CLOUD_LOCATION=global,HEATSAFE_DATASET=${DATASET},HEATSAFE_RAW_BUCKET=${RAW_BUCKET},HEATSAFE_CURRENT_SNAPSHOT_TABLE=zone_snapshots_current,HEATSAFE_MODE=cloud,HEATSAFE_SCENARIO=heatwave,HEATSAFE_ENABLE_AI=1,HEATSAFE_LIVE_FRESHNESS_MINUTES=30"

gcloud run deploy heatsafe-ops --source . --project "${PROJECT_ID}" --region "${REGION}" \
  --allow-unauthenticated --service-account "${SERVICE_ACCOUNT}" --max-instances 2 \
  --labels "app=heatsafe,env=demo,managed_by=scripts" --set-env-vars "${RUNTIME_ENV}"

gcloud run jobs deploy heatsafe-live-ingest --source . --project "${PROJECT_ID}" \
  --region "${REGION}" --service-account "${SERVICE_ACCOUNT}" --max-retries 2 \
  --task-timeout 10m --labels "app=heatsafe,env=demo,managed_by=scripts" \
  --command python --args generate_data.py --set-env-vars "${RUNTIME_ENV}"

SCHEDULER_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/heatsafe-live-ingest:run"
if gcloud scheduler jobs describe heatsafe-live-ingest-15m \
  --project "${PROJECT_ID}" --location "${REGION}" >/dev/null 2>&1; then
  SCHEDULER_ACTION="update"
else
  SCHEDULER_ACTION="create"
fi
gcloud scheduler jobs "${SCHEDULER_ACTION}" http heatsafe-live-ingest-15m \
  --project "${PROJECT_ID}" --location "${REGION}" --schedule "*/15 * * * *" \
  --time-zone "Asia/Ho_Chi_Minh" --uri "${SCHEDULER_URI}" --http-method POST \
  --oauth-service-account-email "${SERVICE_ACCOUNT}" --quiet

echo "HeatSafe deployed as one public demo app."
echo "Use './scripts/deploy_gcp.sh --seed-demo' only to refresh demo data explicitly."
