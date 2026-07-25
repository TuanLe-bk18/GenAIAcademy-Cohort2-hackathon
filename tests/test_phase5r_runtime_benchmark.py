from __future__ import annotations

import unittest

from scripts.benchmark_phase5r_runtime import (
    CumulativeByteBudget,
    EvidenceError,
    checkpoint_timing_summary,
    correlate_attempts,
    exact_disposable_tag,
    full_tick_summary,
    live_resource_names,
    nearest_rank,
    parallel_decision,
    require_remaining_budget,
    validate_cleanup_targets,
    verify_terminal_noop,
)


def _attempt(
    tick: int,
    *,
    mode: str = "FULL",
    execution: str | None = None,
    start_minute: int | None = None,
    duration_seconds: int = 80,
    outcome: str = "SUCCEEDED",
) -> dict[str, object]:
    total_minutes = tick * 2 if start_minute is None else start_minute
    hour, minute = divmod(total_minutes, 60)
    end = total_minutes * 60 + duration_seconds
    end_hour, end_second = divmod(end, 3600)
    end_minute, end_second = divmod(end_second, 60)
    return {
        "tick_index": tick,
        "execution_mode": mode,
        "cloud_run_execution": execution or f"exec-{tick}",
        "dispatch_at": f"2026-07-25T{hour:02d}:{minute:02d}:00+00:00",
        "terminal_at": (
            f"2026-07-25T{end_hour:02d}:{end_minute:02d}:"
            f"{end_second:02d}+00:00"
        ),
        "outcome": outcome,
    }


class Phase5RRuntimeBenchmarkTests(unittest.TestCase):
    def test_full_only_filtering_requires_every_named_tick(self):
        attempts = [
            _attempt(24, duration_seconds=70),
            _attempt(25, mode="MONITOR", duration_seconds=5),
            _attempt(48, duration_seconds=90),
        ]
        summary = full_tick_summary(attempts, (24, 48))
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["max_seconds"], 90)
        with self.assertRaisesRegex(EvidenceError, "missing"):
            full_tick_summary(attempts, (24, 25, 48))

    def test_nearest_rank_p95_does_not_interpolate(self):
        self.assertEqual(nearest_rank(range(1, 21), 95), 19)
        self.assertEqual(nearest_rank((10, 20), 50), 10)

    def test_checkpoint_gate_requires_twenty_samples_and_p95_at_most_three(self):
        summary = checkpoint_timing_summary(
            [2.0] * 19 + [3.0], maximum_p95_seconds=3.0
        )
        self.assertEqual(summary["p95_seconds"], 2.0)
        with self.assertRaisesRegex(EvidenceError, "at least 20"):
            checkpoint_timing_summary(
                [2.0] * 19, maximum_p95_seconds=3.0
            )
        with self.assertRaisesRegex(EvidenceError, "exceeds"):
            checkpoint_timing_summary(
                [3.1] * 20, maximum_p95_seconds=3.0
            )

    def test_budget_stops_before_dispatch_and_settles_observed_bytes(self):
        budget = CumulativeByteBudget(100)
        budget.reserve(60)
        budget.settle(60, 35)
        with self.assertRaisesRegex(EvidenceError, "before dispatch"):
            budget.reserve(70)
        self.assertEqual(budget.observed, 35)
        self.assertEqual(budget.reserved, 0)

    def test_execution_correlation_rejects_overlap_and_duplicate_success(self):
        clean = [_attempt(0), _attempt(1)]
        self.assertTrue(correlate_attempts(clean)["zero_overlap"])
        with self.assertRaisesRegex(EvidenceError, "overlapping"):
            correlate_attempts([_attempt(0), _attempt(1, start_minute=1)])
        with self.assertRaisesRegex(EvidenceError, "duplicate successful"):
            correlate_attempts([
                _attempt(0, execution="exec-a"),
                _attempt(0, execution="exec-b", start_minute=2),
            ])

    def test_invocation_97_is_terminal_noop_and_manifest_is_unchanged(self):
        manifest = {"status": "COMPLETED", "last_completed_tick_index": 95}
        verify_terminal_noop(
            manifest,
            dict(manifest),
            {"outcome": "NO_OP_TERMINAL", "terminal_signal": True},
        )
        with self.assertRaisesRegex(EvidenceError, "mutated"):
            verify_terminal_noop(
                manifest,
                {**manifest, "last_completed_tick_index": 96},
                {"outcome": "NO_OP_TERMINAL", "terminal_signal": True},
            )

    def test_cleanup_accepts_only_exact_matching_disposable_tag(self):
        tag = exact_disposable_tag(
            dataset="heatsafe_phase5r_probe_20260725120000",
            bucket="demo-heatsafe-phase5r-20260725120000",
            job_prefix="heatsafe-phase5r-20260725120000",
        )
        validate_cleanup_targets(
            [
                "dataset:heatsafe_phase5r_probe_20260725120000",
                "job:heatsafe-phase5r-20260725120000",
            ],
            tag=tag,
        )
        with self.assertRaisesRegex(EvidenceError, "outside"):
            validate_cleanup_targets(["dataset:heatsafe_data"], tag=tag)

    def test_live_targets_are_exact_and_run_tagged(self):
        self.assertEqual(
            live_resource_names("20260725120000"),
            {
                "tick_job": "heatsafe-simulation-tick-20260725120000",
                "scheduler_job": (
                    "heatsafe-simulation-replay-2m-20260725120000"
                ),
            },
        )
        with self.assertRaisesRegex(EvidenceError, "tag"):
            live_resource_names("latest")

    def test_remaining_replay_budget_is_reserved_before_resume(self):
        reservation = require_remaining_budget(
            observed=20_000_000_000,
            last_completed_tick=39,
            per_tick_upper_bound=500_000_000,
            maximum=50_000_000_000,
        )
        self.assertEqual(reservation["remaining_ticks"], 56)
        self.assertEqual(reservation["reserved"], 28_000_000_000)
        with self.assertRaisesRegex(EvidenceError, "cannot be dispatched"):
            require_remaining_budget(
                observed=23_000_000_001,
                last_completed_tick=39,
                per_tick_upper_bound=500_000_000,
                maximum=50_000_000_000,
            )

    def test_parallel_candidate_needs_paired_samples_and_both_gains(self):
        serial = [100.0] * 10
        accepted = parallel_decision(
            serial,
            [80.0] * 10,
            trigger_seconds=90,
            minimum_improvement_pct=15,
            equivalent_results=True,
            anomaly_count=0,
        )
        self.assertEqual(accepted["selected_mode"], "parallel")
        rejected = parallel_decision(
            serial,
            [80.0] * 9,
            trigger_seconds=90,
            minimum_improvement_pct=15,
            equivalent_results=True,
            anomaly_count=0,
        )
        self.assertEqual(rejected["selected_mode"], "serial")


if __name__ == "__main__":
    unittest.main()
