#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-cohort2track2}"
REGION="${GOOGLE_CLOUD_REGION:-asia-southeast1}"
DATASET="${HEATSAFE_DATASET:-heatsafe_data}"
STAGING_DATASET="${HEATSAFE_SIMULATION_STAGING_DATASET:-heatsafe_sim_staging}"
RUNTIME_SA_NAME="${HEATSAFE_SIM_RUNTIME_SA:-heatsafe-sim-runtime}"
CONTROL_SA_NAME="${HEATSAFE_SIM_CONTROL_SA:-heatsafe-sim-control-writer}"
SCHEDULER_SA_NAME="${HEATSAFE_SIM_SCHEDULER_SA:-heatsafe-sim-scheduler}"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
CONTROL_SA="${CONTROL_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="${SCHEDULER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
TICK_JOB="heatsafe-simulation-tick"
CONTROL_JOB="heatsafe-simulation-control"
SCHEDULER_JOB="heatsafe-simulation-every-minute"
IMAGE=""
SCHEDULE_MODE="none"
PYTHON_BIN="${HEATSAFE_PYTHON_BIN:-python3}"

usage() {
  echo "Usage: $0 --image IMAGE@sha256:DIGEST [--create-paused-schedule|--enable-simulation-schedule]"
}

while (($#)); do
  case "$1" in
    --image)
      IMAGE="${2:-}"
      shift 2
      ;;
    --create-paused-schedule)
      SCHEDULE_MODE="paused"
      shift
      ;;
    --enable-simulation-schedule)
      SCHEDULE_MODE="enabled"
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${IMAGE}" != *@sha256:* ]]; then
  echo "--image must be an immutable Artifact Registry digest" >&2
  exit 2
fi
if [[ "${SCHEDULE_MODE}" == "enabled" ]]; then
  if [[ -z "${HEATSAFE_TICK_P95_SECONDS:-}" ]]; then
    echo "HEATSAFE_TICK_P95_SECONDS is required before enabling the schedule" >&2
    exit 2
  fi
  if ! awk -v value="${HEATSAFE_TICK_P95_SECONDS}" 'BEGIN { exit !(value + 0 <= 45) }'; then
    echo "Refusing one-minute schedule: tick p95 exceeds 45 seconds" >&2
    exit 2
  fi
fi

for account in "${RUNTIME_SA_NAME}" "${CONTROL_SA_NAME}" "${SCHEDULER_SA_NAME}"; do
  email="${account}@${PROJECT_ID}.iam.gserviceaccount.com"
  if ! gcloud iam service-accounts describe "${email}" \
    --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${account}" \
      --display-name "HeatSafe ${account}" \
      --project "${PROJECT_ID}"
  fi
done

for email in "${RUNTIME_SA}" "${CONTROL_SA}"; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${email}" \
    --role roles/bigquery.jobUser \
    --condition None \
    --quiet >/dev/null
done

model_resource="projects/${PROJECT_ID}/datasets/${DATASET}/models/heat_risk_escalation_model"
model_condition="expression=resource.service == 'bigquery.googleapis.com' && resource.type == 'bigquery.googleapis.com/Model' && resource.name == '${model_resource}',title=HeatSafe simulator model only,description=Phase 5 inference access"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/bigquery.dataViewer \
  --condition "${model_condition}" \
  --quiet >/dev/null

export GOOGLE_CLOUD_PROJECT="${PROJECT_ID}"
export GOOGLE_CLOUD_REGION="${REGION}"
export HEATSAFE_DATASET="${DATASET}"
export HEATSAFE_SIMULATION_STAGING_DATASET="${STAGING_DATASET}"

"${PYTHON_BIN}" infra/provision_gcp.py --schema-only-current
"${PYTHON_BIN}" infra/configure_simulation_iam.py \
  --runtime-service-account "${RUNTIME_SA}"

runtime_edit_tables=(
  simulation_scenario_locks simulation_runs simulation_ticks
  driver_simulation_state order_events weather_observations zone_operations
  demand_history driver_state_history driver_intervention_events
  simulation_control_consumptions zone_snapshots_current
  driver_current_features driver_risk_predictions zone_demand_forecasts
)
runtime_view_tables=(
  simulation_control_events intervention_proposals model_evaluations
)
control_view_tables=(intervention_proposals simulation_ticks)

for table in "${runtime_edit_tables[@]}"; do
  bq add-iam-policy-binding \
    --member "serviceAccount:${RUNTIME_SA}" \
    --role roles/bigquery.dataEditor \
    "${PROJECT_ID}:${DATASET}.${table}" >/dev/null
done
for table in "${runtime_view_tables[@]}"; do
  bq add-iam-policy-binding \
    --member "serviceAccount:${RUNTIME_SA}" \
    --role roles/bigquery.dataViewer \
    "${PROJECT_ID}:${DATASET}.${table}" >/dev/null
