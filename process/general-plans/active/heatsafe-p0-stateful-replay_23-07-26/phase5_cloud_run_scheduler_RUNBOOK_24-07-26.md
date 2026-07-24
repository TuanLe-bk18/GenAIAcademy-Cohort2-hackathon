# Phase 5 Cloud Run and Scheduler Runbook

## Deploy

Build once, resolve the immutable digest, then deploy the two jobs:

```bash
gcloud builds submit \
  --project cohort2track2 \
  --tag asia-southeast1-docker.pkg.dev/cohort2track2/cloud-run-source-deploy/heatsafe-simulation:phase5

IMAGE_DIGEST="$(gcloud artifacts docker images describe \
  asia-southeast1-docker.pkg.dev/cohort2track2/cloud-run-source-deploy/heatsafe-simulation:phase5 \
  --project cohort2track2 \
  --format='value(image_summary.digest)')"

./scripts/deploy_simulation_gcp.sh \
  --image "asia-southeast1-docker.pkg.dev/cohort2track2/cloud-run-source-deploy/heatsafe-simulation@${IMAGE_DIGEST}" \
  --create-paused-schedule
```

The deployment is additive. It does not mutate the public service, legacy jobs,
or the paused `heatsafe-live-ingest-15m` scheduler.

## Start and manually measure

```bash
gcloud run jobs execute heatsafe-simulation-tick \
  --project cohort2track2 \
  --region asia-southeast1 \
  --wait

gcloud run jobs executions list \
  --job heatsafe-simulation-tick \
  --project cohort2track2 \
  --region asia-southeast1
```

Do not enable the every-minute schedule until at least five representative
executions establish p95 duration at or below 45 seconds.

## Scheduler operations

```bash
gcloud scheduler jobs pause heatsafe-simulation-every-minute \
  --project cohort2track2 \
  --location asia-southeast1

gcloud scheduler jobs resume heatsafe-simulation-every-minute \
  --project cohort2track2 \
  --location asia-southeast1
```

Cloud Scheduler refuses a forced run while a job is paused. To test its OAuth
dispatch without exposing the every-minute cadence, temporarily use a dormant
schedule, resume, force-run, pause immediately, and restore the desired cron:

```bash
gcloud scheduler jobs update http heatsafe-simulation-every-minute \
  --project cohort2track2 --location asia-southeast1 \
  --schedule '0 0 1 1 *' --time-zone Asia/Ho_Chi_Minh
gcloud scheduler jobs resume heatsafe-simulation-every-minute \
  --project cohort2track2 --location asia-southeast1
gcloud scheduler jobs run heatsafe-simulation-every-minute \
  --project cohort2track2 --location asia-southeast1
gcloud scheduler jobs pause heatsafe-simulation-every-minute \
  --project cohort2track2 --location asia-southeast1
gcloud scheduler jobs update http heatsafe-simulation-every-minute \
  --project cohort2track2 --location asia-southeast1 \
  --schedule '* * * * *' --time-zone Asia/Ho_Chi_Minh
```

To enable through the guarded deployment entry point, pass the measured p95:

```bash
HEATSAFE_TICK_P95_SECONDS=42 \
./scripts/deploy_simulation_gcp.sh \
  --image "$PINNED_IMAGE" \
  --enable-simulation-schedule
```

## Terminal and incident response

The tick job emits `terminal_signal=true` with `outcome=NO_OP_TERMINAL` after
the run is complete. The operator must pause or delete the schedule within five
minutes. Later invocations remain successful logical no-ops.

Pause the schedule first during rollback. Wait for active executions, then
delete only Phase 5 resources if required:

```bash
gcloud scheduler jobs pause heatsafe-simulation-every-minute \
  --project cohort2track2 --location asia-southeast1
gcloud run jobs executions list --job heatsafe-simulation-tick \
  --project cohort2track2 --region asia-southeast1
gcloud scheduler jobs delete heatsafe-simulation-every-minute \
  --project cohort2track2 --location asia-southeast1
gcloud run jobs delete heatsafe-simulation-control \
  --project cohort2track2 --region asia-southeast1
gcloud run jobs delete heatsafe-simulation-tick \
  --project cohort2track2 --region asia-southeast1
```

Retain the pinned image and BigQuery evidence. Do not disable the Scheduler API
and do not delete or resume `heatsafe-live-ingest-15m`.
