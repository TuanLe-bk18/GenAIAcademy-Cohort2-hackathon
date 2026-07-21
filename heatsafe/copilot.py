from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any

from .ai_decision import recommend_ai_intervention
from .config import Settings
from .currency import USD_TO_VND, usd_to_vnd
from .models import DecisionConstraints, ZoneSnapshot
from .repository import HybridRepository
from .risk import TIER_LABELS, heat_tier, operational_priority
from .telemetry import log_event

GEMINI_REQUEST_TIMEOUT_MS = 20_000


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    facts: dict
    deterministic_answer: str


@dataclass(frozen=True)
class ToolRequest:
    """A pure routing decision. Repository work happens only when it is executed."""

    tool_name: str
    arguments: dict[str, Any]


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.lower())
    without_marks = "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    )
    return without_marks.replace("đ", "d")


def _question_horizon_minutes(question: str, default: int = 60) -> int:
    plain = _plain(question)
    minute_match = re.search(r"\b(\d{1,3})\s*(?:minutes?|mins?|phut)\b", plain)
    if minute_match:
        return max(15, min(240, int(minute_match.group(1))))
    hour_match = re.search(r"\b(\d{1,2})\s*(?:hours?|hrs?|gio)\b", plain)
    if hour_match:
        return max(15, min(240, int(hour_match.group(1)) * 60))
    return max(15, min(240, int(default)))


def _parse_number(value: str, *, decimal_context: bool = False) -> Decimal:
    """Parse common English/Vietnamese money notation without locale state."""

    value = value.strip().replace(" ", "")
    if not value:
        raise InvalidOperation
    if "," in value and "." in value:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        value = value.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif "," in value or "." in value:
        separator = "," if "," in value else "."
        groups = value.split(separator)
        is_grouped_integer = (
            not decimal_context
            and len(groups) > 1
            and all(len(group) == 3 for group in groups[1:])
        )
        value = "".join(groups) if is_grouped_integer else value.replace(separator, ".")
    return Decimal(value)


def _question_budget_vnd(question: str, default: int = 1_000_000) -> int:
    """Extract an explicit budget and normalize it to VND at the fixed product rate."""

    plain = _plain(question)
    number = r"\d+(?:[.,]\d+)*"

    usd_patterns = (
        rf"(?<!\w)\$\s*({number})",
        rf"\b({number})\s*(?:usd|us\s*dollars?)\b",
        rf"\b(?:usd|us\s*dollars?)\s*({number})\b",
    )
    for pattern in usd_patterns:
        match = re.search(pattern, plain)
        if match:
            try:
                return max(0, usd_to_vnd(_parse_number(match.group(1))))
            except (InvalidOperation, ValueError):
                continue

    million_match = re.search(
        rf"\b({number})\s*(?:million|trieu)(?:\s*(?:vnd|dong))?\b", plain
    )
    if million_match:
        try:
            amount = _parse_number(million_match.group(1), decimal_context=True)
            return max(0, int((amount * 1_000_000).to_integral_value()))
        except (InvalidOperation, ValueError):
            pass

    vnd_patterns = (
        rf"\b(?:vnd|dong)\s*({number})\b",
        rf"\b({number})\s*(?:vnd|dong)\b",
    )
    for pattern in vnd_patterns:
        match = re.search(pattern, plain)
        if match:
            try:
                return max(0, int(_parse_number(match.group(1))))
            except (InvalidOperation, ValueError):
                continue

    # Preserve support for clearly formatted/large bare VND amounts while avoiding
    # treating horizons and driver counts as budgets.
    bare_amount = re.search(r"\b(\d{1,3}(?:[.,]\d{3})+|\d{6,})\b", plain)
    if bare_amount:
        try:
            return max(0, int(_parse_number(bare_amount.group(1))))
        except (InvalidOperation, ValueError):
            pass
    return max(0, int(default))


def _monitoring_only_result(tool_name: str, exc: Exception | None = None) -> ToolResult:
    facts = {"status": "TOOL_UNAVAILABLE"}
    if exc is not None:
        facts["error_type"] = type(exc).__name__
    return ToolResult(
        tool_name,
        facts,
        "HeatSafe could not verify the requested forecast or prediction; "
        "it remains in monitoring-only mode.",
    )


