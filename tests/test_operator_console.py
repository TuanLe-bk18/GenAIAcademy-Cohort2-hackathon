from __future__ import annotations

import dataclasses
import datetime
import types
import unittest
from typing import cast

from heatsafe.models import (
    DecisionConstraints,
    DriverDecision,
    ForecastEvidenceLineage,
    ForecastHorizon,
    HeatForecastEvidence,
    InterventionWindow,
    PauseWave,
    PredictiveCityPlan,
    PredictiveZonePlanRow,
    SafePauseProposal,
    ZoneSnapshot,
)
from heatsafe.simulation.models import TickResult
from heatsafe.ui.operator_console.outcomes import build_safepause_outcome_view
from heatsafe.ui.operator_console.presentation import (
    _parse_replay_state,
    load_presentation_timeline,
)
from heatsafe.ui.operator_console.view_models import (
    MAX_DRIVER_ROWS,
    MAX_HISTORY_ROWS,
    MAX_PORTFOLIO_OPTIONS,
    MAX_PRIORITY_AREAS,
    MAX_TIMING_OPTIONS,
    OperatorAreaView,
    build_operator_console_view,
)
from heatsafe.ui.operator_console.vocabulary import (
    format_hanoi_range,
    format_hanoi_time,
    format_mode_label,
    format_plan_status_label,
    operator_copy_violations,
)


BASE_TIME = datetime.datetime(2026, 7, 28, 4, 15, tzinfo=datetime.UTC)


def make_zone(index: int) -> ZoneSnapshot:
    return ZoneSnapshot(
        zone_id=f"zone-{index:02d}",
        name=f"Area {index + 1}",
        latitude=21.0 + index / 100,
        longitude=105.8 + index / 100,
        temperature_c=38.0,
        humidity_percent=60.0,
        heat_index_c=42.0 + index / 10,
        observed_at=BASE_TIME,
        scenario_id="operator-test",
        snapshot_id="internal-evidence-id",
        weather_observed_at=BASE_TIME,
        operations_observed_at=BASE_TIME,
        active_drivers=100 + index,
        fresh_drivers=70,
        exposed_2h=12,
        exposed_4h=1,
        forecast_requests_30m=90,
        avg_platform_contribution_vnd=20_000,
        avg_driver_earnings_vnd=35_000,
        coolstop_name="Cooling point",
        coolstop_latitude=21.0,
        coolstop_longitude=105.8,
        source="Synthetic test evidence",
        weather_is_simulated=True,
        operations_is_simulated=True,
    )


def make_proposal(zone: ZoneSnapshot, index: int, *, proposal_id: str | None = None) -> SafePauseProposal:
    driver_decisions = tuple(
        DriverDecision(
            driver_id_hash=f"driver-{index:02d}-{driver_index:02d}",
            exposure_minutes=260 - driver_index,
            baseline_risk=0.75,
            action_risk=0.50,
            pause_start_delay_minutes=15,
            pause_duration_minutes=30,
            priority_tier="MANDATORY_4H" if driver_index == 0 else "MODEL_ELIGIBLE",
        )
        for driver_index in range(25)
    )
    return SafePauseProposal(
        proposal_id=proposal_id or f"proposal-{index:02d}",
        zone_id=zone.zone_id,
        zone_name=zone.name,
        created_at=BASE_TIME,
        source_snapshot_at=BASE_TIME,
        eligible_drivers=25,
        high_priority_drivers=1,
        medium_priority_drivers=24,
        selected_drivers=25,
        cohort_coverage=1.0,
        pause_minutes=30,
        waves=2,
        planned_paused_driver_slots=25,
        reassigned_trips=8,
        missed_trips=1,
        earnings_guard_cost_vnd=200_000,
        partner_sponsorship_vnd=20_000,
        lost_contribution_vnd=40_000,
        net_platform_cost_vnd=220_000,
        partner_hydration_value_vnd=50_000,
        exposure_minutes_avoided=750,
        risk_weighted_minutes_avoided=500,
        simulation_horizon_minutes=120,
        projected_fulfillment_rate=0.985,
        projected_eta_increase_minutes=0.5,
        p90_fulfillment_rate=0.975,
        p90_eta_increase_minutes=1.0,
        within_guardrails=True,
        guardrail_notes=("Meets all operational limits",),
        decision_reason="Safety-first coverage within current limits.",
        wave_plan=(
            PauseWave(
                wave=1,
                start_minute=15,
                end_minute=45,
                selected_drivers=13,
                high_priority_drivers=1,
                medium_priority_drivers=12,
            ),
            PauseWave(
                wave=2,
                start_minute=30,
                end_minute=60,
                selected_drivers=12,
                high_priority_drivers=0,
                medium_priority_drivers=12,
            ),
        ),
        baseline_expected_risk_events=6.0,
        action_expected_risk_events=3.0,
        expected_risk_events_prevented=3.0,
        baseline_fulfillment_rate=0.99,
        baseline_stress_fulfillment_rate=0.99,
        driver_decisions=driver_decisions,
        mandatory_eligible_drivers=1,
        mandatory_selected_drivers=1,
        max_mandatory_delay_minutes=15,
    )


