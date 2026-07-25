from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

from heatsafe.ui.replay import replay_run_label, replay_tick_time


class ReplayUiTests(unittest.TestCase):
    def test_run_label_uses_fixture_epoch_not_legacy_ledger_or_creation_time(self):
        run = SimpleNamespace(
            scenario_version="hanoi_heatwave_v1",
            simulation_start_at=datetime(2026, 7, 24, tzinfo=UTC),
            status="RUNNING",
            simulation_run_id="454bffa67d9846d7adfa743b7f35c868",
        )

        self.assertEqual(
            replay_run_label(run),
            "26 May 00:00 ICT · running · 454bffa6",
        )
        self.assertEqual(
            replay_tick_time(run, 2).isoformat(),
            "2026-05-26T00:30:00+07:00",
        )

    def test_unknown_fixture_falls_back_to_hanoi_ledger_time(self):
        run = SimpleNamespace(
            scenario_version="future_fixture",
            simulation_start_at=datetime(2026, 8, 1, 17, tzinfo=UTC),
            status="COMPLETED",
            simulation_run_id="a" * 32,
        )

        self.assertEqual(
            replay_run_label(run),
            "02 Aug 00:00 ICT · completed · aaaaaaaa",
        )


if __name__ == "__main__":
    unittest.main()
