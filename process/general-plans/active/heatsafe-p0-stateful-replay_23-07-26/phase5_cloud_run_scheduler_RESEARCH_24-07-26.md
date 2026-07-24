# Phase 5 Cloud Run Job and Scheduler Research

**Date:** 24-07-2026
**Mode:** RESEARCH complete; EXECUTE authorized
**Mutation during research:** none

## Inter-phase process update

Phase 4 was user-confirmed and recorded as `✅ VERIFIED`. Phase 5 is authorized
for research and execution. Its boundary remains:

- add two simulation-specific Cloud Run jobs and three workload identities;
- preserve the public service, legacy jobs, and the paused
  `heatsafe-live-ingest-15m` scheduler;
- make the new every-minute scheduler opt-in and reversible;
- do not revoke the legacy `heatsafe-demo` roles until every workload using
  that identity has migrated and passed smoke tests;
- leave Phase 5 in testing until recurring execution is user-confirmed.

The installed ADAS workflow command surface was unavailable in this shell:
`adas profile-task`, `adas level`, and `adas authorize` each exited `127`
(`command not found`). The accepted umbrella plan, explicit user authorization,
and phase contract are therefore the execution authority fallback.

## Current provider inventory

Read-only inspection found:

- project `cohort2track2`, region `asia-southeast1`;
- Cloud Run, Scheduler, Cloud Build, Artifact Registry, BigQuery, and IAM APIs
  already enabled;
- public service `heatsafe-ops`;
- legacy jobs `heatsafe-live-ingest`, `heatsafe-train-models`, and
  `heatsafe-score-snapshot`;
- all four legacy workloads use
  `heatsafe-demo@cohort2track2.iam.gserviceaccount.com`;
- their current image digest is
  `sha256:c97d7a0e4bcb099b8ca4141edb209d404a414ea3de0f85aa899bda8f549c25b3`;
- `heatsafe-demo` retains project-wide BigQuery Data Editor, BigQuery Job User,
  Vertex AI User, and Cloud Run Invoker;
- scheduler `heatsafe-live-ingest-15m` is `PAUSED`, runs every 15 minutes in
  `Asia/Ho_Chi_Minh`, and targets the legacy ingestion job;
- the shared `heatsafe_data` dataset has the legacy tables/model but does not
  yet have the Phase 3 simulation tables and nullable schema extensions.

## Official contract verification

Current Google Cloud documentation confirms:

- Scheduler invokes a Cloud Run Job with HTTP `POST` to the v2 `:run` URI and
  an OAuth service-account token;
- the caller needs `roles/run.invoker` on the target job;
- Scheduler can rarely issue duplicate requests, so tick execution must be
  idempotent;
- Cloud Run Jobs expose `CLOUD_RUN_JOB`, `CLOUD_RUN_EXECUTION`,
  `CLOUD_RUN_TASK_INDEX`, and `CLOUD_RUN_TASK_ATTEMPT`;
- BigQuery table/view IAM may be resource-scoped;
- BigQuery IAM cannot be attached directly to an individual model, but a
  dataset-level IAM Condition may select the exact model using
  `resource.type`, `resource.name`, and `resource.service`.

Sources:

- <https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule>
- <https://docs.cloud.google.com/scheduler/docs/creating>
- <https://docs.cloud.google.com/sdk/gcloud/reference/scheduler/jobs/create/http>
- <https://docs.cloud.google.com/run/docs/container-contract>
- <https://docs.cloud.google.com/run/docs/configuring/task-timeout>
- <https://docs.cloud.google.com/bigquery/docs/control-access-to-resources-iam>
- <https://docs.cloud.google.com/bigquery/docs/conditions>

Installed `gcloud 575.0.0` exposes the required job and Scheduler flags.

## IAM design correction

The Phase 4 publisher creates short-lived load staging tables before the fenced
transaction. If those tables remain in `heatsafe_data`, the runtime needs
dataset-level create permission. Granting dataset-wide BigQuery Data Editor
would also allow it to mutate authoritative `simulation_control_events`, which
would invalidate the required negative IAM proof.

Phase 5 therefore uses:

- authoritative dataset: `cohort2track2.heatsafe_data`;
- staging dataset: `cohort2track2.heatsafe_sim_staging`;
- staging default table expiration: one hour;
- runtime dataset-level Data Editor only on staging;
- exact table-level viewer/editor grants on authoritative tables;
- a conditional project IAM entry that permits Data Viewer only when the
  resource is the `heat_risk_escalation_model` model;
- control writer editor only on `simulation_control_events`;
- Scheduler caller invoker only on `heatsafe-simulation-tick`.

This correction preserves both load-job functionality and the negative control
write contract.

## Execution decisions

1. Build one image, resolve its Artifact Registry digest, and deploy both jobs
   from that immutable digest.
2. Provision the configured authoritative schema through an explicit
   `--schema-only-current` path that never accesses Cloud Storage or seeds data.
3. Deploy tick with one task, parallelism one, one retry, 300-second timeout,
   one CPU, and 1 GiB memory.
4. Deploy control with one task, parallelism one, no retry, and 60-second
   timeout.
5. Emit structured tick logs with execution/task attempt, run/tick/snapshot
   lineage, checksum, duration, outcome, and terminal signal.
6. Treat fresh-lease overlap and post-completion dispatch as successful bounded
   no-ops.
7. Create the new scheduler paused for evidence only after the jobs pass smoke
   tests. Enable it only if representative execution p95 is at most 45 seconds.
8. Do not modify the legacy scheduler or broad legacy identity during Phase 5.

## Gates before recurring execution

- local full suite and deployment contract green;
- configured schema provisioned additively;
- IAM readback matches the matrix and negative permissions are demonstrated;
- one manual job invocation advances exactly one tick;
- two overlapping invocations advance at most one logical tick;
- Cloud Logging lineage matches BigQuery;
- representative p95 tick duration is at most 45 seconds;
- paused Scheduler force-run works and pause stops further advancement;
- user confirms recurring execution before Phase 6.
