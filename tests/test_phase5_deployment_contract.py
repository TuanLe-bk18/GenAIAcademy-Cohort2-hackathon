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
        self.assertIn("--enable-simulation-schedule", self.script)
        self.assertIn("HEATSAFE_TICK_P95_SECONDS", self.script)
        self.assertIn("value + 0 <= 45", self.script)
        self.assertIn('scheduler_schedule="0 0 1 1 *"', self.script)

    def test_scheduler_uses_v2_post_oauth_and_no_retries(self):
        self.assertIn("https://run.googleapis.com/v2/projects/", self.script)
        self.assertIn("--http-method POST", self.script)
        self.assertIn("--oauth-service-account-email", self.script)
        self.assertIn('--schedule "* * * * *"', self.script)
        self.assertIn('--time-zone "Asia/Ho_Chi_Minh"', self.script)
        self.assertIn("--attempt-deadline 30s", self.script)
        self.assertIn("--max-retry-attempts 0", self.script)

    def test_runtime_has_no_editor_on_authoritative_controls(self):
        edit_block = self.script.split("runtime_edit_tables=(", 1)[1].split(")", 1)[0]
        self.assertNotIn("simulation_control_events", edit_block)
        self.assertIn("simulation_control_events", self.script)
        self.assertIn("simulation_control_consumptions", edit_block)

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
