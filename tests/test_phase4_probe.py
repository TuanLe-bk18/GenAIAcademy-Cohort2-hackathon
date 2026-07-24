from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "probe_phase4_timesfm.py"
SCORING_SCRIPT = ROOT / "scripts" / "probe_phase4_scoring.py"


class Phase4ProbeSafetyTests(unittest.TestCase):
    def test_dry_run_records_context_and_billing_contract(self):
        result = subprocess.run(
            [
                str(ROOT / "venv" / "bin" / "python"),
                str(SCRIPT),
                "--project", "cohort2track2",
                "--dataset", "heatsafe_phase4_probe_unit",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertTrue(document["dry_run"])
        self.assertEqual(document["context_points_per_zone"], 2016)
        self.assertEqual(document["maximum_bytes_billed"], 250_000_000)

    def test_shared_dataset_is_rejected_before_cloud_access(self):
        result = subprocess.run(
            [
                str(ROOT / "venv" / "bin" / "python"),
                str(SCRIPT),
                "--project", "cohort2track2",
                "--dataset", "heatsafe_data",
                "--execute",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing shared target", result.stderr)

    def test_scoring_probe_is_dry_run_and_shared_safe_by_default(self):
        result = subprocess.run(
            [
                str(ROOT / "venv" / "bin" / "python"),
                str(SCORING_SCRIPT),
                "--project", "cohort2track2",
                "--dataset", "heatsafe_phase4_probe_scoring_unit",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertTrue(document["dry_run"])
        self.assertFalse(document["shared_mutation"])
        self.assertEqual(
            document["scoring_maximum_bytes_billed"], 300_000_000
        )


if __name__ == "__main__":
    unittest.main()