def make_plan() -> tuple[PredictiveCityPlan, tuple[ZoneSnapshot, ...]]:
    zones = tuple(make_zone(index) for index in range(10))
    rows = []
    for index, zone in enumerate(zones):
        heat = HeatForecastEvidence(
            minutes_ahead=0,
            temperature_c=zone.temperature_c,
            humidity_percent=zone.humidity_percent,
            heat_index_c=zone.heat_index_c,
            provenance="SIMULATED",
        )
        horizons = (
            ForecastHorizon(
                minutes_ahead=0,
                heat=heat,
                demand_median=80,
                demand_upper=95,
                mandatory_now=1,
                projected_mandatory=0,
                watchlist=0,
                expected_crossers=0.0,
                online_continuation_probability=1.0,
                baseline_expected_risk=4.0,
            ),
            ForecastHorizon(
                minutes_ahead=120,
                heat=dataclasses.replace(heat, minutes_ahead=120),
                demand_median=100,
                demand_upper=125,
                mandatory_now=1,
                projected_mandatory=2,
                watchlist=1,
                expected_crossers=2.3,
                online_continuation_probability=0.8,
                baseline_expected_risk=5.0,
            ),
        )
        proposal = make_proposal(zone, index)
        window = InterventionWindow(
            start_delay_minutes=15,
            end_delay_minutes=60,
            proposal=proposal,
            path_costs_vnd=(220_000,) * 64,
            expected_cost_vnd=220_000,
            p95_reserved_cost_vnd=250_000,
            projected_mandatory_after_60m=0.0,
            projected_mandatory_after_120m=0.5,
            residual_risk_60m=2.5,
            residual_risk_120m=2.0,
        )
        rows.append(
            PredictiveZonePlanRow(
                zone_id=zone.zone_id,
                zone_name=zone.name,
                horizons=horizons,
                current_raw_risk=4.0,
                expected_risk_prevented=3.0,
                best_window=window,
                preventive_pauses=25,
                severity_rank=index + 1,
                future_safety_rank=index + 1,
                opportunity_rank=index + 1,
                portfolio_status="SELECTED" if index < 5 else "DEFERRED",
                portfolio_reason=(
                    "Selected by city optimization within the P95 cost cap."
                    if index < 5
                    else "Deferred by the shared P95 cost cap."
                ),
                path_costs_vnd=(220_000,) * 64,
            )
        )
    lineage = ForecastEvidenceLineage(
        mode="CURRENT",
        scenario_id="operator-test",
        snapshot_id="internal-evidence-id",
        observed_at=BASE_TIME,
        prediction_run_ids=("internal-run",),
        model_versions=("internal-model",),
    )
    plan = PredictiveCityPlan(
        portfolio_id="authoritative-plan",
        mode="CURRENT",
        rows=tuple(rows),
        selected_zone_ids=tuple(zone.zone_id for zone in zones[:5]),
        expected_cost_vnd=800_000,
        p95_reserved_cost_vnd=1_000_000,
        budget_cap_vnd=5_000_000,
        status="READY",
        evidence_lineage=lineage,
        forecast_version="internal-forecast",
        created_at=BASE_TIME,
        expires_at=BASE_TIME + datetime.timedelta(minutes=15),
        mandatory_now_covered=10,
        mandatory_now_uncovered=0,
    )
    return plan, zones


