from __future__ import annotations

import contextlib
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from heatsafe.replay_copilot import ReplayCopilot, ReplayCopilotFrame
from heatsafe.ui.copilot_panel import (
    _chat_context_requires_reset,
    render_replay_copilot_panel,
)
from heatsafe.ui.operator_console.presentation import load_presentation_timeline


ENVIRONMENT = {
    "HEATSAFE_MODE": "snapshot",
    "HEATSAFE_ENABLE_AI": "0",
    "HEATSAFE_SCENARIO": "heatwave",
}


class ReplayCopilotTests(unittest.TestCase):
    def context(
        self,
        *,
        tick: int,
        branch: str,
        district: bool = True,
    ) -> ReplayCopilotFrame:
        timeline = load_presentation_timeline()
        frames = timeline["pre_decision"]
        if branch in {"ACTIVATE", "CONTINUE"}:
            frames = [*frames, *timeline["branches"][branch]]
        frame = next(item for item in frames if item["tick"] == tick)
        selected_zone_id = None
        if district:
            selected_zone = next(
                (zone for zone in frame["zones"] if zone.get("selected")),
                frame["zones"][0],
            )
            selected_zone_id = selected_zone["id"]
        return ReplayCopilotFrame.from_timeline(
            timeline,
            tick_index=tick,
            selected_zone_id=selected_zone_id,
            branch=branch,
        )

    def copilot(self, context: ReplayCopilotFrame) -> ReplayCopilot:
        with mock.patch.dict(os.environ, ENVIRONMENT, clear=False):
            return ReplayCopilot(context)

    def test_citywide_frame_has_no_selected_zone_and_uses_snapshot_tool(self):
        context = self.context(
            tick=37,
            branch="PRE_DECISION",
            district=False,
        )

        self.assertEqual(context.scope, "citywide")
        self.assertEqual(context.scope_label, "City")
        self.assertIsNone(context.selected_zone_id)
        self.assertIsNone(context.selected_zone)

        answer, tool = self.copilot(context).answer("City conditions now")

        self.assertEqual(tool, "get_replay_snapshot")
        self.assertIn("Current conditions", answer)

    def test_district_frame_infers_scope_and_uses_selected_area_tool(self):
        context = self.context(tick=37, branch="PRE_DECISION", district=True)

        self.assertEqual(context.scope, "district")
        self.assertEqual(context.scope_label, context.selected_zone_name)
        self.assertIsNotNone(context.selected_zone)

        answer, tool = self.copilot(context).answer(
            f"{context.selected_zone_name} conditions now"
        )

        self.assertEqual(tool, "explain_replay_zone")
        self.assertIn(context.selected_zone_name, answer)

    def test_replay_panel_prompts_follow_citywide_and_district_scope(self):
        citywide = self.context(
            tick=37,
            branch="PRE_DECISION",
            district=False,
        )
        district = self.context(
            tick=37,
            branch="PRE_DECISION",
            district=True,
        )

        with (
            mock.patch(
                "heatsafe.ui.copilot_panel.st.container",
                return_value=contextlib.nullcontext(),
            ),
            mock.patch("heatsafe.ui.copilot_panel._render_chat") as render_chat,
        ):
            render_replay_copilot_panel(citywide)
            citywide_call = render_chat.call_args.kwargs
            citywide_prompts = citywide_call["suggested_prompts"]
            render_replay_copilot_panel(district)
            district_call = render_chat.call_args.kwargs
            district_prompts = district_call["suggested_prompts"]

        self.assertIn("City conditions now", citywide_prompts)
        self.assertEqual(
            citywide_call["scope"],
            "operations:PRE_DECISION:citywide:citywide",
        )
        self.assertEqual(citywide_call["position"], 37)
        self.assertNotIn(
            f"{district.selected_zone_name} conditions now",
            citywide_prompts,
        )
        self.assertIn(
            f"{district.selected_zone_name} conditions now",
            district_prompts,
        )
        self.assertNotIn("City conditions now", district_prompts)
        self.assertEqual(
            district_call["scope"],
            f"operations:PRE_DECISION:district:{district.selected_zone_id}",
        )
        self.assertEqual(district_call["position"], 37)

    def test_replay_history_resets_on_scope_change_or_rewind_only(self):
        base = {
            "previous_scope": "operations:PRE_DECISION:citywide:citywide",
            "scope": "operations:PRE_DECISION:citywide:citywide",
            "previous_context": "tick-38",
            "context": "tick-39",
            "reset_on_context_change": False,
            "previous_position": 38,
            "position": 39,
        }

        self.assertFalse(_chat_context_requires_reset(**base))
        self.assertTrue(
            _chat_context_requires_reset(
                **{**base, "position": 37},
            )
        )
        self.assertTrue(
            _chat_context_requires_reset(
                **{
                    **base,
                    "scope": "operations:PRE_DECISION:district:hoan-kiem",
                },
            )
        )
        self.assertTrue(
            _chat_context_requires_reset(
                **{
                    **base,
                    "scope": "operations:ACTIVATE:citywide:citywide",
                },
            )
        )

    def test_safepause_does_not_reveal_future_decision_evidence(self):
        context = self.context(tick=37, branch="PRE_DECISION")

        answer, tool = self.copilot(context).answer(
            "Compare the SafePause options"
        )

        self.assertEqual(tool, "safepause_decision_pending")
        self.assertIn("The Safety Optimizer has evaluated conditions", answer)
        self.assertIn("No SafePause scenario is needed", answer)
        self.assertNotIn("10:00", answer)
        self.assertNotIn(
            "| Area | SafePause plan | Guardrails (cost/ETA) |", answer
        )

    def test_decision_answer_uses_only_recorded_replay_evidence(self):
        context = self.context(tick=40, branch="PRE_DECISION")

        answer, tool = self.copilot(context).answer(
            "Explain the recorded SafePause decision"
        )

        self.assertEqual(tool, "explain_city_safepause_decision")
        self.assertIn("SafePause is recommended across the city", answer)
        self.assertIn("| Area | Forecast drivers near limit |", answer)
        self.assertNotIn("monitoring-only", answer)

    def test_demand_answer_is_bound_to_selected_replay_frame(self):
        context = self.context(tick=41, branch="ACTIVATE", district=True)

        answer, tool = self.copilot(context).answer(
            f"Explain demand in {context.selected_zone_name}"
        )

        self.assertEqual(tool, "explain_replay_demand")
        self.assertIn("Expected trip requests", answer)

    def test_vietnamese_safepause_question_uses_decision_tool(self):
        context = self.context(tick=40, branch="PRE_DECISION")

        answer, tool = self.copilot(context).answer(
            "Tại sao lúc này cần SafePause?"
        )

        self.assertEqual(tool, "explain_city_safepause_decision")
        self.assertIn("SafePause is recommended across the city", answer)
        self.assertIn("Hoàn Kiếm", answer)
        self.assertIn("Đống Đa", answer)

    def test_generic_demand_question_uses_all_areas_not_the_selected_area(self):
        context = self.context(tick=40, branch="PRE_DECISION")

        answer, tool = self.copilot(context).answer("Nhu cầu chuyến đi lúc này?")

        self.assertEqual(tool, "explain_replay_demand")
        self.assertIn("across all areas", answer)
        self.assertIn("Hoàn Kiếm", answer)
        self.assertIn("Đống Đa", answer)

    def test_current_operational_priority_returns_ranked_areas_before_a_decision(self):
        context = self.context(tick=37, branch="PRE_DECISION")

        answer, tool = self.copilot(context).answer(
            "Which areas have the highest current operational priority?"
        )

        self.assertEqual(tool, "compare_replay_areas")
        self.assertIn("sorted by **current operational risk**", answer)
        self.assertIn(
            "| Rank | Area | Forecast at safety limit (+120m) |", answer
        )
        self.assertLess(answer.index("Hoàn Kiếm"), answer.index("Đống Đa"))
        self.assertLess(answer.index("Đống Đa"), answer.index("Cầu Giấy"))
        self.assertNotIn("SafePause Optimizer", answer)
        self.assertNotIn("Heat Index", answer)


    def test_optimizer_priority_uses_recorded_optimizer_outputs(self):
        context = self.context(tick=40, branch="PRE_DECISION")

        answer, tool = self.copilot(context).answer(
            "Which areas are prioritized by the SafePause Optimizer?"
        )

        self.assertEqual(tool, "rank_safepause_optimizer_areas")
        self.assertIn("| Rank | Area | In selected portfolio |", answer)
        self.assertIn("without using Heat Index", answer)

    def test_citywide_suggested_demand_prompt_includes_all_areas(self):
        context = self.context(tick=40, branch="PRE_DECISION")

        answer, tool = self.copilot(context).answer(
            "Explain trip demand across all areas at the current operational time"
        )

        self.assertEqual(tool, "explain_replay_demand")
        self.assertIn("across all areas", answer)
        self.assertIn("Hoàn Kiếm", answer)
        self.assertIn("Đống Đa", answer)

    def test_named_demand_question_filters_to_that_area(self):
        context = self.context(tick=40, branch="PRE_DECISION")

        answer, tool = self.copilot(context).answer("Nhu cầu chuyến đi ở Đống Đa?")

        self.assertEqual(tool, "explain_replay_demand")
        self.assertIn("Đống Đa", answer)
        self.assertNotIn("| Area | Expected trip requests |", answer)

    def test_vietnamese_comparison_prompt_returns_cross_area_table(self):
        context = self.context(tick=40, branch="PRE_DECISION")

        answer, tool = self.copilot(context).answer(
            "So sánh các tùy chọn SafePause giữa các khu vực"
        )

        self.assertEqual(tool, "compare_recorded_safepause_options")
        self.assertIn(
            "| Area | SafePause plan | Guardrails (cost/ETA) |", answer
        )
        self.assertIn("| Hoàng Mai | 7 drivers | \\$7 · +0.0 min |", answer)
        self.assertNotIn("drivers · 10:00", answer)
        self.assertNotIn("Ready", answer)
        self.assertNotIn(" of \\$400", answer)
        self.assertNotIn("| Pickup delay |", answer)
        self.assertIn("Hoàn Kiếm", answer)
        self.assertIn("Đống Đa", answer)

    def test_safepause_tool_contains_all_areas_and_selected_alternatives(self):
        context = self.context(tick=40, branch="PRE_DECISION", district=True)
        copilot = self.copilot(context)
        request = copilot._normalize_request(
            "compare_recorded_safepause_options",
            {
                "tick_index": 40,
                "zone_name": context.selected_zone_name,
                "scope": "all_areas",
            },
        )

        result = copilot._execute(request)

        self.assertEqual(len(result.facts["all_area_recommendations"]), 10)
        self.assertGreater(len(result.facts["city_portfolio_options"]), 1)
        self.assertIn("timing_options", result.facts["selected_area_insights"])
        self.assertIn("stress_metrics", result.facts["selected_area_insights"])
        self.assertEqual(
            set(context.knowledge_base.inventory["sources"]),
            set(load_presentation_timeline()),
        )

    def test_follow_up_reuses_previous_tool_intent(self):
        context = self.context(tick=41, branch="ACTIVATE")
        history = (
            {"role": "user", "content": "Explain demand in Hà Đông"},
            {
                "role": "assistant",
                "content": "Verified demand report",
                "tool": "explain_replay_demand",
            },
        )

        answer, tool = self.copilot(context).answer(
            "Còn Đống Đa thì sao?", history
        )

        self.assertEqual(tool, "explain_replay_demand")
        self.assertIn("Đống Đa", answer)

    def test_safepause_impact_follow_ups_keep_the_operational_intent(self):
        context = self.context(tick=40, branch="PRE_DECISION")
        copilot = self.copilot(context)
        history = (
            {"role": "user", "content": "Tại sao lúc này cần SafePause?"},
            {
                "role": "assistant",
                "content": "SafePause is recommended across the city.",
                "tool": "explain_city_safepause_decision",
            },
        )

        answer, tool = copilot.answer("Có ảnh hưởng gì không?", history)

        self.assertEqual(tool, "explain_replay_operational_impact")
        self.assertIn("Fulfillment, city-wide demand-weighted", answer)
        self.assertIn("Pickup ETA, city-wide demand-weighted", answer)
        self.assertIn("Hoàn Kiếm", answer)
        self.assertIn("Đống Đa", answer)

        answer, tool = copilot.answer(
            "Các thông số fullfillment và ETA mà?",
            (*history, {"role": "assistant", "content": answer, "tool": tool}),
        )

        self.assertEqual(tool, "explain_replay_operational_impact")
        self.assertIn("Worst-area pickup ETA", answer)
        self.assertIn("0.5 min", answer)
        self.assertNotIn("Current conditions", answer)

    def test_safepause_intent_narrows_function_selection(self):
        copilot = self.copilot(self.context(tick=40, branch="PRE_DECISION"))

        self.assertEqual(
            copilot._allowed_tools_for_question("Tại sao lúc này cần SafePause?"),
            ["explain_city_safepause_decision"],
        )
        self.assertEqual(
            copilot._allowed_tools_for_question(
                "So sánh các tùy chọn SafePause giữa các khu vực"
            ),
            ["compare_recorded_safepause_options"],
        )

    def test_gemini_cannot_move_a_current_question_to_an_older_frame(self):
        context = self.context(tick=40, branch="PRE_DECISION")
        copilot = self.copilot(context)
        copilot.settings = replace(copilot.settings, enable_ai=True)
        generate = mock.Mock(
            return_value=SimpleNamespace(
                function_calls=[
                    SimpleNamespace(
                        name="rank_safepause_optimizer_areas",
                        args={"tick_index": 37, "branch": "PRE_DECISION"},
                    )
                ]
            )
        )
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

        with mock.patch("google.genai.Client", return_value=client):
            answer, tool = copilot.answer(
                "Which areas are prioritized by the SafePause Optimizer?"
            )

        self.assertEqual(tool, "rank_safepause_optimizer_areas")
        self.assertIn("priorities at **10:00**", answer)
        self.assertNotIn("running at **09:15**", answer)

    def test_gemini_cannot_scope_a_generic_question_to_the_selected_area(self):
        context = self.context(tick=40, branch="PRE_DECISION", district=True)
        copilot = self.copilot(context)
        copilot.settings = replace(copilot.settings, enable_ai=True)
        generate = mock.Mock(
            return_value=SimpleNamespace(
                function_calls=[
                    SimpleNamespace(
                        name="explain_demand",
                        args={"zone_name": context.selected_zone_name, "tick_index": 40},
                    )
                ]
            )
        )
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

        with mock.patch("google.genai.Client", return_value=client):
            answer, tool = copilot.answer("Nhu cầu chuyến đi lúc này?")

        self.assertEqual(generate.call_count, 1)
        self.assertEqual(tool, "explain_replay_demand")
        self.assertIn("across all areas", answer)
        self.assertIn("Hoàn Kiếm", answer)
        self.assertIn("Đống Đa", answer)

    def test_gemini_selects_one_allowlisted_function_in_one_call(self):
        context = self.context(tick=41, branch="ACTIVATE")
        copilot = self.copilot(context)
        copilot.settings = replace(copilot.settings, enable_ai=True)
        generate = mock.Mock(
            return_value=SimpleNamespace(
                function_calls=[
                    SimpleNamespace(
                        name="explain_demand",
                        args={"zone_name": "Đống Đa", "tick_index": 41},
                    )
                ]
            )
        )
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        history = (
            {"role": "user", "content": "Demand in Hà Đông?"},
            {
                "role": "assistant",
                "content": "Verified demand report",
                "tool": "explain_replay_demand",
            },
        )

        with mock.patch("google.genai.Client", return_value=client):
            answer, tool = copilot.answer("Còn Đống Đa thì sao?", history)

        self.assertEqual(generate.call_count, 1)
        self.assertEqual(tool, "explain_replay_demand")
        self.assertIn("Đống Đa", answer)
        call = generate.call_args.kwargs
        self.assertIn("Demand in Hà Đông?", call["contents"])
        llm_context = (
            call["contents"] + " " + str(call["config"].system_instruction)
        ).lower()
        for forbidden in ("replay", "recorded", "simulation", "simulated", "production"):
            self.assertNotIn(forbidden, llm_context)
        allowed = (
            call["config"]
            .tool_config.function_calling_config.allowed_function_names
        )
        self.assertEqual(len(allowed), 12)
        self.assertIn("explain_demand", allowed)
        self.assertTrue(
            all(
                forbidden not in name.lower()
                for name in allowed
                for forbidden in ("replay", "recorded", "simulation", "production")
            )
        )

    def test_context_rejects_zone_from_another_frame(self):
        timeline = load_presentation_timeline()
        with self.assertRaisesRegex(ValueError, "Selected zone"):
            ReplayCopilotFrame.from_timeline(
                timeline,
                tick_index=37,
                selected_zone_id="outside-replay",
                branch="PRE_DECISION",
            )


if __name__ == "__main__":
    unittest.main()
