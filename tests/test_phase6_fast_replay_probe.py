from __future__ import annotations

import unittest

from scripts.probe_phase6_fast_replay import run_probe


class Phase6FastReplayProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_probe(2)

    def test_existing_history_reconstructs_public_zone_contract(self):
        self.assertTrue(self.result["history_reconstruction"]["passed"])
        self.assertEqual(self.result["history_reconstruction"]["failed_ticks"], [])

    def test_batch_1_4_8_transport_manifests_are_identical(self):
        self.assertTrue(self.result["transport_batch_equivalent"])
        manifests = self.result["manifests"]
        self.assertEqual(
            {manifest["batch_size"] for manifest in manifests},
            {1, 4, 8},
        )
        self.assertEqual(
            len({str(manifest["tables"]) for manifest in manifests}),
            1,
        )

    def test_probe_does_not_claim_provider_runtime(self):
        self.assertFalse(self.result["provider_runtime_proven"])


if __name__ == "__main__":
    unittest.main()
