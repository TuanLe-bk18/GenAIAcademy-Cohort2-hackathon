from __future__ import annotations

import unittest

from heatsafe.simulation.engine import (
    advance_tick,
    initialize_state,
    load_zone_priors,
)
from heatsafe.simulation.scenario import load_scenario
from scripts.probe_phase5r_checkpoint import (
    CODEC_VERSION,
    OFFSET_CODEC_VERSION,
    decode_checkpoint,
    encode_checkpoint,
)
from scripts.probe_phase5r_timesfm import DATASET_RE, _forecast_sql


class Phase5RProbeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_scenario("hanoi_heatwave_v1")
        cls.zones = load_zone_priors()
        initial = initialize_state(seed=42, fixture=cls.fixture, zones=cls.zones)
        cls.state = advance_tick(
            initial, fixture=cls.fixture, zones=cls.zones
        ).state

    def test_offset_codec_is_byte_stable_and_preserves_next_transition(self):
        expanded, compressed = encode_checkpoint(
            self.state,
            runtime_contract_id="runtime-test",
            tick_index=0,
            codec_version=OFFSET_CODEC_VERSION,
        )
        _, restored = decode_checkpoint(
            compressed,
            runtime_contract_id="runtime-test",
            codec_version=OFFSET_CODEC_VERSION,
        )
        repeated_expanded, repeated_compressed = encode_checkpoint(
            self.state,
            runtime_contract_id="runtime-test",
            tick_index=0,
            codec_version=OFFSET_CODEC_VERSION,
        )
        self.assertEqual(self.state, restored)
        self.assertEqual(expanded, repeated_expanded)
        self.assertEqual(compressed, repeated_compressed)
        self.assertEqual(
            advance_tick(
                self.state, fixture=self.fixture, zones=self.zones
            ).checksum,
            advance_tick(
                restored, fixture=self.fixture, zones=self.zones
            ).checksum,
        )

    def test_frozen_utc_only_codec_reproduces_the_detected_divergence(self):
        _, compressed = encode_checkpoint(
            self.state,
            runtime_contract_id="runtime-test",
            tick_index=0,
            codec_version=CODEC_VERSION,
        )
        _, restored = decode_checkpoint(
            compressed,
            runtime_contract_id="runtime-test",
            codec_version=CODEC_VERSION,
        )
        self.assertEqual(self.state, restored)
        self.assertNotEqual(
            advance_tick(
                self.state, fixture=self.fixture, zones=self.zones
            ).checksum,
            advance_tick(
                restored, fixture=self.fixture, zones=self.zones
            ).checksum,
        )

    def test_timesfm_probe_is_pinned_narrow_and_disposable(self):
        sql = _forecast_sql("project.dataset.corpus", 1024, 16)
        self.assertIn("model => 'TimesFM 2.5'", sql)
        self.assertIn("context_window => 1024", sql)
        self.assertIn(
            "SELECT zone_id, interval_start, requests FROM inputs", sql
        )
        self.assertNotIn("SELECT *", sql)
        self.assertTrue(DATASET_RE.fullmatch("heatsafe_phase5r_probe_20260724165231"))
        self.assertFalse(DATASET_RE.fullmatch("heatsafe_data"))


if __name__ == "__main__":
    unittest.main()
