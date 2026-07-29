from __future__ import annotations

import unittest
from typing import Any

from heatsafe.cloud_bundle import (
    EVENT_REPLAY_END_TICK,
    EVENT_REPLAY_GENERATOR_VERSION,
    EVENT_REPLAY_START_TICK,
    ProductionBundleUnavailable,
    _verify_bundle_ledger,
)
from scripts.run_event_replay_cloud_bundle import _validate_local_source


class _QueryResult:
    def __init__(self, rows):
        self.rows = rows

    def result(self):
        return self.rows


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.query_text = ""
        self.job_config = None

    def query(self, query, job_config):
        self.query_text = query
        self.job_config = job_config
        return _QueryResult(self.rows)


class _Repository:
    dataset = "cohort2track2.heatsafe_bundle_test"

    def __init__(self, rows):
        self.client = _Client(rows)

    def _client(self):
        return self.client

    @staticmethod
    def _job_config(parameters, maximum_bytes_billed):
        return type(
            "Config",
            (),
            {
                "query_parameters": parameters,
                "maximum_bytes_billed": maximum_bytes_billed,
            },
        )()


def _ready_row(**overrides):
    row = {
        "run_status": "PAUSED",
        "pending_score_tick_id": None,
        "scored_tick_count": 5,
        "wrong_generator_count": 0,
        "selected_tick_ready": 1,
        "risk_model_version": "heat-risk-bqml-20260705T103527Z",
        "forecast_context_version": "timesfm-2.5-context-2048-v1",
    }
    row.update(overrides)
    return row


class CloudBundleContractTests(unittest.TestCase):
    def test_local_source_is_exact_v2_window(self):
        window, warm_state = _validate_local_source()
        self.assertEqual(window.generator_version, EVENT_REPLAY_GENERATOR_VERSION)
        self.assertEqual(window.start_tick, EVENT_REPLAY_START_TICK)
        self.assertEqual(warm_state.minute_index, EVENT_REPLAY_START_TICK * 15)
        self.assertEqual(EVENT_REPLAY_END_TICK, 41)

    def test_ledger_gate_requires_five_scored_ticks_and_paused_run(self):
        repository = _Repository([_ready_row()])
        _verify_bundle_ledger(
            repository,  # type: ignore[arg-type]
            run_id="a" * 32,
            selected_tick_index=41,
        )
        self.assertIn("scoring_outcome = 'SCORED'", repository.client.query_text)
        parameters = {
            parameter.name: parameter.value
            for parameter in repository.client.job_config.query_parameters
        }
        self.assertEqual(parameters["start_tick"], 37)
        self.assertEqual(parameters["end_tick"], 41)
        self.assertEqual(
            parameters["generator_version"], EVENT_REPLAY_GENERATOR_VERSION
        )

    def test_ledger_gate_fails_closed_for_incomplete_or_running_bundle(self):
        cases: tuple[dict[str, Any], ...] = (
            {"scored_tick_count": 4},
            {"run_status": "RUNNING"},
            {"wrong_generator_count": 1},
            {"forecast_context_version": None},
        )
        for override in cases:
            with self.subTest(override=override):
                repository = _Repository([_ready_row(**override)])
                with self.assertRaises(ProductionBundleUnavailable):
                    _verify_bundle_ledger(
                        repository,  # type: ignore[arg-type]
                        run_id="a" * 32,
                        selected_tick_index=41,
                    )


if __name__ == "__main__":
    unittest.main()