def _safe_log(event: str, **fields: Any) -> None:
    try:
        log_event(event, **fields)
    except Exception:
        # Telemetry must never break a read-only operational answer.
        pass


def rank_hotspots(zones: list[ZoneSnapshot], limit: int = 3) -> ToolResult:
    ranked = sorted(
        zones,
        key=lambda zone: (operational_priority(zone), zone.heat_index_c),
        reverse=True,
    )[:limit]
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
    answer = "Intervention priority:"
    if lines:
        answer += "\n\n" + "\n\n".join(lines)
    else:
        answer = "No HeatSafe zones are available; HeatSafe remains in monitoring-only mode."
    return ToolResult("rank_hotspots", facts, answer)


def get_ops_snapshot(zones: list[ZoneSnapshot]) -> ToolResult:
    active = sum(zone.active_drivers for zone in zones)
    exposed = sum(zone.exposed_2h for zone in zones)
    danger_zones = sum(
        heat_tier(zone.heat_index_c) in {"DANGER", "EXTREME_DANGER"}
        for zone in zones
    )
    facts = {
        "active_drivers": active,
        "exposed_2h": exposed,
        "danger_zones": danger_zones,
    }
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


def _evaluate_zone_action(
    zone: ZoneSnapshot,
    repository: HybridRepository,
    constraints: DecisionConstraints,
    forecast: Any | None = None,
) -> ToolResult:
    try:
        if forecast is None:
            repository.load()
            forecast = repository.forecast_demand(
                zone.zone_id, constraints.horizon_minutes
            )
        predictions = repository.load_driver_predictions(zone.zone_id, zone.snapshot_id)
        result = recommend_ai_intervention(
            zone,
            predictions,
            demand_by_interval=tuple(
                point.predicted_requests for point in forecast.points
            ),
            upper_demand_by_interval=tuple(
                point.upper_bound for point in forecast.points
            ),
            budget_cap_vnd=constraints.budget_cap_vnd,
            sponsor_per_driver_vnd=constraints.sponsor_per_driver_vnd,
        )
    except Exception as exc:
        return ToolResult(
            "ai_decision_unavailable",
            {
                "status": "MODEL_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "horizon_minutes": constraints.horizon_minutes,
                "budget_cap_vnd": constraints.budget_cap_vnd,
                "sponsor_per_driver_vnd": constraints.sponsor_per_driver_vnd,
            },
            "AI decision unavailable; HeatSafe remains in monitoring-only mode.",
        )

    if result.recommended is None:
        return ToolResult(
            "recommend_ai_intervention",
            {
                "status": result.status,
                "message": result.message,
                "prediction_run_id": result.prediction_run_id,
                "horizon_minutes": constraints.horizon_minutes,
                "budget_cap_vnd": constraints.budget_cap_vnd,
                "sponsor_per_driver_vnd": constraints.sponsor_per_driver_vnd,
            },
            result.message,
        )
    proposal = result.recommended
    facts = proposal.to_dict()
    facts.update(
        {
            "forecast_source": forecast.source,
            "alternatives": [item.to_dict() for item in result.alternatives],
            "requested_horizon_minutes": constraints.horizon_minutes,
            "budget_cap_vnd": constraints.budget_cap_vnd,
            "sponsor_per_driver_vnd": constraints.sponsor_per_driver_vnd,
        }
    )
    answer = (
        f"Compared {len(result.alternatives)} feasible SafePause option(s). BigQuery ML recommends "
        f"{proposal.selected_drivers} of {proposal.eligible_drivers} "
        f"AI-eligible drivers in {proposal.waves} wave(s), with an estimated "
        f"{proposal.expected_risk_events_prevented:.2f} operational heat-risk escalations prevented. "
        f"Upper-demand fulfillment is {proposal.p90_fulfillment_rate:.1%} versus baseline "
        f"{proposal.baseline_stress_fulfillment_rate:.1%}; ETA impact is "
        f"+{proposal.p90_eta_increase_minutes:.1f} min and net platform cost is "
        f"${proposal.net_platform_cost_vnd / USD_TO_VND:,.2f}. {proposal.guardrail_notes[0]}."
    )
    return ToolResult("simulate_safepause", facts, answer)


