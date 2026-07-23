from __future__ import annotations

import os
import subprocess
import sys
import unittest
from dataclasses import replace

from heatsafe.simulation import (
    DeterministicRandom,
    advance_tick,
    canonical_checksum,
    initialize_state,
    load_scenario,
    load_zone_priors,
)


class DeterministicRandomnessTests(unittest.TestCase):
    def test_hash_stream_is_repeatable_and_key_scoped(self):
        first = DeterministicRandom("scenario", 42, 10, "driver-1")
        second = DeterministicRandom("scenario", 42, 10, "driver-1")
        different = DeterministicRandom("scenario", 43, 10, "driver-1")
        first_values = tuple(first.uniform() for _ in range(8))
        self.assertEqual(first_values, tuple(second.uniform() for _ in range(8)))
        self.assertNotEqual(first_values, tuple(different.uniform() for _ in range(8)))

    def test_gamma_poisson_sampler_is_bounded_and_repeatable(self):
        first = DeterministicRandom("demand", 99)
        second = DeterministicRandom("demand", 99)
        values = tuple(first.negative_binomial(75, 40) for _ in range(100))
        self.assertEqual(
            values,
            tuple(second.negative_binomial(75, 40) for _ in range(100)),
        )
        self.assertTrue(all(value >= 0 for value in values))
        self.assertGreater(max(values), min(values))

    def test_canonical_checksum_normalizes_datetimes_and_floats(self):
        self.assertEqual(
            canonical_checksum({"b": 1.25, "a": (1, 2)}),
            canonical_checksum({"a": [1, 2], "b": 1.25000001}),
        )

    def test_reordered_entities_do_not_change_tick(self):
        fixture = load_scenario("hanoi_heatwave_v1")
        zones = load_zone_priors()
        original = initialize_state(seed=7, fixture=fixture, zones=zones)
        reordered = replace(
            original,
            drivers=tuple(reversed(original.drivers)),
            orders=tuple(reversed(original.orders)),
        )
        first = advance_tick(original, fixture=fixture, zones=zones)
        second = advance_tick(reordered, fixture=fixture, zones=zones)
        self.assertEqual(first.checksum, second.checksum)
        self.assertEqual(first.zones, second.zones)

    def test_cross_process_hash_seed_does_not_change_stream(self):
        code = (
            "from heatsafe.simulation import DeterministicRandom;"
            "r=DeterministicRandom('x',42,'driver');"
            "print(','.join(f'{r.uniform():.12f}' for _ in range(5)))"
        )
        outputs = []
        for hash_seed in ("1", "987654"):
            environment = {**os.environ, "PYTHONHASHSEED": hash_seed}
            outputs.append(
                subprocess.check_output(
                    [sys.executable, "-c", code],
                    cwd=os.fspath(os.path.dirname(os.path.dirname(__file__))),
                    env=environment,
                    text=True,
                ).strip()
            )
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