class VocabularyAndTimeTests(unittest.TestCase):
    def test_hanoi_clock_and_cross_day_range(self):
        self.assertEqual(format_hanoi_time(BASE_TIME), "11:15")
        self.assertEqual(
            format_hanoi_time(BASE_TIME, include_date=True),
            "28 Jul · 11:15",
        )
        start = datetime.datetime(2026, 7, 28, 16, 45, tzinfo=datetime.UTC)
        end = start + datetime.timedelta(minutes=30)
        self.assertEqual(
            format_hanoi_range(start, end),
            "28 Jul · 23:45–29 Jul · 00:15",
        )

    def test_operator_vocabulary_maps_internal_states(self):
        self.assertEqual(format_mode_label("CURRENT"), "PRODUCTION")
        self.assertEqual(format_mode_label("accelerated-production"), "EVENT REPLAY")
        self.assertEqual(format_plan_status_label("SELECTED"), "Included")
        self.assertEqual(format_plan_status_label("DEFERRED"), "Watch")
        self.assertEqual(
            format_plan_status_label("UNAVAILABLE"), "Data unavailable"
        )

    def test_forbidden_term_detector_observes_word_boundaries(self):
        self.assertEqual(operator_copy_violations("A valid ticket number"), ())
        self.assertEqual(
            set(operator_copy_violations("Tick 3", "K=45", "P95 reserve")),
            {"tick", "K=", "P95"},
        )