def simulate_zone_action(
    zone: ZoneSnapshot,
    repository: HybridRepository | None = None,
    budget_cap_vnd: int = 1_000_000,
    sponsor_per_driver_vnd: int = 8_000,
    horizon_minutes: int = 240,
) -> ToolResult:
    constraints = DecisionConstraints(
        horizon_minutes=horizon_minutes,
        budget_cap_vnd=budget_cap_vnd,
        sponsor_per_driver_vnd=sponsor_per_driver_vnd,
    )
    try:
        active_repository = repository or HybridRepository("snapshot")
    except Exception as exc:
        return _monitoring_only_result("ai_decision_unavailable", exc)
    return _evaluate_zone_action(zone, active_repository, constraints)


def forecast_zone_demand(
    zone: ZoneSnapshot,
    repository: HybridRepository,
    horizon_minutes: int,
) -> ToolResult:
    horizon_minutes = max(15, min(240, horizon_minutes))
    try:
        repository.load()
        forecast = repository.forecast_demand(zone.zone_id, horizon_minutes)
    except Exception as exc:
        return _monitoring_only_result("forecast_zone_demand", exc)
    facts = forecast.to_dict()
    answer = (
        f"Demand forecast for {zone.name} over the next {horizon_minutes} minutes is "
        f"{forecast.predicted_requests:,} requests. Source: {forecast.source}. "
        "This forecast is an estimate."
    )
    return ToolResult("forecast_zone_demand", facts, answer)


def recommend_intervention(
    zones: list[ZoneSnapshot],
    repository: HybridRepository,
    horizon_minutes: int,
    budget_cap_vnd: int,
    sponsor_per_driver_vnd: int = 8_000,
) -> ToolResult:
    constraints = DecisionConstraints(
        horizon_minutes=horizon_minutes,
        budget_cap_vnd=budget_cap_vnd,
        sponsor_per_driver_vnd=sponsor_per_driver_vnd,
    )
    ranked = sorted(
        zones,
        key=lambda zone: (operational_priority(zone), zone.heat_index_c),
        reverse=True,
    )[:3]
    try:
        repository.load()
        forecasts = repository.forecast_demand_many(
            [zone.zone_id for zone in ranked], constraints.horizon_minutes
        )
    except Exception as exc:
        return _monitoring_only_result("recommend_intervention", exc)

    candidates: list[dict] = []
    for zone in ranked:
        forecast = forecasts.get(zone.zone_id)
        if forecast is None:
            result = _monitoring_only_result(
                "ai_decision_unavailable", KeyError(zone.zone_id)
            )
        else:
            result = _evaluate_zone_action(zone, repository, constraints, forecast)
        candidates.append(
            {
                "zone": zone.name,
                "priority": operational_priority(zone),
                "status": result.facts.get("status", result.tool_name),
                "proposal": (
                    result.facts if result.tool_name == "simulate_safepause" else None
                ),
            }
        )
    feasible = [item for item in candidates if item["proposal"]]
    if not feasible:
        evidence_unavailable = any(
            item["status"] in {"MODEL_UNAVAILABLE", "TOOL_UNAVAILABLE"}
            for item in candidates
        )
        status = "MODEL_UNAVAILABLE" if evidence_unavailable else "NO_FEASIBLE"
        message = (
            "City-wide forecast or model evidence is incomplete; HeatSafe remains "
            "in monitoring-only mode."
            if evidence_unavailable
            else "No zone has a verified feasible SafePause plan for the requested "
            "horizon and budget; HeatSafe remains in monitoring-only mode."
        )
        return ToolResult(
            "recommend_intervention",
            {
                "status": status,
                "horizon_minutes": constraints.horizon_minutes,
                "budget_cap_vnd": constraints.budget_cap_vnd,
                "sponsor_per_driver_vnd": constraints.sponsor_per_driver_vnd,
                "candidates": candidates,
            },
            message,
        )
    selected = feasible[0]
    proposal = selected["proposal"]
    answer = (
        f"Intervene first in {selected['zone']}: {proposal['selected_drivers']} drivers "
        f"across {proposal['waves']} wave(s), estimated net platform cost "
        f"${proposal['net_platform_cost_vnd'] / USD_TO_VND:,.2f}, stress-case fulfillment "
        f"{proposal['p90_fulfillment_rate']:.1%}, and ETA impact "
        f"+{proposal['p90_eta_increase_minutes']:.1f} min. This action is simulated."
    )
    return ToolResult(
        "recommend_intervention",
        {
            "recommended": selected,
            "alternatives": candidates,
            "horizon_minutes": constraints.horizon_minutes,
            "budget_cap_vnd": constraints.budget_cap_vnd,
            "sponsor_per_driver_vnd": constraints.sponsor_per_driver_vnd,
        },
        answer,
    )