done
bq add-iam-policy-binding \
  --member "serviceAccount:${CONTROL_SA}" \
  --role roles/bigquery.dataEditor \
  "${PROJECT_ID}:${DATASET}.simulation_control_events" >/dev/null
for table in "${control_view_tables[@]}"; do
  bq add-iam-policy-binding \
    --member "serviceAccount:${CONTROL_SA}" \
    --role roles/bigquery.dataViewer \
    "${PROJECT_ID}:${DATASET}.${table}" >/dev/null
done

runtime_env="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION},GOOGLE_CLOUD_LOCATION=global,HEATSAFE_DATASET=${DATASET},HEATSAFE_SIMULATION_STAGING_DATASET=${STAGING_DATASET},HEATSAFE_CURRENT_SNAPSHOT_TABLE=zone_snapshots_current,HEATSAFE_MODE=cloud,HEATSAFE_SCENARIO=heatwave,HEATSAFE_ENABLE_AI=1,HEATSAFE_SIMULATION_ENABLED=1,HEATSAFE_SIMULATION_LEASE_SECONDS=360"

gcloud run jobs deploy "${TICK_JOB}" \
  --image "${IMAGE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --service-account "${RUNTIME_SA}" \
  --tasks 1 \
  --parallelism 1 \
  --max-retries 1 \
  --task-timeout 300s \
  --cpu 1 \
  --memory 1Gi \
  --labels "app=heatsafe,env=demo,component=simulation,managed_by=scripts" \
  --command python \
  --args=-m,heatsafe.simulation.cli,tick,--scenario,heatwave \
  --set-env-vars "${runtime_env}"

gcloud run jobs deploy "${CONTROL_JOB}" \
  --image "${IMAGE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --service-account "${CONTROL_SA}" \
  --tasks 1 \
  --parallelism 1 \
  --max-retries 0 \
  --task-timeout 60s \
  --cpu 1 \
  --memory 512Mi \
  --labels "app=heatsafe,env=demo,component=simulation-control,managed_by=scripts" \
  --command python \
  --args=-m,heatsafe.simulation.cli,queue-control \
  --set-env-vars "${runtime_env}"

gcloud run jobs add-iam-policy-binding "${TICK_JOB}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --member "serviceAccount:${SCHEDULER_SA}" \
  --role roles/run.invoker \
  --quiet >/dev/null

if [[ "${SCHEDULE_MODE}" != "none" ]]; then
  scheduler_uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${TICK_JOB}:run"
  scheduler_schedule="* * * * *"
  if [[ "${SCHEDULE_MODE}" == "paused" ]]; then
    # Cloud Scheduler creates HTTP jobs enabled. Use a dormant cadence until
    # the resource is paused so creation cannot race a minute boundary.
    scheduler_schedule="0 0 1 1 *"
  fi
  if gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
    --project "${PROJECT_ID}" \
    --location "${REGION}" >/dev/null 2>&1; then
    gcloud scheduler jobs update http "${SCHEDULER_JOB}" \
      --project "${PROJECT_ID}" \
      --location "${REGION}" \
      --schedule "${scheduler_schedule}" \
      --time-zone "Asia/Ho_Chi_Minh" \
      --uri "${scheduler_uri}" \
      --http-method POST \
      --oauth-service-account-email "${SCHEDULER_SA}" \
      --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" \
      --message-body '{}' \
      --attempt-deadline 30s \
      --max-retry-attempts 0
  else
    gcloud scheduler jobs create http "${SCHEDULER_JOB}" \
      --project "${PROJECT_ID}" \
      --location "${REGION}" \
      --schedule "${scheduler_schedule}" \
      --time-zone "Asia/Ho_Chi_Minh" \
      --uri "${scheduler_uri}" \
      --http-method POST \
      --oauth-service-account-email "${SCHEDULER_SA}" \
      --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" \
      --message-body '{}' \
      --attempt-deadline 30s \
      --max-retry-attempts 0
  fi
  if [[ "${SCHEDULE_MODE}" == "paused" ]]; then
    gcloud scheduler jobs pause "${SCHEDULER_JOB}" \
      --project "${PROJECT_ID}" \
      --location "${REGION}"
    gcloud scheduler jobs update http "${SCHEDULER_JOB}" \
      --project "${PROJECT_ID}" \
      --location "${REGION}" \
      --schedule "* * * * *" \
      --time-zone "Asia/Ho_Chi_Minh" >/dev/null
  else
    gcloud scheduler jobs resume "${SCHEDULER_JOB}" \
      --project "${PROJECT_ID}" \
      --location "${REGION}"
  fi
fi

echo "Phase 5 jobs deployed from ${IMAGE}."
echo "Scheduler mode: ${SCHEDULE_MODE}; legacy heatsafe-live-ingest-15m was not modified."
