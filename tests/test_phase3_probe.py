from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Phase3ProbeSafetyTests(unittest.TestCase):
    def test_dry_run_never_mutates_and_names_the_disposable_target(self):
        result = subprocess.run(
            [
                sys.executable,
                "scripts/probe_phase3_bigquery.py",
                "--project", "cohort2track2",
                "--dataset", "heatsafe_phase3_probe_unit",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        document = json.loads(result.stdout)
        self.assertTrue(document["dry_run"])
        self.assertEqual(document["dataset"], "heatsafe_phase3_probe_unit")

    def test_shared_or_unprefixed_dataset_is_rejected_before_any_cloud_call(self):
        result = subprocess.run(
            [
                sys.executable,
                "scripts/probe_phase3_bigquery.py",
                "--project", "cohort2track2",
                "--dataset", "heatsafe_data",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing shared/non-probe target", result.stderr)


if __name__ == "__main__":
    unittest.main()
