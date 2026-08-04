#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-cohort2track2}"
REGION="${GOOGLE_CLOUD_REGION:-asia-southeast1}"
DATASET="${HEATSAFE_DATASET:-heatsafe_data}"
RAW_BUCKET="${HEATSAFE_RAW_BUCKET:-${PROJECT_ID}-heatsafe-raw}"
GEMINI_MODEL="${HEATSAFE_GEMINI_MODEL:-gemini-3.1-flash-lite}"
PRODUCTION_BUNDLE_DATASET="${HEATSAFE_PRODUCTION_BUNDLE_DATASET:-heatsafe_event_replay_v2_20260729}"
PRODUCTION_BUNDLE_RUN_ID="${HEATSAFE_PRODUCTION_BUNDLE_RUN_ID:-8cf771e3c7d846128224504fa554885b}"
PUBLIC_SERVICE_ACCOUNT_NAME="${HEATSAFE_PUBLIC_SERVICE_ACCOUNT:-heatsafe-public-runtime}"
PUBLIC_SERVICE_ACCOUNT="${PUBLIC_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
JOB_SERVICE_ACCOUNT_NAME="${HEATSAFE_JOB_SERVICE_ACCOUNT:-heatsafe-demo}"
JOB_SERVICE_ACCOUNT="${JOB_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SEED_FLAG="${1:-}"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com aiplatform.googleapis.com bigquery.googleapis.com \
  storage.googleapis.com iam.googleapis.com \
  --project "${PROJECT_ID}"

ensure_service_account() {
  local service_account_name="$1"
  local display_name="$2"
  local service_account="$service_account_name@${PROJECT_ID}.iam.gserviceaccount.com"
  if ! gcloud iam service-accounts describe "${service_account}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${service_account_name}" \
      --display-name "${display_name}" --project "${PROJECT_ID}"
  fi
}

ensure_service_account "${PUBLIC_SERVICE_ACCOUNT_NAME}" "HeatSafe public runtime"
ensure_service_account "${JOB_SERVICE_ACCOUNT_NAME}" "HeatSafe batch jobs"

# The public service only needs query-job creation and Gemini access. Its
# dataset/table access is granted separately at the resource level.
for role in roles/bigquery.jobUser roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${PUBLIC_SERVICE_ACCOUNT}" --role "${role}" \
    --condition None --quiet >/dev/null
done

# Jobs create BigQuery ML models and write the operational dataset. Keep the
# job identity's data access scoped to this dataset rather than the project.
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${JOB_SERVICE_ACCOUNT}" \
  --role roles/bigquery.jobUser --condition None --quiet >/dev/null

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

python3 - "${PROJECT_ID}" "${DATASET}" "${JOB_SERVICE_ACCOUNT}" <<'PY'
from __future__ import annotations

import sys

from google.cloud import bigquery

project_id, dataset_id, service_account = sys.argv[1:]
client = bigquery.Client(project=project_id)
dataset = client.get_dataset(f"{project_id}.{dataset_id}")
entry = bigquery.AccessEntry("WRITER", "userByEmail", service_account)
if not any(
    item.role == entry.role
    and item.entity_type == entry.entity_type
    and item.entity_id == entry.entity_id
    for item in dataset.access_entries
):
    dataset.access_entries = [*dataset.access_entries, entry]
    client.update_dataset(dataset, ["access"])
PY

gcloud storage buckets add-iam-policy-binding "gs://${RAW_BUCKET}" \
  --member "serviceAccount:${JOB_SERVICE_ACCOUNT}" --role roles/storage.objectCreator \
  --quiet >/dev/null

RUNTIME_ENV="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION},GOOGLE_CLOUD_LOCATION=global,HEATSAFE_DATASET=${DATASET},HEATSAFE_RAW_BUCKET=${RAW_BUCKET},HEATSAFE_CURRENT_SNAPSHOT_TABLE=zone_snapshots_current,HEATSAFE_MODE=cloud,HEATSAFE_SCENARIO=heatwave,HEATSAFE_ENABLE_AI=1,HEATSAFE_GEMINI_MODEL=${GEMINI_MODEL},HEATSAFE_LIVE_FRESHNESS_MINUTES=30,HEATSAFE_PRODUCTION_BUNDLE_DATASET=${PRODUCTION_BUNDLE_DATASET},HEATSAFE_PRODUCTION_BUNDLE_RUN_ID=${PRODUCTION_BUNDLE_RUN_ID},HEATSAFE_PRODUCTION_BUNDLE_TICK_INDEX=40"

gcloud run deploy heatsafe-ops --source . --project "${PROJECT_ID}" --region "${REGION}" \
  --allow-unauthenticated --service-account "${PUBLIC_SERVICE_ACCOUNT}" --max-instances 2 \
  --labels "app=heatsafe,env=demo,managed_by=scripts" --set-env-vars "${RUNTIME_ENV}"

APP_IMAGE="$(gcloud run services describe heatsafe-ops --project "${PROJECT_ID}" \
  --region "${REGION}" --format='value(spec.template.spec.containers[0].image)')"

gcloud run jobs deploy heatsafe-live-ingest --image "${APP_IMAGE}" --project "${PROJECT_ID}" \
  --region "${REGION}" --service-account "${JOB_SERVICE_ACCOUNT}" --max-retries 2 \
  --task-timeout 10m --labels "app=heatsafe,env=demo,managed_by=scripts" \
  --command python --args generate_data.py --set-env-vars "${RUNTIME_ENV}"

gcloud run jobs deploy heatsafe-train-models --image "${APP_IMAGE}" --project "${PROJECT_ID}" \
  --region "${REGION}" --service-account "${JOB_SERVICE_ACCOUNT}" --max-retries 1 \
  --task-timeout 30m --labels "app=heatsafe,env=demo,managed_by=scripts" \
  --command python --args=infra/ml_pipeline.py,--all,--scenario,heatwave \
  --set-env-vars "${RUNTIME_ENV}"

gcloud run jobs deploy heatsafe-score-snapshot --image "${APP_IMAGE}" --project "${PROJECT_ID}" \
  --region "${REGION}" --service-account "${JOB_SERVICE_ACCOUNT}" --max-retries 1 \
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

# The paused Scheduler target authenticates as the batch identity. Scope its
# invoker permission to the one Job instead of granting roles/run.invoker on
# the whole project.
gcloud run jobs add-iam-policy-binding heatsafe-live-ingest \
  --project "${PROJECT_ID}" --region "${REGION}" \
  --member "serviceAccount:${JOB_SERVICE_ACCOUNT}" --role roles/run.invoker \
  --quiet >/dev/null

echo "HeatSafe deployed as one public demo app."
echo "Use './scripts/deploy_gcp.sh --seed-demo' only to refresh demo data explicitly."
echo "Run the heatsafe-live-ingest Cloud Run Job manually when live weather needs refreshing."
echo "Run heatsafe-train-models after changing training data; run heatsafe-score-snapshot after refreshing a snapshot."
