# HeatSafe AI Ops

HeatSafe AI Ops is a GCP-native decision-support platform for protecting two-wheel ride-hailing drivers during extreme heat while keeping platform cost, fulfillment, and pickup delay within explicit operational guardrails.

> **Safety notice:** HeatSafe is a simulated operations demo. Heat Index is used as a screening indicator; risk scores and projected intervention benefits are not medical diagnoses or proof of reduced health incidents. The application does not dispatch drivers, send notifications, or execute real-world interventions.

## What the current app provides

The Streamlit operator console has two modes and two workspace surfaces:

| Mode | Purpose | Evidence source |
|---|---|---|
| **PRODUCTION** | Review the current city-wide SafePause plan, inspect zone evidence, and record a simulated `ACTIVATE` or `CONTINUE` decision. | A pinned, integrity-checked BigQuery replay bundle at decision tick **40** when configured; otherwise the verified local Production window at **K=45**. |
| **EVENT REPLAY** | Present the reviewed Hanoi heatwave from 09:15 to 13:15, compare `With SafePause` and `Without SafePause`, and inspect the policy outcome every 15 minutes. | A precomputed deterministic timeline generated from the stateful simulation engine. Browser playback does not rerun the optimizer. |

The workspace can switch between:

- **Operations** — city map, priority areas, safety KPIs, recommendation, guardrails, and simulated action controls.
- **Evidence & history** — model lineage, forecasts, proposal evidence, and the decision/audit trail.

Both modes include an evidence-bound Copilot. Production uses allowlisted operational tools and Gemini when enabled; Event Replay answers only from the selected, validated replay frame.

## Architecture

```text
Weather + driver telemetry + intervention outcomes
                         |
                         v
              Cloud Storage + BigQuery
        raw lineage, snapshots, simulation ledger,
          forecasts, predictions, and audit events
                         |
             +-----------+------------+
             |                        |
             v                        v
    BigQuery ML risk model     TimesFM AI.FORECAST
             |                        |
             +-----------+------------+
                         v
          action-conditioned SafePause optimizer
       mandatory coverage + cost + service guardrails
                         |
              verified Production bundle
                         |
                         v
        Cloud Run / Streamlit operator console
             |                        |
             v                        v
      simulated audit action    Gemini Copilot
                                allowlisted tools
```

### Main components

- **Cloud Storage** — immutable provider payloads, replay inputs, and compressed simulation checkpoints with checksum lineage.
- **BigQuery** — operational snapshots, driver features, intervention outcomes, simulation runs/ticks, forecasts, predictions, proposals, and audit events.
- **BigQuery ML** — boosted-tree driver risk scoring and TimesFM demand forecasting.
- **Stateful simulation engine** — advances driver exposure, orders, service metrics, and SafePause controls in deterministic 15-minute ticks.
- **SafePause optimizer** — evaluates action timing and duration against safety, budget, fulfillment, and ETA constraints.
- **Verified cloud bundle loader** — validates the pinned five-tick slice before Production evidence is exposed.
- **Vertex AI Gemini Copilot** — explains only allowlisted evidence and falls back to deterministic or monitoring-only responses when evidence cannot be verified.
- **Cloud Run** — hosts the Streamlit app and manually triggered ingestion/training/scoring jobs.

## SafePause policy and guardrails

SafePause compares the no-action baseline with action-conditioned pause options for each eligible driver:

- start delay: `0`, `15`, `30`, or `45` minutes;
- pause duration: `15` or `30` minutes;
- mandatory protection once continuous extreme-heat exposure reaches **240 minutes**;
- staggered waves to avoid removing supply all at once;
- forecast evaluation under median and upper-demand conditions.

A recommendation is actionable only when all required evidence has matching lineage and the plan satisfies:

1. **Mandatory coverage:** all currently mandatory drivers are covered.
2. **Budget:** projected net platform cost remains within the configured cap.
3. **Fulfillment:** upper-demand degradation remains within the service guardrail.
4. **ETA:** upper-demand pickup-delay increase remains within the service guardrail.

If evidence is incomplete or no feasible plan exists, the system fails closed with monitoring-only states such as `MODEL_UNAVAILABLE`, `EVIDENCE_UNAVAILABLE`, `NO_FEASIBLE`, or `SAFETY_CAPACITY_BREACH` instead of fabricating a recommendation.

### Rolling Event Replay policy

The Event Replay policy evaluates a narrow 15-minute supplement at each tick. It:

- reserves a driver as soon as a control is scheduled so the driver cannot be selected twice;
- prioritizes drivers already at the mandatory threshold;
- can protect drivers forecast to cross the threshold before the next tick;
- maintains a replay-wide P95 budget ledger and reserves part of the budget for mandatory protection;
- reports a safety-capacity breach explicitly when mandatory demand exceeds available budget/capacity.

## Verified Production evidence

A cloud-backed Production bundle is enabled when both of these variables are set:

```text
HEATSAFE_PRODUCTION_BUNDLE_DATASET
HEATSAFE_PRODUCTION_BUNDLE_RUN_ID
```

`HEATSAFE_PRODUCTION_BUNDLE_TICK_INDEX` defaults to `40`. Before loading the selected snapshot, HeatSafe verifies that:

- the run is paused and has no pending scoring operation;
- ticks `37` through `41` all completed and were scored;
- every tick uses generator lineage `stateful-replay-v2`;
- the selected tick has snapshot and forecast lineage;
- BigQuery ML model lineage and the TimesFM context version are present;
- all loaded bundle components match the configured run and tick.

A failed integrity check stops Production from presenting stale or mixed evidence.

When no cloud bundle is configured, the app uses the checked-in deterministic Production window (`data/scenarios/hanoi_heatwave_v1/production_window/`) and advances its verified warm checkpoint from tick `37` to the local decision point `K=45`.

## Run locally

### Prerequisites

- Python 3.12
- a virtual environment with dependencies from `requirements.txt`
- Google Cloud CLI and Application Default Credentials for cloud-backed Production
- `jq` when using the Cloud Run configuration launcher

Install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Recommended: mirror the deployed Cloud Run configuration

```bash
gcloud auth login
gcloud auth application-default login
./scripts/run_local_like_cloud_run.sh
```

The launcher reads only allowlisted, non-secret environment settings from the deployed `heatsafe-ops` service, pins Production to tick `40`, and starts the app at <http://127.0.0.1:8501>. It does not mutate Cloud Run or copy secrets.

Optional launcher overrides:

```bash
HEATSAFE_LOCAL_CLOUD_PROJECT=cohort2track2 \
HEATSAFE_LOCAL_CLOUD_REGION=asia-southeast1 \
HEATSAFE_LOCAL_CLOUD_SERVICE=heatsafe-ops \
HEATSAFE_LOCAL_PORT=8501 \
./scripts/run_local_like_cloud_run.sh
```

### Local deterministic fallback

To run without the configured cloud bundle or Gemini:

```bash
HEATSAFE_MODE=snapshot HEATSAFE_ENABLE_AI=0 streamlit run app.py
```

Production then uses the checked-in verified scenario artifacts. Event Replay remains available from its precomputed timeline.

## Provision and deploy to Google Cloud

Provision demo resources and train/score the initial models:

```bash
source venv/bin/activate
export GOOGLE_CLOUD_PROJECT=cohort2track2
python infra/provision_gcp.py --seed-demo
python infra/ml_pipeline.py --all --scenario heatwave
```

Deploy the public Cloud Run demo and its manual jobs:

```bash
chmod +x scripts/deploy_gcp.sh
./scripts/deploy_gcp.sh --seed-demo
```

The deployment creates or updates:

- Cloud Run service `heatsafe-ops`;
- job `heatsafe-live-ingest`;
- job `heatsafe-train-models`;
- job `heatsafe-score-snapshot`.

Use `--seed-demo` only when demo data/models must be refreshed explicitly. The deployed ingestion, training, and scoring jobs are intentionally run manually rather than through Cloud Scheduler.

## Project structure

```text
app.py                         Streamlit entry point and mode orchestration
heatsafe/                      domain models, repositories, optimizer, Copilot, UI
heatsafe/simulation/           deterministic stateful simulation runtime
infra/                         GCP provisioning and BigQuery ML pipeline
scripts/                       replay builders, cloud bundle runner, deploy/launch tools
data/scenarios/                reviewed scenario, checkpoint, and replay artifacts
tests/                         unit and integration-style contract tests
Dockerfile                     Python 3.12 Cloud Run image
```

## Validation

Run the committed test suite and static runtime checks:

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py heatsafe infra
pip check
```

## Key runtime configuration

| Variable | Default | Purpose |
|---|---:|---|
| `GOOGLE_CLOUD_PROJECT` | `cohort2track2` | GCP project ID |
| `GOOGLE_CLOUD_REGION` | `asia-southeast1` | Cloud Run and BigQuery region |
| `GOOGLE_CLOUD_LOCATION` | `global` | Vertex AI location |
| `HEATSAFE_DATASET` | `heatsafe_data` | Primary BigQuery dataset |
| `HEATSAFE_MODE` | `auto` | Repository mode: `auto`, `cloud`, or `snapshot` |
| `HEATSAFE_SCENARIO` | `heatwave` | Operational scenario: `heatwave` or `live` |
| `HEATSAFE_ENABLE_AI` | `1` | Enables Gemini-backed Copilot responses |
| `HEATSAFE_GEMINI_MODEL` | `gemini-3.1-flash-lite` | Allowlisted Gemini model |
| `HEATSAFE_PRODUCTION_BUNDLE_DATASET` | unset | BigQuery dataset containing the verified bundle |
| `HEATSAFE_PRODUCTION_BUNDLE_RUN_ID` | unset | Lowercase 32-character bundle run ID |
| `HEATSAFE_PRODUCTION_BUNDLE_TICK_INDEX` | `40` | Pinned Production evidence tick |
| `HEATSAFE_OPERATOR_BUDGET_CAP_VND` | `3000000` | Server-side city planning budget cap |
| `HEATSAFE_OPERATOR_SPONSOR_PER_DRIVER_VND` | `8000` | Support amount per selected driver |

Secrets must be provided through Google Cloud credentials/runtime configuration; do not commit credentials or API keys.
