from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .ai_decision import recommend_ai_intervention
from .config import Settings
from .models import ZoneSnapshot
from .repository import HybridRepository
from .risk import TIER_LABELS, heat_tier, operational_priority
from .telemetry import log_event


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    facts: dict
    deterministic_answer: str


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.lower())
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def rank_hotspots(zones: list[ZoneSnapshot], limit: int = 3) -> ToolResult:
    ranked = sorted(zones, key=lambda zone: (operational_priority(zone), zone.heat_index_c), reverse=True)[:limit]
    facts = {
        "hotspots": [
            {
                "zone": zone.name,
                "heat_index_c": zone.heat_index_c,
                "tier": heat_tier(zone.heat_index_c),
                "priority": operational_priority(zone),
                "exposed_2h": zone.exposed_2h,
            }
            for zone in ranked
        ]
    }
    lines = [
        f"{index}. {zone.name}: priority {operational_priority(zone)}/100, "
        f"Heat Index {zone.heat_index_c:.1f}°C, {zone.exposed_2h} drivers active ≥2 hours."
        for index, zone in enumerate(ranked, start=1)
    ]
    return ToolResult("rank_hotspots", facts, "Intervention priority:\n\n" + "\n\n".join(lines))


def get_ops_snapshot(zones: list[ZoneSnapshot]) -> ToolResult:
    active = sum(zone.active_drivers for zone in zones)
    exposed = sum(zone.exposed_2h for zone in zones)
    danger_zones = sum(heat_tier(zone.heat_index_c) in {"DANGER", "EXTREME_DANGER"} for zone in zones)
    facts = {"active_drivers": active, "exposed_2h": exposed, "danger_zones": danger_zones}
    answer = (
        f"Snapshot currently has {active:,} active drivers, {exposed:,} who have been active ≥2 hours "
        f"and {danger_zones} zones at Danger level or above."
    )
    return ToolResult("get_ops_snapshot", facts, answer)


def explain_zone(zone: ZoneSnapshot) -> ToolResult:
    tier = heat_tier(zone.heat_index_c)
    facts = {
        "zone": zone.name,
        "heat_index_c": zone.heat_index_c,
        "tier": tier,
        "priority": operational_priority(zone),
        "active_drivers": zone.active_drivers,
        "exposed_2h": zone.exposed_2h,
        "exposed_4h": zone.exposed_4h,
        "forecast_requests_30m": zone.forecast_requests_30m,
    }
    answer = (
        f"{zone.name} has Heat Index {zone.heat_index_c:.1f}°C ({TIER_LABELS[tier]}), "
        f"priority {operational_priority(zone)}/100. Out of {zone.active_drivers} active drivers, "
        f"{zone.exposed_2h} have driven ≥2 hours and {zone.exposed_4h} ≥4 hours. "
        f"Demand for the next 30 mins is simulated at {zone.forecast_requests_30m} requests."
    )
    return ToolResult("explain_zone", facts, answer)


def simulate_zone_action(
    zone: ZoneSnapshot, repository: HybridRepository | None = None
) -> ToolResult:
    repository = repository or HybridRepository("snapshot")
    repository.load()
    forecast = repository.forecast_demand(zone.zone_id, 240)
    try:
        predictions = repository.load_driver_predictions(zone.zone_id, zone.snapshot_id)
    except Exception as exc:
        return ToolResult(
            "ai_decision_unavailable",
            {"status": "MODEL_UNAVAILABLE", "reason": str(exc)},
            "AI decision unavailable; HeatSafe remains in monitoring-only mode.",
        )
    result = recommend_ai_intervention(
        zone, predictions,
        demand_by_interval=tuple(
            point.predicted_requests for point in forecast.points
        ),
        upper_demand_by_interval=tuple(point.upper_bound for point in forecast.points),
    )
    if result.recommended is None:
        return ToolResult(
            "recommend_ai_intervention",
            {
                "status": result.status,
                "message": result.message,
                "prediction_run_id": result.prediction_run_id,
            },
            result.message,
        )
    proposal = result.recommended
    facts = proposal.to_dict()
    facts["forecast_source"] = forecast.source
    answer = (
        f"BigQuery ML recommends {proposal.selected_drivers} of {proposal.eligible_drivers} "
        f"AI-eligible drivers in {proposal.waves} wave(s), with an estimated "
        f"{proposal.expected_risk_events_prevented:.2f} operational heat-risk escalations prevented. "
        f"Upper-demand fulfillment is {proposal.p90_fulfillment_rate:.1%} versus baseline "
        f"{proposal.baseline_stress_fulfillment_rate:.1%}; ETA impact is "
        f"+{proposal.p90_eta_increase_minutes:.1f} min and net platform cost is "
        f"${proposal.net_platform_cost_vnd / 25000:,.2f}. {proposal.guardrail_notes[0]}."
    )
    return ToolResult("simulate_safepause", facts, answer)


