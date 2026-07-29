#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${HEATSAFE_LOCAL_CLOUD_PROJECT:-cohort2track2}"
REGION="${HEATSAFE_LOCAL_CLOUD_REGION:-asia-southeast1}"
SERVICE="${HEATSAFE_LOCAL_CLOUD_SERVICE:-heatsafe-ops}"
LOCAL_PORT="${HEATSAFE_LOCAL_PORT:-8501}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for command_name in gcloud jq; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Missing required command: ${command_name}" >&2
    exit 1
  fi
done

if [[ ! -x "${REPO_ROOT}/venv/bin/streamlit" ]]; then
  echo "Missing Streamlit executable: ${REPO_ROOT}/venv/bin/streamlit" >&2
  exit 1
fi

SERVICE_JSON="$(
  gcloud run services describe "${SERVICE}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --format=json
)"

while IFS=$'\t' read -r env_name env_value; do
  case "${env_name}" in
    GOOGLE_CLOUD_PROJECT|\
    GOOGLE_CLOUD_REGION|\
    GOOGLE_CLOUD_LOCATION|\
    HEATSAFE_DATASET|\
    HEATSAFE_RAW_BUCKET|\
    HEATSAFE_CURRENT_SNAPSHOT_TABLE|\
    HEATSAFE_MODE|\
    HEATSAFE_SCENARIO|\
    HEATSAFE_ENABLE_AI|\
    HEATSAFE_GEMINI_MODEL|\
    HEATSAFE_LIVE_FRESHNESS_MINUTES|\
    HEATSAFE_PRODUCTION_BUNDLE_DATASET|\
    HEATSAFE_PRODUCTION_BUNDLE_RUN_ID|\
    HEATSAFE_PRODUCTION_BUNDLE_TICK_INDEX)
      export "${env_name}=${env_value}"
      ;;
  esac
done < <(
  jq -r '
    .spec.template.spec.containers[0].env[]?
    | select(.value != null)
    | [.name, .value]
    | @tsv
  ' <<<"${SERVICE_JSON}"
)

# Production always opens on the reviewed decision point. This intentionally
# supersedes an older deployed revision that may still advertise tick 41.
export HEATSAFE_PRODUCTION_BUNDLE_TICK_INDEX=40

for required_name in \
  GOOGLE_CLOUD_PROJECT \
  HEATSAFE_MODE \
  HEATSAFE_PRODUCTION_BUNDLE_DATASET \
  HEATSAFE_PRODUCTION_BUNDLE_RUN_ID \
  HEATSAFE_PRODUCTION_BUNDLE_TICK_INDEX; do
  if [[ -z "${!required_name:-}" ]]; then
    echo "Cloud Run service is missing required env: ${required_name}" >&2
    exit 1
  fi
done

REVISION="$(jq -r '.status.latestReadyRevisionName' <<<"${SERVICE_JSON}")"
echo "Starting localhost from Cloud Run revision ${REVISION}"
echo "Pinned bundle: ${HEATSAFE_PRODUCTION_BUNDLE_DATASET}/${HEATSAFE_PRODUCTION_BUNDLE_RUN_ID}/tick-${HEATSAFE_PRODUCTION_BUNDLE_TICK_INDEX}"

cd "${REPO_ROOT}"
exec "${REPO_ROOT}/venv/bin/streamlit" run "${REPO_ROOT}/app.py" \
  --server.address=127.0.0.1 \
  --server.port="${LOCAL_PORT}" \
  --server.headless=true \
  "$@"
