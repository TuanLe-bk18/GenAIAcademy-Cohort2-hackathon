#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-cohort2track2}"
REGION="${GOOGLE_CLOUD_REGION:-asia-southeast1}"
DATASET="${HEATSAFE_DATASET:-heatsafe_data}"
STAGING_DATASET="${HEATSAFE_SIMULATION_STAGING_DATASET:-heatsafe_sim_staging}"
CHECKPOINT_BUCKET="${HEATSAFE_SIMULATION_CHECKPOINT_BUCKET:-${PROJECT_ID}-heatsafe-sim-checkpoints}"
MODEL_DATASET="${HEATSAFE_SIMULATION_MODEL_DATASET:-${PROJECT_ID}.${DATASET}}"
RUNTIME_SA_NAME="${HEATSAFE_SIM_RUNTIME_SA:-heatsafe-sim-runtime}"
CONTROL_SA_NAME="${HEATSAFE_SIM_CONTROL_SA:-heatsafe-sim-control-writer}"
SCHEDULER_SA_NAME="${HEATSAFE_SIM_SCHEDULER_SA:-heatsafe-sim-scheduler}"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
CONTROL_SA="${CONTROL_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="${SCHEDULER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
TICK_JOB="heatsafe-simulation-tick"
CONTROL_JOB="heatsafe-simulation-control"
SCHEDULER_JOB=""
RESOURCE_TAG=""
SCHEDULER_PROFILE="accelerated-replay"
SCHEDULER_CRON="*/2 * * * *"
IMAGE=""
SCHEDULE_MODE="none"
BOOTSTRAP_CHECKPOINTS=0
PYTHON_BIN="${HEATSAFE_PYTHON_BIN:-python3}"

usage() {
  echo "Usage: $0 --image IMAGE@sha256:DIGEST [--resource-tag YYYYMMDDhhmmss] [--scheduler-profile accelerated-replay|real-operations] [--bootstrap-checkpoints] [--create-paused-schedule|--enable-proof-schedule|--enable-simulation-schedule]"
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
    --bootstrap-checkpoints)
      BOOTSTRAP_CHECKPOINTS=1
      shift
      ;;
    --resource-tag)
      RESOURCE_TAG="${2:-}"
      shift 2
      ;;
    --scheduler-profile)
      SCHEDULER_PROFILE="${2:-}"
      shift 2
      ;;
    --enable-simulation-schedule)
      SCHEDULE_MODE="enabled"
      shift
      ;;
    --enable-proof-schedule)
      SCHEDULE_MODE="proof"
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
if [[ ! "${MODEL_DATASET}" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]\.[a-z][a-z0-9_]{0,127}$ ]]; then
  echo "HEATSAFE_SIMULATION_MODEL_DATASET must be project.dataset" >&2
  exit 2
fi
MODEL_PROJECT="${MODEL_DATASET%%.*}"
MODEL_DATASET_ID="${MODEL_DATASET#*.}"
if [[ -n "${RESOURCE_TAG}" ]]; then
  if [[ ! "${RESOURCE_TAG}" =~ ^[0-9]{14}$ ]]; then
    echo "--resource-tag must contain exactly 14 UTC timestamp digits" >&2
    exit 2
  fi
  TICK_JOB="heatsafe-simulation-tick-${RESOURCE_TAG}"
  CONTROL_JOB="heatsafe-simulation-control-${RESOURCE_TAG}"
fi
case "${SCHEDULER_PROFILE}" in
  accelerated-replay)
    SCHEDULER_CRON="*/2 * * * *"
    [[ -z "${RESOURCE_TAG}" ]] || \
      SCHEDULER_JOB="heatsafe-simulation-replay-2m-${RESOURCE_TAG}"
    ;;
  real-operations)
    SCHEDULER_CRON="*/15 * * * *"
    [[ -z "${RESOURCE_TAG}" ]] || \
      SCHEDULER_JOB="heatsafe-simulation-real-ops-15m-${RESOURCE_TAG}"
    if [[ "${SCHEDULE_MODE}" == "enabled" || "${SCHEDULE_MODE}" == "proof" ]]; then
      echo "real-operations profile may only be created PAUSED in Phase 5R" >&2
      exit 2
    fi
    ;;
  *)
    echo "--scheduler-profile must be accelerated-replay or real-operations" >&2
    exit 2
    ;;
esac
if [[ "${SCHEDULE_MODE}" != "none" && -z "${RESOURCE_TAG}" ]]; then
  echo "Scheduler creation requires a unique --resource-tag" >&2
  exit 2