class HeatSafeCopilot:
    """Gemini orchestrates an allowlisted decision toolbox; it never generates SQL."""

    def __init__(
        self,
        zones: list[ZoneSnapshot],
        repository: HybridRepository | None = None,
        default_constraints: DecisionConstraints | None = None,
    ):
        self.zones = zones
        self.repository = repository or HybridRepository("snapshot")
        self.default_constraints = (
            default_constraints or DecisionConstraints()
        ).normalized()
        self.settings = Settings.from_env()

    def _find_zone(self, question: str) -> ZoneSnapshot | None:
        plain_question = _plain(question)
        for zone in self.zones:
            if _plain(zone.name) in plain_question:
                return zone
        return None

    def _question_constraints(self, question: str) -> DecisionConstraints:
        defaults = self.default_constraints
        return replace(
            defaults,
            horizon_minutes=_question_horizon_minutes(
                question, defaults.horizon_minutes
            ),
            budget_cap_vnd=_question_budget_vnd(
                question, defaults.budget_cap_vnd
            ),
        ).normalized()

    def _route(self, question: str) -> ToolRequest:
        """Route intent and parse controls without touching the repository."""

        plain_question = _plain(question)
        zone = self._find_zone(question)
        constraints = self._question_constraints(question)
        constraint_args = {
            "horizon_minutes": constraints.horizon_minutes,
            "budget_cap_vnd": constraints.budget_cap_vnd,
            "sponsor_per_driver_vnd": constraints.sponsor_per_driver_vnd,
        }
        intervention_intent = any(
            phrase in plain_question
            for phrase in (
                "where should",
                "which area",
                "where to intervene",
                "should be intervened",
                "nen can thiep",
                "can thiep o dau",
                "khu vuc nao",
            )
        )
        if intervention_intent:
            return ToolRequest("recommend_intervention", constraint_args)
        if any(
            word in plain_question
            for word in ("forecast", "demand", "du bao", "nhu cau")
        ):
            return ToolRequest(
                "forecast_zone_demand",
                {
                    "zone_name": zone.name if zone else None,
                    "horizon_minutes": constraints.horizon_minutes,
                },
            )
        if any(
            word in plain_question
            for word in (
                "chi phi",
                "cost",
                "budget",
                "ngan sach",
                "compare",
                "option",
                "accommodation",
                "safe pause",
                "safepause",
                "pause",
                "can thiep",
                "nghi",
            )
        ):
            target = zone
            if target is None and self.zones:
                target = max(self.zones, key=operational_priority)
            return ToolRequest(
                "compare_safepause_options",
                {"zone_name": target.name if target else None, **constraint_args},
            )
        if zone:
            return ToolRequest("explain_zone_risk", {"zone_name": zone.name})
        if any(
            word in plain_question
            for word in ("khu vuc", "hotspot", "rui ro", "uu tien", "cao nhat")
        ):
            return ToolRequest("rank_heat_hotspots", {"limit": 3})
        return ToolRequest("get_operational_snapshot", {})

    def _resolve_zone(self, zone_name: str | None) -> ZoneSnapshot:
        if not zone_name:
            raise ValueError("A HeatSafe zone is required")
        plain_name = _plain(zone_name)
        for zone in self.zones:
            if plain_name in {_plain(zone.name), _plain(zone.zone_id)}:
                return zone
        raise ValueError(f"Unknown zone: {zone_name}")

    def _execute_request(self, request: ToolRequest) -> ToolResult:
        """Execute exactly one allowlisted logical tool behind one failure boundary."""

        try:
            arguments = request.arguments
            if request.tool_name == "get_operational_snapshot":
                result = get_ops_snapshot(self.zones)
                if self.zones:
                    result.facts["observed_at"] = max(
                        zone.observed_at for zone in self.zones
                    ).isoformat()
                    result.facts["source"] = sorted(
                        {zone.source for zone in self.zones}
                    )
                    result.facts["is_simulated"] = any(
                        zone.is_simulated for zone in self.zones
                    )
                return result
            if request.tool_name == "rank_heat_hotspots":
                limit = max(1, min(int(arguments.get("limit", 3)), 10))
                return rank_hotspots(self.zones, limit)
            if request.tool_name == "explain_zone_risk":
                return explain_zone(self._resolve_zone(arguments.get("zone_name")))
            if request.tool_name == "forecast_zone_demand":
                if not arguments.get("zone_name"):
                    return ToolResult(
                        "forecast_zone_demand",
                        {"status": "ZONE_REQUIRED"},
                        "Please specify one HeatSafe zone for the demand forecast.",
                    )
                return forecast_zone_demand(
                    self._resolve_zone(arguments.get("zone_name")),
                    self.repository,
                    int(arguments["horizon_minutes"]),
                )
            if request.tool_name == "compare_safepause_options":
                return simulate_zone_action(
                    self._resolve_zone(arguments.get("zone_name")),
                    self.repository,
                    budget_cap_vnd=int(arguments["budget_cap_vnd"]),
                    sponsor_per_driver_vnd=int(
                        arguments["sponsor_per_driver_vnd"]
                    ),
                    horizon_minutes=int(arguments["horizon_minutes"]),
                )
            if request.tool_name == "recommend_intervention":
                return recommend_intervention(
                    self.zones,
                    self.repository,
                    int(arguments["horizon_minutes"]),
                    int(arguments["budget_cap_vnd"]),
                    int(arguments["sponsor_per_driver_vnd"]),
                )
            raise ValueError(f"Disallowed tool: {request.tool_name}")
        except Exception as exc:
            return _monitoring_only_result(request.tool_name, exc)

    def _gemini_declarations(self, types: Any) -> list[Any]:
        return [
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
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10}
                    },
                },
            ),
            types.FunctionDeclaration(
                name="explain_zone_risk",
                description="Explain heat, exposed drivers and demand for one exact zone.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "zone_name": {
                            "type": "string",
                            "enum": [zone.name for zone in self.zones],
                        }
                    },
                    "required": ["zone_name"],
                },
            ),
            types.FunctionDeclaration(
                name="forecast_zone_demand",
                description="Forecast trip requests for one exact zone.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "zone_name": {
                            "type": "string",
                            "enum": [zone.name for zone in self.zones],
                        },
                        "horizon_minutes": {
                            "type": "integer",
                            "minimum": 15,
                            "maximum": 240,
                        },
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
                        "zone_name": {
                            "type": "string",
                            "enum": [zone.name for zone in self.zones],
                        },
                        "horizon_minutes": {
                            "type": "integer",
                            "minimum": 15,
                            "maximum": 240,
                        },
                        "budget_cap_vnd": {"type": "integer", "minimum": 0},
                        "sponsor_per_driver_vnd": {
                            "type": "integer",
                            "minimum": 0,
                        },
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
                        "horizon_minutes": {
                            "type": "integer",
                            "minimum": 15,
                            "maximum": 240,
                        },
                        "budget_cap_vnd": {"type": "integer", "minimum": 0},
                        "sponsor_per_driver_vnd": {
                            "type": "integer",
                            "minimum": 0,
                        },
                    },
                },
            ),
        ]

    def _select_with_gemini(
        self,
        client: Any,
        types: Any,
        question: str,
        deterministic_request: ToolRequest,
    ) -> ToolRequest:
        """Let Gemini confirm one allowlisted tool; deterministic parsing owns controls."""

        selection = client.models.generate_content(
            model=self.settings.gemini_model,
            contents=question,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "Select the one allowed HeatSafe function. In HeatSafe, "
                    "'accommodation options', 'rest options', and 'phuong an nghi' mean "
                    "SafePause duration-and-wave options, never lodging or hotels. "
                    f"Valid zones: {', '.join(zone.name for zone in self.zones)}. "
                    "Never invent a zone."
                ),
                temperature=0,
                tools=[
                    types.Tool(
                        function_declarations=self._gemini_declarations(types)
                    )
                ],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.ANY,
                        allowed_function_names=[deterministic_request.tool_name],
                    )
                ),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
        function_calls = selection.function_calls or []
        if len(function_calls) != 1:
            raise RuntimeError("Gemini must return exactly one allowed function call")
        call = function_calls[0]
        if call.name != deterministic_request.tool_name:
            raise RuntimeError(f"Disallowed function call: {call.name}")
        # Budget, horizon, sponsorship and zone resolution stay deterministic. This
        # prevents model-generated arguments from weakening explicit/default controls.
        return deterministic_request

    def _explain_with_gemini(
        self,
        client: Any,
        types: Any,
        question: str,
        request: ToolRequest,
        result: ToolResult,
    ) -> str:
        response = client.models.generate_content(
            model=self.settings.gemini_model,
            contents=(
                f"User question: {question}\n"
                f"Executed HeatSafe tool: {request.tool_name}\n"
                "Verified result: "
                + json.dumps(
                    {
                        "facts": result.facts,
                        "deterministic_answer": result.deterministic_answer,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            ),
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are the HeatSafe AI Ops Copilot. Use only the provided verified "
                    "tool result; do not invent numbers, zones, or causes. Interpret "
                    "accommodation/rest options as SafePause options. Mark forecasts and "
                    "counterfactual impacts as estimates, never turn MODEL_UNAVAILABLE, "
                    "TOOL_UNAVAILABLE, or NO_FEASIBLE into a recommendation, refer to "
                    "cost/fulfillment/ETA as guardrails, and clarify that actions are simulated."
                ),
                temperature=0.1,
                max_output_tokens=550,
            ),
        )
        answer = (response.text or "").strip()
        if not answer:
            raise RuntimeError("Gemini returned an empty explanation")
        return answer

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

        try:
            deterministic_request = self._route(question)
        except Exception as exc:
            result = _monitoring_only_result("intent_routing", exc)
            _safe_log(
                "copilot_fallback",
                severity="WARNING",
                error_type=type(exc).__name__,
                fallback_tool=result.tool_name,
            )
            return result.deterministic_answer, result.tool_name

        if not self.settings.enable_ai:
            result = self._execute_request(deterministic_request)
            return result.deterministic_answer, result.tool_name

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(
                vertexai=True,
                project=self.settings.project_id,
                location=self.settings.vertex_location,
                http_options=types.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT_MS),
            )
            request = self._select_with_gemini(
                client, types, question, deterministic_request
            )
        except Exception as exc:
            # Selection did not execute a repository tool. Execute the deterministic
            # request once, then stop rather than risking a second model failure.
            result = self._execute_request(deterministic_request)
            _safe_log(
                "copilot_fallback",
                severity="WARNING",
                error_type=type(exc).__name__,
                fallback_tool=result.tool_name,
                boundary="gemini_selection",
            )
            return (
                result.deterministic_answer
                + "\n\n_AI tool selection unavailable; used the deterministic report._",
                result.tool_name,
            )

        result = self._execute_request(request)
        trace = request.tool_name
        if result.facts.get("status") in {"MODEL_UNAVAILABLE", "TOOL_UNAVAILABLE"}:
            # Failed forecasts/predictions remain a deterministic, fail-closed report;
            # they are not sent to a model that could soften the monitoring-only state.
            return result.deterministic_answer, result.tool_name
        try:
            answer = self._explain_with_gemini(
                client, types, question, request, result
            )
        except Exception as exc:
            # Reuse the result already computed above. Never rerun a repository tool
            # merely because natural-language rendering failed.
            _safe_log(
                "copilot_fallback",
                severity="WARNING",
                error_type=type(exc).__name__,
                fallback_tool=result.tool_name,
                boundary="gemini_explanation",
            )
            return result.deterministic_answer, result.tool_name

        _safe_log(
            "copilot_answered",
            tool_trace=trace,
            model=self.settings.gemini_model,
        )
        return answer, trace