class PresentationTimelineTests(unittest.TestCase):
    def test_replay_cursor_is_atomic_and_validated_against_the_timeline(self):
        timeline = load_presentation_timeline()
        initial = timeline["pre_decision"][0]
        initial_zone = next(
            (zone for zone in initial["zones"] if zone.get("selected")),
            initial["zones"][0],
        )

        self.assertEqual(
            _parse_replay_state(timeline, None),
            (initial["tick"], initial_zone["id"], "PRE_DECISION"),
        )
        activated = timeline["branches"]["ACTIVATE"][0]
        activated_zone = activated["zones"][2]["id"]
        self.assertEqual(
            _parse_replay_state(
                timeline,
                {
                    "tick": activated["tick"],
                    "selected_zone_id": activated_zone,
                    "branch": "ACTIVATE",
                },
            ),
            (activated["tick"], activated_zone, "ACTIVATE"),
        )
        self.assertEqual(
            _parse_replay_state(
                timeline,
                {
                    "tick": 999,
                    "selected_zone_id": "not-a-zone",
                    "branch": "CONTINUE",
                },
            ),
            (initial["tick"], initial_zone["id"], "PRE_DECISION"),
        )

    def test_display_timeline_is_bounded_and_clock_aligned(self):
        timeline = load_presentation_timeline()
        pre = timeline["pre_decision"]
        activate = timeline["branches"]["ACTIVATE"]
        continued = timeline["branches"]["CONTINUE"]

        self.assertEqual(timeline["schema_version"], "operator-presentation-v1")
        self.assertEqual(
            [frame["tick"] for frame in pre],
            list(range(timeline["start_tick"], timeline["decision_tick"] + 1)),
        )
        self.assertEqual(
            [frame["tick"] for frame in activate],
            list(range(timeline["decision_tick"] + 1, timeline["end_tick"] + 1)),
        )
        self.assertEqual(
            [frame["tick"] for frame in continued],
            [frame["tick"] for frame in activate],
        )
        self.assertEqual(timeline["range_label"], "09:15–13:15")
        self.assertEqual(timeline["decision_time_label"], "10:00")
        self.assertEqual(timeline["plan_status"], "READY")
        self.assertEqual(timeline["decision_tick"], 40)
        decision_frame = timeline["pre_decision"][-1]
        self.assertEqual(decision_frame["city"]["urgent_drivers"], 0)
        self.assertEqual(decision_frame["city"]["budget_remaining_usd"], 454)
        selected_zone_ids = {
            zone["id"] for zone in decision_frame["zones"] if zone["included"]
        }
        self.assertEqual(len(selected_zone_ids), 10)
        self.assertEqual(
            sum(
                timeline["decision_views"][zone_id]["recommendation"]["driver_count"]
                for zone_id in selected_zone_ids
            ),
            43,
        )
        first_activated = timeline["branches"]["ACTIVATE"][0]
        first_continued = timeline["branches"]["CONTINUE"][0]
        self.assertEqual(first_activated["time_label"], "10:15")
        self.assertEqual(first_activated["city"]["urgent_drivers"], 7)
        self.assertEqual(first_continued["city"]["urgent_drivers"], 43)
        self.assertEqual(
            first_activated["city"]["coverage"]["mandatory"],
            {
                "covered_drivers": 36,
                "required_drivers": 43,
                "status": "36 protected · 7 still need a break",
            },
        )
        self.assertEqual(
            first_activated["city"]["coverage"]["preventive"]["started_drivers"],
            34,
        )
        self.assertEqual(timeline["rolling_policy"]["action_horizon_minutes"], 15)
        self.assertEqual(timeline["rolling_policy"]["mandatory_budget_reserve_usd"], 100)
        events = timeline["rolling_events"]
        self.assertEqual([event["tick"] for event in events], list(range(40, 53)))
        cumulative = 0
        for event in events:
            cumulative += event["new_driver_count"]
            self.assertEqual(event["cumulative_driver_count"], cumulative)
            self.assertLessEqual(event["cumulative_p95_cost_vnd"], 12_500_000)
        self.assertEqual(events[0]["new_preventive_count"], 43)
        self.assertTrue(
            any("SAFETY_CAPACITY_BREACH" in event["outcome"] for event in events)
        )
        self.assertEqual(
            {feature["properties"]["zone_id"] for feature in timeline["district_boundaries"]["features"]},
            {zone["id"] for zone in pre[0]["zones"]},
        )
        self.assertEqual(timeline["presentation_limits"]["budget_usd"], 500)
        self.assertEqual(
            timeline["presentation_limits"]["support_per_driver_usd"],
            0.32,
        )
        self.assertTrue(
            all(
                view["recommendation"]["can_activate"]
                for view in timeline["decision_views"].values()
            )
        )
        self.assertTrue(
            all(len(frame["zones"]) == 10 for frame in (*pre, *activate, *continued))
        )

    def test_decision_payload_is_shared_instead_of_repeated_per_frame(self):
        timeline = load_presentation_timeline()
        frames = (
            *timeline["pre_decision"],
            *timeline["branches"]["ACTIVATE"],
            *timeline["branches"]["CONTINUE"],
        )

        self.assertEqual(len(timeline["decision_views"]), 10)
        self.assertTrue(all("decision_views" not in frame for frame in frames))
        self.assertTrue(
            all(
                frame["branch"] == "ACTIVATE"
                for frame in timeline["branches"]["ACTIVATE"]
            )
        )
        self.assertTrue(
            all(
                frame["branch"] == "CONTINUE"
                for frame in timeline["branches"]["CONTINUE"]
            )
        )


