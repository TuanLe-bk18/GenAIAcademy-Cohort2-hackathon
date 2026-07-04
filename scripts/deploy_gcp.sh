#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-cohort2track2}"
REGION="${GOOGLE_CLOUD_REGION:-asia-southeast1}"

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  pubsub.googleapis.com \
  --project "${PROJECT_ID}"

python infra/provision_gcp.py

gcloud run deploy heatsafe-ops \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION},GOOGLE_CLOUD_LOCATION=global,HEATSAFE_MODE=cloud,HEATSAFE_SCENARIO=heatwave,HEATSAFE_ENABLE_AI=1"

gcloud run jobs deploy heatsafe-weather-ingest \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --command python \
  --args generate_data.py \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION}"

echo "Run live ingestion with:"
echo "gcloud run jobs execute heatsafe-weather-ingest --project ${PROJECT_ID} --region ${REGION} --wait"