class HeatSafeCopilot:
    """Gemini orchestrates an allowlisted decision toolbox; it never generates SQL."""

    def __init__(self, zones: list[ZoneSnapshot], repository: HybridRepository | None = None):
        self.zones = zones
        self.repository = repository or HybridRepository("snapshot")
        self.settings = Settings.from_env()

    def _find_zone(self, question: str) -> ZoneSnapshot | None:
        plain_question = _plain(question)
        for zone in self.zones:
            if _plain(zone.name) in plain_question:
                return zone
        return None

    def _route(self, question: str) -> ToolResult:
        plain_question = _plain(question)
        zone = self._find_zone(question)
        if any(
            word in plain_question
            for word in (
                "chi phi",
                "cost",
                "safe pause",
                "safepause",
                "pause",
                "can thiep",
                "nghi",
            )
        ):
            target = zone or max(self.zones, key=operational_priority)
            return simulate_zone_action(target, self.repository)
        if zone:
            return explain_zone(zone)
        if any(word in plain_question for word in ("khu vuc", "hotspot", "rui ro", "uu tien", "cao nhat")):
            return rank_hotspots(self.zones)
        return get_ops_snapshot(self.zones)

    def answer(self, question: str) -> tuple[str, str]:
        plain_question = _plain(question)
        if any(
            token in plain_question
            for token in ("xoa", "delete", "drop", "truncate", "sua bang")
        ):
            return (
                "I cannot delete or modify data. Copilot only provides read-only analytical tools.",
                "safety_guard",
            )

        fallback = self._route(question)
        if not self.settings.enable_ai:
            return fallback.deterministic_answer, fallback.tool_name

        tool_calls: list[str] = []

        def resolve_zone(zone_name: str) -> ZoneSnapshot:
            plain_name = _plain(zone_name)
            for zone in self.zones:
                if plain_name in {_plain(zone.name), _plain(zone.zone_id)}:
                    return zone
            raise ValueError(f"Unknown zone: {zone_name}")

        def get_operational_snapshot() -> dict:
            """Get current fleet totals, dangerous-zone count and data provenance."""
            tool_calls.append("get_operational_snapshot")
            value = get_ops_snapshot(self.zones).facts
            value["observed_at"] = max(zone.observed_at for zone in self.zones).isoformat()
            value["source"] = sorted({zone.source for zone in self.zones})
            value["is_simulated"] = any(zone.is_simulated for zone in self.zones)
            return value

        def rank_heat_hotspots(limit: int = 3) -> dict:
            """Rank operational zones by explainable heat and exposure priority."""
            tool_calls.append("rank_heat_hotspots")
            limit = max(1, min(limit, 10))
            return rank_hotspots(self.zones, limit).facts

        def explain_zone_risk(zone_name: str) -> dict:
            """Explain environmental heat, driver exposure and operating demand for one zone."""
            tool_calls.append("explain_zone_risk")
            return explain_zone(resolve_zone(zone_name)).facts

        def forecast_zone_demand(zone_name: str, horizon_minutes: int = 60) -> dict:
            """Forecast trip requests for one zone over a 15-to-240-minute horizon."""
            tool_calls.append("forecast_zone_demand")
            zone = resolve_zone(zone_name)
            return self.repository.forecast_demand(zone.zone_id, horizon_minutes).to_dict()

        def compare_safepause_options(zone_name: str, budget_cap_vnd: int = 1_000_000) -> dict:
            """Compare SafePause durations and waves under cost, fulfillment and ETA guardrails."""
            tool_calls.append("compare_safepause_options")
            budget_cap_vnd = budget_cap_vnd if budget_cap_vnd >= 100_000 else 1_000_000
            zone = resolve_zone(zone_name)
            forecast = self.repository.forecast_demand(zone.zone_id, 240)
            predictions = self.repository.load_driver_predictions(zone.zone_id, zone.snapshot_id)
            result = recommend_ai_intervention(
                zone, predictions,
                demand_by_interval=tuple(
                    point.predicted_requests for point in forecast.points
                ),
                upper_demand_by_interval=tuple(
                    point.upper_bound for point in forecast.points
                ),
                budget_cap_vnd=max(0, budget_cap_vnd),
            )
            return {
                "forecast": forecast.to_dict(),
                "status": result.status,
                "options": [proposal.to_dict() for proposal in result.alternatives],
                "recommended_proposal_id": (
                    result.recommended.proposal_id if result.recommended else None
                ),
                "all_impacts_are_estimates": True,
            }

        def recommend_intervention(
            horizon_minutes: int = 60, budget_cap_vnd: int = 1_000_000
        ) -> dict:
            """Choose exact HeatSafe zones and feasible SafePause options for 'where should we intervene' questions."""
            tool_calls.append("recommend_intervention")
            horizon_minutes = max(15, min(240, horizon_minutes))
            budget_cap_vnd = budget_cap_vnd if budget_cap_vnd >= 100_000 else 1_000_000
            candidates: list[dict] = []
            top_zones = sorted(
                self.zones,
                key=lambda zone: (operational_priority(zone), zone.heat_index_c),
                reverse=True,
            )[:3]
            forecasts = self.repository.forecast_demand_many(
                [zone.zone_id for zone in top_zones], max(240, horizon_minutes)
            )
            for zone in top_zones:
                forecast = forecasts[zone.zone_id]
                predictions = self.repository.load_driver_predictions(
                    zone.zone_id, zone.snapshot_id
                )
                result = recommend_ai_intervention(
                    zone, predictions,
                    demand_by_interval=tuple(
                        point.predicted_requests for point in forecast.points
                    ),
                    upper_demand_by_interval=tuple(
                        point.upper_bound for point in forecast.points
                    ),
                    budget_cap_vnd=max(0, budget_cap_vnd),
                )
                candidates.append(
                    {
                        "zone": zone.name,
                        "zone_id": zone.zone_id,
                        "priority": operational_priority(zone),
                        "forecast": forecast.to_dict(),
                        "status": result.status,
                        "proposal": result.recommended.to_dict() if result.recommended else None,
                    }
                )
            feasible = [item for item in candidates if item["proposal"]]
            recommended = feasible[0] if feasible else None
            return {
                "recommended": recommended,
                "alternatives": candidates,
                "all_forecasts_and_impacts_are_estimates": True,
            }

        try:
            import json

            from google import genai
            from google.genai import types

            client = genai.Client(
                vertexai=True,
                project=self.settings.project_id,
                location=self.settings.vertex_location,
            )
            executors = {
                "get_operational_snapshot": get_operational_snapshot,
                "rank_heat_hotspots": rank_heat_hotspots,
                "explain_zone_risk": explain_zone_risk,
                "forecast_zone_demand": forecast_zone_demand,
                "compare_safepause_options": compare_safepause_options,
                "recommend_intervention": recommend_intervention,
            }
            declarations = [
                types.FunctionDeclaration(
                    name="get_operational_snapshot",
                    description="Get current fleet totals, dangerous-zone count and provenance.",
                    parameters_json_schema={"type": "object", "properties": {}},
                ),
                types.FunctionDeclaration(
                    name="rank_heat_hotspots",
                    description="Rank zones by explainable heat and exposure priority.",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}},
                    },
                ),
                types.FunctionDeclaration(
                    name="explain_zone_risk",
                    description="Explain heat, exposed drivers and demand for one exact zone.",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {"zone_name": {"type": "string", "enum": [z.name for z in self.zones]}},
                        "required": ["zone_name"],
                    },
                ),
                types.FunctionDeclaration(
                    name="forecast_zone_demand",
                    description="Forecast trip requests for one exact zone.",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {
                            "zone_name": {"type": "string", "enum": [z.name for z in self.zones]},
                            "horizon_minutes": {"type": "integer", "minimum": 15, "maximum": 240},
                        },
                        "required": ["zone_name"],
                    },
                ),
                types.FunctionDeclaration(
                    name="compare_safepause_options",
                    description="Compare SafePause options for one zone under P&L and SLA guardrails.",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {
                            "zone_name": {"type": "string", "enum": [z.name for z in self.zones]},
                            "budget_cap_vnd": {"type": "integer", "minimum": 100000},
                        },
                        "required": ["zone_name"],
                    },
                ),
                types.FunctionDeclaration(
                    name="recommend_intervention",
                    description="Choose where to intervene and return feasible SafePause alternatives.",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {
                            "horizon_minutes": {"type": "integer", "minimum": 15, "maximum": 240},
                            "budget_cap_vnd": {"type": "integer", "minimum": 100000},
                        },
                    },
                ),
            ]
            if any(token in plain_question for token in ("nen can thiep", "o dau", "khu vuc nao")):
                allowed = ["recommend_intervention"]
            elif any(
                token in plain_question
                for token in ("chi phi", "cost", "safepause", "safe pause", "pause", "phuong an nghi")
            ):
                allowed = ["compare_safepause_options"]
            elif any(token in plain_question for token in ("du bao", "forecast", "nhu cau")):
                allowed = ["forecast_zone_demand"]
            elif self._find_zone(question):
                allowed = ["explain_zone_risk"]
            elif any(token in plain_question for token in ("hotspot", "rui ro", "uu tien", "cao nhat")):
                allowed = ["rank_heat_hotspots"]
            else:
                allowed = ["get_operational_snapshot"]

            selection = client.models.generate_content(
                model=self.settings.gemini_model,
                contents=question,
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "Select the one allowed HeatSafe function and extract its arguments from the user question. "
                        f"Valid zones: {', '.join(zone.name for zone in self.zones)}. Never invent a zone."
                    ),
                    temperature=0,
                    tools=[types.Tool(function_declarations=declarations)],
                    tool_config=types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode=types.FunctionCallingConfigMode.ANY,
                            allowed_function_names=allowed,
                        )
                    ),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            function_calls = selection.function_calls or []
            if not function_calls:
                raise RuntimeError("Gemini did not return an allowed function call")
            outputs: list[dict] = []
            for call in function_calls[:3]:
                if call.name not in allowed or call.name not in executors:
                    raise RuntimeError(f"Disallowed function call: {call.name}")
                outputs.append(
                    {
                        "tool": call.name,
                        "result": executors[call.name](**dict(call.args or {})),
                    }
                )

            final_response = client.models.generate_content(
                model=self.settings.gemini_model,
                contents=(
                    f"User question: {question}\n"
                    f"Verified results from HeatSafe tools: {json.dumps(outputs, ensure_ascii=False)}"
                ),
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "You are the HeatSafe AI Ops Copilot. Use only provided BigQuery ML tool results; do not invent numbers, "
                        "zones, or causes. Reply concisely in English, cite sources if any, mark forecasts "
                        "and counterfactual impacts as estimates, never turn MODEL_UNAVAILABLE or NO_FEASIBLE into a recommendation, "
                        "refer to cost/fulfillment/ETA as guardrails, and clarify that actions are simulated."
                    ),
                    temperature=0.1,
                    max_output_tokens=550,
                ),
            )
            answer = (final_response.text or "").strip()
            trace = " → ".join(tool_calls)
            log_event("copilot_answered", tool_trace=trace, model=self.settings.gemini_model)
            return answer or fallback.deterministic_answer, trace
        except Exception as exc:
            log_event(
                "copilot_fallback",
                severity="WARNING",
                error_type=type(exc).__name__,
                fallback_tool=fallback.tool_name,
            )
            return (
                fallback.deterministic_answer
                + f"\n\n_AI tool orchestration unavailable; used deterministic report ({type(exc).__name__})._",
                fallback.tool_name,
            )
