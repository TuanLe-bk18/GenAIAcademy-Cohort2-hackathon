from __future__ import annotations

from dataclasses import replace
import unittest

from heatsafe.ingestion import calculate_heat_index
from heatsafe.models import DecisionConstraints
from heatsafe.production_mode import (
    ProductionSession,
    ProductionWindow,
    build_production_evidence,
    controls_from_proposals,
    state_before_tick,
)
from heatsafe.services.preventive_planning import (
    build_accelerated_forecast_input,
    build_predictive_city_plan,
    project_city_forecast,
)
from heatsafe.simulation.checkpoint import FORMAT_VERSION, encode_checkpoint
from heatsafe.simulation.engine import advance_tick, load_scenario, load_zone_priors


class ProductionModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_scenario("hanoi_heatwave_v1")
        cls.zones = load_zone_priors()
        cls.warm = state_before_tick(
            seed=42, tick_index=44, fixture=cls.fixture, zones=cls.zones
        )
        encoded = encode_checkpoint(cls.warm)
        cls.window = ProductionWindow(
            scenario_version="hanoi_heatwave_v1",
            generator_version=cls.warm.generator_version,
            seed=42,
            start_tick=44,
            decision_tick=45,
            end_tick=47,
            selected_zone_ids=("hai-ba-trung", "cau-giay", "ha-dong"),
            source_state_checksum=encoded.state_checksum,
            checkpoint_format_version=FORMAT_VERSION,
            checkpoint_payload_sha256=encoded.payload_sha256,
            checkpoint_compressed_size=len(encoded.data),
        )

    def test_session_matches_shadow_before_decision_and_resets(self):
        session = ProductionSession.create(
            window=self.window,
            warm_state=self.warm,
            fixture=self.fixture,
            zones=self.zones,
        )
        self.assertEqual(session.current_tick, 44)
        self.assertEqual(session.actual_state, session.shadow_state)
        session.start()
        session.advance()
        self.assertEqual(session.status, "AWAITING_DECISION")
        self.assertEqual(session.current_tick, 45)
        self.assertEqual(session.actual_state, session.shadow_state)
        session.reset()
        self.assertEqual(session.current_tick, 44)
        self.assertEqual(session.actual_state, session.shadow_state)

    def test_activate_uses_exact_proposal_controls_and_diverges_from_shadow(self):
        session = ProductionSession.create(
            window=self.window,
            warm_state=self.warm,
            fixture=self.fixture,
            zones=self.zones,
        )
        session.start()
        session.advance()
        session.choose("ACTIVATE")
        self.assertTrue(session.controls)
        session.advance()
        self.assertNotEqual(session.actual_state, session.shadow_state)
        self.assertTrue(session.actual_state.interventions)

    def test_shared_city_portfolio_controls_are_not_limited_to_window_top_three(self):
        session = ProductionSession.create(
            window=self.window,
            warm_state=self.warm,
            fixture=self.fixture,
            zones=self.zones,
        )
        session.start()
        session.advance()
        evidence = build_accelerated_forecast_input(
            session.actual_result,
            fixture=session.fixture,
            zones=session.zones,
        )
        plan = build_predictive_city_plan(
            project_city_forecast(evidence),
            DecisionConstraints(horizon_minutes=120),
        )
        proposals = tuple(
            row.best_window.proposal
            for row in plan.rows
            if row.zone_id in plan.selected_zone_ids
            and row.best_window is not None
        )
        self.assertTrue(proposals)
        self.assertNotEqual(
            set(plan.selected_zone_ids), set(session.window.selected_zone_ids)
        )
        session.choose("ACTIVATE", proposals=proposals)
        proposal_ids = {proposal.proposal_id for proposal in proposals}
        self.assertTrue(session.controls)
        self.assertTrue(
            {item.proposal_id for item in session.controls} <= proposal_ids
        )

    def test_opaque_predictive_tick_uses_session_tick_for_controls(self):
        session = ProductionSession.create(
            window=self.window,
            warm_state=self.warm,
            fixture=self.fixture,
            zones=self.zones,
        )
        session.start()
        session.advance()
        evidence = build_accelerated_forecast_input(
            session.actual_result,
            fixture=session.fixture,
            zones=session.zones,
        )
        plan = build_predictive_city_plan(
            project_city_forecast(evidence),
            DecisionConstraints(horizon_minutes=120),
        )
        proposal = next(
            row.best_window.proposal
            for row in plan.rows
            if row.zone_id in plan.selected_zone_ids
            and row.best_window is not None
        )

        self.assertFalse(proposal.source_tick_id.startswith("tick-"))
        controls = controls_from_proposals(
            (proposal,), source_tick_index=session.current_tick
        )
        self.assertTrue(controls)
        self.assertTrue(
            all(
                control.requested_minute
                == (session.current_tick + 1) * 15
                + control.pause_start_delay_minutes
                for control in controls
            )
        )

    def test_continue_keeps_actual_equal_to_shadow(self):
        session = ProductionSession.create(
            window=self.window,
            warm_state=self.warm,
            fixture=self.fixture,
            zones=self.zones,
        )
        session.start()
        session.advance()
        session.choose("CONTINUE")
        session.advance()
        self.assertEqual(session.actual_state, session.shadow_state)

    def test_controls_preserve_wave_delay_and_proposal_lineage(self):
        state = advance_tick(
            self.warm, fixture=self.fixture, zones=self.zones
        ).state
        result = advance_tick(state, fixture=self.fixture, zones=self.zones)
        evidence = build_production_evidence(
            result, fixture=self.fixture, zones=self.zones
        )
        proposal = evidence.city_plan.rows[0].proposal
        controls = controls_from_proposals((proposal,))
        self.assertTrue(controls)
        self.assertTrue(all(item.proposal_id == proposal.proposal_id for item in controls))
        self.assertTrue(
            all(item.pause_start_delay_minutes in {0, 15, 30, 45} for item in controls)
        )

    def test_zone_heat_index_uses_documented_microclimate_offsets(self):
        state = advance_tick(
            self.warm, fixture=self.fixture, zones=self.zones
        ).state
        result = advance_tick(state, fixture=self.fixture, zones=self.zones)
        evidence = build_production_evidence(
            result, fixture=self.fixture, zones=self.zones
        )
        offsets = self.fixture.manifest["zone_weather_offsets"]
        heat_by_zone = {zone.zone_id: zone.heat_index_c for zone in evidence.zones}

        self.assertGreater(len(set(heat_by_zone.values())), 1)
        self.assertAlmostEqual(sum(offsets.values()), 0.0)
        for zone in evidence.zones:
            expected = calculate_heat_index(
                result.weather.temperature_c + offsets[zone.zone_id],
                result.weather.humidity_percent,
            )
            with self.subTest(zone=zone.zone_id):
                self.assertEqual(zone.heat_index_c, expected)
                self.assertEqual(
                    zone.temperature_c,
                    round(
                        result.weather.temperature_c + offsets[zone.zone_id],
                        4,
                    ),
                )
                self.assertIn("synthetic zone microclimate offset", zone.source)
                self.assertIn("zone-weather", zone.generator_version)


if __name__ == "__main__":
    unittest.main()
