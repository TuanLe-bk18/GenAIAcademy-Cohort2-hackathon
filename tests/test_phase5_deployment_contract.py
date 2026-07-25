from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Phase5DeploymentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (
            ROOT / "scripts" / "deploy_simulation_gcp.sh"
        ).read_text(encoding="utf-8")

    def test_image_must_be_digest_pinned(self):
        self.assertIn('if [[ "${IMAGE}" != *@sha256:* ]]', self.script)

    def test_tick_job_has_single_task_and_exact_retry_timeout_shape(self):
        self.assertIn('gcloud run jobs deploy "${TICK_JOB}"', self.script)
        self.assertIn("--tasks 1", self.script)
        self.assertIn("--parallelism 1", self.script)
        self.assertIn("--max-retries 1", self.script)
        self.assertIn("--task-timeout 300s", self.script)
        self.assertIn("--memory 1Gi", self.script)

    def test_scheduler_is_default_off_and_guarded_by_p95(self):
        self.assertIn('SCHEDULE_MODE="none"', self.script)
        self.assertIn("--enable-proof-schedule", self.script)
        self.assertIn("--enable-simulation-schedule", self.script)
        self.assertIn("HEATSAFE_TICK_P95_SECONDS", self.script)
        self.assertIn("value + 0 <= 105", self.script)
        self.assertIn("HEATSAFE_TICK_MAX_SECONDS", self.script)
        self.assertIn("value + 0 < 120", self.script)
        self.assertIn("HEATSAFE_REPLAY_ZERO_OVERLAP", self.script)
        self.assertIn("HEATSAFE_REPLAY_96_PLUS_1_VERIFIED", self.script)
        self.assertIn('scheduler_schedule="0 0 1 1 *"', self.script)

    def test_proof_schedule_breaks_no_circular_96_plus_1_gate(self):
        self.assertIn('SCHEDULE_MODE="proof"', self.script)
        self.assertIn(
            'if [[ "${SCHEDULE_MODE}" == "enabled" && '
            '"${HEATSAFE_REPLAY_96_PLUS_1_VERIFIED:-}" != "1" ]]',
            self.script,
        )
        self.assertIn(
            "Refusing recurring execution: completed 96+1 evidence is required",
            self.script,
        )

    def test_scheduler_uses_v2_post_oauth_and_no_retries(self):
        self.assertIn("https://run.googleapis.com/v2/projects/", self.script)
        self.assertIn("--http-method POST", self.script)
        self.assertIn("--oauth-service-account-email", self.script)
        self.assertIn('SCHEDULER_CRON="*/2 * * * *"', self.script)
        self.assertIn('SCHEDULER_CRON="*/15 * * * *"', self.script)
        self.assertIn('--schedule "${SCHEDULER_CRON}"', self.script)
        self.assertIn('--time-zone "Asia/Ho_Chi_Minh"', self.script)
        self.assertIn("--attempt-deadline 30s", self.script)
        self.assertIn("--max-retry-attempts 0", self.script)

    def test_scheduler_targets_are_unique_and_never_use_legacy_name(self):
        self.assertIn("--resource-tag", self.script)
        self.assertIn(
            'TICK_JOB="heatsafe-simulation-tick-${RESOURCE_TAG}"',
            self.script,
        )
        self.assertIn(
            'SCHEDULER_JOB="heatsafe-simulation-replay-2m-${RESOURCE_TAG}"',
            self.script,
        )
        self.assertIn(
            'SCHEDULER_JOB="heatsafe-simulation-real-ops-15m-${RESOURCE_TAG}"',
            self.script,
        )
        self.assertIn(
            'Scheduler creation requires a unique --resource-tag',
            self.script,
        )
        self.assertNotIn(
            'SCHEDULER_JOB="heatsafe-simulation-every-minute"',
            self.script,
        )

    def test_runtime_has_no_editor_on_authoritative_controls(self):
        edit_block = self.script.split("runtime_edit_tables=(", 1)[1].split(")", 1)[0]
        self.assertNotIn("simulation_control_events", edit_block)
        self.assertIn("simulation_control_events", self.script)
        self.assertIn("simulation_control_consumptions", edit_block)

    def test_disposable_state_dataset_can_use_shared_read_only_model(self):
        self.assertIn("HEATSAFE_SIMULATION_MODEL_DATASET", self.script)
        self.assertIn('MODEL_PROJECT="${MODEL_DATASET%%.*}"', self.script)
        self.assertIn('MODEL_DATASET_ID="${MODEL_DATASET#*.}"', self.script)
        self.assertIn(
            'datasets/${MODEL_DATASET_ID}/models/heat_risk_escalation_model',
            self.script,
        )
        self.assertIn(
            "HEATSAFE_SIMULATION_MODEL_DATASET=${MODEL_DATASET}",
            self.script,
        )
        self.assertIn("HEATSAFE_SIMULATION_COMPONENT_TELEMETRY=1", self.script)

    def test_checkpoint_bucket_is_explicit_and_runtime_cannot_delete(self):
        self.assertIn("--bootstrap-checkpoints", self.script)
        self.assertIn("roles/storage.objectCreator", self.script)
        self.assertIn("roles/storage.objectViewer", self.script)
        self.assertNotIn("roles/storage.objectAdmin", self.script)
        self.assertNotIn("roles/storage.admin", self.script)
        self.assertIn("HEATSAFE_SIMULATION_STATE_MODE", self.script)
        self.assertNotIn("gcloud run services deploy", self.script)

    def test_legacy_scheduler_is_never_addressed_by_gcloud(self):
        commands = [
            line
            for line in self.script.splitlines()
            if line.strip().startswith("gcloud ")
        ]
        self.assertTrue(
            all("heatsafe-live-ingest-15m" not in command for command in commands)
        )


if __name__ == "__main__":
    unittest.main()