class OutcomeAdapterTests(unittest.TestCase):
    @staticmethod
    def result(tick_index: int, exposure_minutes: int):
        return types.SimpleNamespace(
            tick_index=tick_index,
            simulation_time=BASE_TIME + datetime.timedelta(minutes=tick_index * 15),
            state=types.SimpleNamespace(
                drivers=(
                    types.SimpleNamespace(
                        continuous_exposure_minutes=exposure_minutes
                    ),
                )
            ),
        )

    def test_outcome_requires_aligned_post_decision_histories(self):
        with_safepause = cast(
            tuple[TickResult, ...],
            (self.result(45, 300), self.result(46, 0)),
        )
        without_safepause = cast(
            tuple[TickResult, ...],
            (self.result(45, 300), self.result(46, 315)),
        )

        outcome = build_safepause_outcome_view(
            with_safepause,
            without_safepause,
            decision_tick=45,
        )

        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertTrue(outcome.available)
        self.assertEqual(outcome.points[-1].with_safepause, 0.0)
        self.assertEqual(outcome.points[-1].without_safepause, 5.25)
        self.assertIn("5.2", outcome.summary)
        self.assertIsNone(
            build_safepause_outcome_view(
                with_safepause,
                cast(
                    tuple[TickResult, ...],
                    (self.result(45, 300), self.result(47, 315)),
                ),
                decision_tick=45,
            )
        )


class OperatorBuilderContractTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.zones = make_plan()
        self.constraints = DecisionConstraints(
            horizon_minutes=120,
            budget_cap_vnd=self.plan.budget_cap_vnd,
            sponsor_per_driver_vnd=8_000,
        )

    def build_view(self, **kwargs):
        selected_window = self.plan.rows[0].best_window
        self.assertIsNotNone(selected_window)
        assert selected_window is not None
        selected_proposal = selected_window.proposal
        return build_operator_console_view(
            self.plan,
            self.zones,
            self.constraints,
            selected_zone_id=self.zones[0].zone_id,
            selected_decision=types.SimpleNamespace(proposal=selected_proposal),
            now=BASE_TIME,
            **kwargs,
        )

    def test_view_models_are_frozen(self):
        area = OperatorAreaView(
            zone_id="a",
            name="Area",
            latitude=0,
            longitude=0,
            active_drivers=1,
            heat_index_c=40,
            heat_state_label="Danger",
            drivers_needing_break_now=1,
            expected_needing_protection_by_label="1 by 13:15",
            expected_needing_protection_count=1,
            recommended_start_label="11:30",
            plan_status_label="Included",
            selected=True,
            included_in_plan=True,
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            area.name = "Changed"  # type: ignore[misc]

    def test_exactly_three_kpis_and_at_most_three_priorities(self):
        view = self.build_view()
        self.assertEqual(len(view.city_kpis.cards), 3)
        self.assertEqual(
            [card.label for card in view.city_kpis.cards],
            [
                "Mandatory breaks now",
                "Preventive risk",
                "Active drivers",
            ],
        )
        self.assertLessEqual(len(view.priority_areas), MAX_PRIORITY_AREAS)
        self.assertIsNone(view.city_kpis.at_risk_within_15m)
        self.assertEqual(view.city_kpis.cards[1].value, "Not available")
        self.assertGreater(view.city_kpis.active_drivers, 0)

    def test_builder_does_not_mutate_or_replace_authoritative_selection(self):
        before = self.plan
        view = self.build_view()
        self.assertEqual(self.plan, before)
        self.assertEqual(
            {area.zone_id for area in view.map_areas if area.included_in_plan},
            set(self.plan.selected_zone_ids),
        )
        self.assertEqual(view.recommendation.start_time_label, "11:30")

    def test_generated_default_copy_contains_no_forbidden_terms(self):
        view = self.build_view()
        self.assertEqual(operator_copy_violations(dataclasses.asdict(view)), ())

    def test_missing_optimization_evidence_is_safe_and_bounded(self):
        view = self.build_view()
        self.assertLessEqual(
            len(view.decision_insights.timing_options), MAX_TIMING_OPTIONS
        )
        self.assertEqual(len(view.decision_insights.portfolio_options), 0)
        self.assertFalse(view.decision_insights.outcome_available)
        self.assertEqual(
            view.decision_insights.evaluated_option_label,
            "Comparison details are not available yet.",
        )

    def test_optional_optimization_evidence_is_capped(self):
        evidence = types.SimpleNamespace(
            evaluated_portfolio_count=1_024,
            budget_compliant_portfolio_count=384,
            timing_options=tuple(
                types.SimpleNamespace(
                    proposal_id=f"option-{index}",
                    start_delay_minutes=index * 15,
                    pause_minutes=30,
                    drivers_protected=20,
                    projected_drivers_at_limit_120m=index,
                    feasible=True,
                    rejection_reasons=(),
                )
                for index in range(8)
            ),
            portfolio_options=tuple(
                types.SimpleNamespace(
                    selected=index == 0,
                    feasible=index < 15,
                    protected_drivers=20 + index,
                    urgent_drivers_covered=10,
                    urgent_drivers_required=10,
                    exposure_hours_avoided=5.0 + index,
                    high_demand_reserved_cost_vnd=500_000 + index,
                    worst_area_pickup_delay_minutes=1.0,
                    rejection_reasons=(),
                )
                for index in range(20)
            ),
        )
        view = self.build_view(optimization_evidence=evidence)
        self.assertEqual(
            len(view.decision_insights.timing_options), MAX_TIMING_OPTIONS
        )
        self.assertEqual(
            len(view.decision_insights.portfolio_options), MAX_PORTFOLIO_OPTIONS
        )
        self.assertIn("1,024", view.decision_insights.evaluated_option_label)
        self.assertEqual(
            operator_copy_violations(dataclasses.asdict(view.decision_insights)), ()
        )

    def test_evidence_table_shape_contracts(self):
        history = tuple(
            {
                "approved_at": BASE_TIME + datetime.timedelta(minutes=index),
                "status": "SIMULATED",
                "selected_drivers": index,
                "zone_name": f"Area {index}",
            }
            for index in range(15)
        )
        view = self.build_view(history=history)
        self.assertEqual(len(view.evidence_summary.areas.columns), 6)
        self.assertEqual(len(view.evidence_summary.areas.rows), 10)
        self.assertEqual(len(view.evidence_summary.drivers.columns), 6)
        self.assertLessEqual(
            len(view.evidence_summary.drivers.rows), MAX_DRIVER_ROWS
        )
        self.assertEqual(len(view.evidence_summary.history.columns), 5)
        self.assertLessEqual(
            len(view.evidence_summary.history.rows), MAX_HISTORY_ROWS
        )
        for table in (
            view.evidence_summary.areas,
            view.evidence_summary.drivers,
            view.evidence_summary.history,
        ):
            self.assertTrue(all(len(row) == len(table.columns) for row in table.rows))
            self.assertEqual(operator_copy_violations(table.columns, table.rows), ())

    def test_mismatched_selected_decision_cannot_override_city_plan(self):
        selected_window = self.plan.rows[0].best_window
        self.assertIsNotNone(selected_window)
        assert selected_window is not None
        other = dataclasses.replace(
            selected_window.proposal,
            proposal_id="different-proposal",
            driver_decisions=(),
        )
        view = build_operator_console_view(
            self.plan,
            self.zones,
            self.constraints,
            selected_zone_id=self.zones[0].zone_id,
            selected_decision=types.SimpleNamespace(proposal=other),
            now=BASE_TIME,
        )
        self.assertEqual(len(view.evidence_summary.drivers.rows), MAX_DRIVER_ROWS)
        self.assertEqual(view.recommendation.driver_count, 25)


if __name__ == "__main__":
    unittest.main()