fi
if [[ "${SCHEDULE_MODE}" == "enabled" || "${SCHEDULE_MODE}" == "proof" ]]; then
  if [[ -z "${HEATSAFE_TICK_P95_SECONDS:-}" || -z "${HEATSAFE_TICK_MAX_SECONDS:-}" ]]; then
    echo "HEATSAFE_TICK_P95_SECONDS and HEATSAFE_TICK_MAX_SECONDS are required before enabling the schedule" >&2
    exit 2
  fi
  if ! awk -v value="${HEATSAFE_TICK_P95_SECONDS}" 'BEGIN { exit !(value + 0 <= 105) }'; then
    echo "Refusing accelerated replay: FULL tick p95 exceeds 105 seconds" >&2
    exit 2
  fi
  if ! awk -v value="${HEATSAFE_TICK_MAX_SECONDS}" 'BEGIN { exit !(value + 0 < 120) }'; then
    echo "Refusing accelerated replay: dispatch-to-terminal maximum is not below 120 seconds" >&2
    exit 2
  fi
  if [[ "${HEATSAFE_REPLAY_ZERO_OVERLAP:-}" != "1" ]]; then
    echo "Refusing accelerated replay: corrected baseline must prove zero overlap" >&2
    exit 2
  fi
  if [[ "${SCHEDULE_MODE}" == "enabled" && "${HEATSAFE_REPLAY_96_PLUS_1_VERIFIED:-}" != "1" ]]; then
    echo "Refusing recurring execution: completed 96+1 evidence is required" >&2
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

model_resource="projects/${MODEL_PROJECT}/datasets/${MODEL_DATASET_ID}/models/heat_risk_escalation_model"
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

if [[ "${BOOTSTRAP_CHECKPOINTS}" == "1" ]]; then
  HEATSAFE_SIMULATION_CHECKPOINT_BUCKET="${CHECKPOINT_BUCKET}" \
    "${PYTHON_BIN}" infra/provision_gcp.py --bootstrap-checkpoints
fi
gcloud storage buckets describe "gs://${CHECKPOINT_BUCKET}" \
  --project "${PROJECT_ID}" >/dev/null
for role in roles/storage.objectCreator roles/storage.objectViewer; do
  gcloud storage buckets add-iam-policy-binding "gs://${CHECKPOINT_BUCKET}" \
    --member "serviceAccount:${RUNTIME_SA}" \
    --role "${role}" \
    --project "${PROJECT_ID}" \
    --quiet >/dev/null
done

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

runtime_env="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION},GOOGLE_CLOUD_LOCATION=global,HEATSAFE_DATASET=${DATASET},HEATSAFE_SIMULATION_STAGING_DATASET=${STAGING_DATASET},HEATSAFE_SIMULATION_CHECKPOINT_BUCKET=${CHECKPOINT_BUCKET},HEATSAFE_SIMULATION_STATE_MODE=${HEATSAFE_SIMULATION_STATE_MODE:-oracle},HEATSAFE_SIMULATION_STAGING_WORKERS=${HEATSAFE_SIMULATION_STAGING_WORKERS:-1},HEATSAFE_SIMULATION_MODEL_DATASET=${MODEL_DATASET},HEATSAFE_SIMULATION_COMPONENT_TELEMETRY=1,HEATSAFE_CURRENT_SNAPSHOT_TABLE=zone_snapshots_current,HEATSAFE_MODE=cloud,HEATSAFE_SCENARIO=heatwave,HEATSAFE_ENABLE_AI=1,HEATSAFE_SIMULATION_ENABLED=1,HEATSAFE_SIMULATION_LEASE_SECONDS=360"

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
  scheduler_schedule="${SCHEDULER_CRON}"
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
      --schedule "${SCHEDULER_CRON}" \
      --time-zone "Asia/Ho_Chi_Minh" >/dev/null
  else
    gcloud scheduler jobs resume "${SCHEDULER_JOB}" \
      --project "${PROJECT_ID}" \
      --location "${REGION}"
  fi
fi

echo "Phase 5 jobs deployed from ${IMAGE}."
echo "Tick job: ${TICK_JOB}; control job: ${CONTROL_JOB}."
echo "Scheduler: ${SCHEDULER_JOB:-not-created}; profile: ${SCHEDULER_PROFILE}; mode: ${SCHEDULE_MODE}."
echo "Legacy heatsafe-simulation-every-minute and heatsafe-live-ingest-15m were not modified."
