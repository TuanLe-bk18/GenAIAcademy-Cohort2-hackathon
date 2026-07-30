from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .copilot import GEMINI_REQUEST_TIMEOUT_MS
from .telemetry import log_event

_ALLOWED_BRANCHES = frozenset({"PRE_DECISION", "ACTIVATE", "CONTINUE"})
_ALLOWED_TOOLS = frozenset(
    {
        "get_replay_snapshot",
        "explain_replay_zone",
        "compare_replay_areas",
        "rank_safepause_optimizer_areas",
        "explain_replay_demand",
        "explain_city_safepause_decision",
        "explain_replay_operational_impact",
        "explain_safepause_decision",
        "compare_recorded_safepause_options",
        "compare_replay_branches",
        "get_replay_events",
        "get_replay_policy",
    }
)
_GEMINI_TOOL_TO_INTERNAL = {
    "get_operational_snapshot": "get_replay_snapshot",
    "explain_area_conditions": "explain_replay_zone",
    "compare_operating_areas": "compare_replay_areas",
    "rank_safepause_optimizer_areas": "rank_safepause_optimizer_areas",
    "explain_demand": "explain_replay_demand",
    "explain_city_safepause_decision": "explain_city_safepause_decision",
    "explain_operational_impact": "explain_replay_operational_impact",
    "explain_safepause_decision": "explain_safepause_decision",
    "compare_safepause_options": "compare_recorded_safepause_options",
    "compare_operational_outcomes": "compare_replay_branches",
    "get_operational_events": "get_replay_events",
    "get_operating_policy": "get_replay_policy",
}
_INTERNAL_TO_GEMINI_TOOL = {
    internal: external for external, internal in _GEMINI_TOOL_TO_INTERNAL.items()
}


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.lower())
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ReplayKnowledgeBase:
    """The complete immutable EVENT REPLAY dataset used by allowlisted tools."""

    timeline: Mapping[str, Any]

    @property
    def inventory(self) -> Mapping[str, Any]:
        branches = _mapping(self.timeline.get("branches"))
        return {
            "schema_version": self.timeline.get("schema_version"),
            "sources": tuple(self.timeline.keys()),
            "pre_decision_frames": len(self.timeline.get("pre_decision", ())),
            "activate_frames": len(branches.get("ACTIVATE", ())),
            "continue_frames": len(branches.get("CONTINUE", ())),
            "decision_views": len(_mapping(self.timeline.get("decision_views"))),
            "rolling_events": len(self.timeline.get("rolling_events", ())),
        }

    @property
    def start_tick(self) -> int:
        return int(self.timeline.get("start_tick", 0))

    @property
    def decision_tick(self) -> int:
        return int(self.timeline.get("decision_tick", 0))

    @property
    def end_tick(self) -> int:
        return int(self.timeline.get("end_tick", 0))

    @property
    def decision_views(self) -> Mapping[str, Any]:
        return _mapping(self.timeline.get("decision_views"))

    def frames(self, branch: str) -> tuple[Mapping[str, Any], ...]:
        pre = self.timeline.get("pre_decision")
        frames = (
            [item for item in pre if isinstance(item, Mapping)]
            if isinstance(pre, list)
            else []
        )
        if branch in {"ACTIVATE", "CONTINUE"}:
            branch_frames = _mapping(self.timeline.get("branches")).get(branch)
            if isinstance(branch_frames, list):
                frames.extend(
                    item for item in branch_frames if isinstance(item, Mapping)
                )
        return tuple(frames)

    def frame(self, tick_index: int, branch: str) -> Mapping[str, Any]:
        frame = next(
            (item for item in self.frames(branch) if item.get("tick") == tick_index),
            None,
        )
        if not isinstance(frame, Mapping):
            raise ValueError("Replay tick is not present in the selected branch")
        return frame

    @staticmethod
    def zones(frame: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        zones = frame.get("zones")
        if not isinstance(zones, list):
            return ()
        return tuple(zone for zone in zones if isinstance(zone, Mapping))

    def zone(
        self, frame: Mapping[str, Any], zone_id: str
    ) -> Mapping[str, Any]:
        zone = next(
            (item for item in self.zones(frame) if item.get("id") == zone_id),
            None,
        )
        if not isinstance(zone, Mapping):
            raise ValueError("Selected zone is not present in the replay frame")
        return zone

    def resolve_zone_id(self, value: str | None, default: str) -> str:
        if not value:
            return default
        plain_value = _plain(value)
        first_frame = self.frames("PRE_DECISION")[0]
        for zone in self.zones(first_frame):
            if plain_value in {
                _plain(str(zone.get("id", ""))),
                _plain(str(zone.get("name", ""))),
            }:
                return str(zone["id"])
        raise ValueError(f"Unknown replay zone: {value!r}")

    def zone_names(self) -> tuple[str, ...]:
        first_frame = self.frames("PRE_DECISION")[0]
        return tuple(str(zone.get("name")) for zone in self.zones(first_frame))

    def events(self, start_tick: int, end_tick: int) -> tuple[Mapping[str, Any], ...]:
        events = self.timeline.get("rolling_events")
        if not isinstance(events, list):
            return ()
        return tuple(
            event
            for event in events
            if isinstance(event, Mapping)
            and start_tick <= int(event.get("tick", -1)) <= end_tick
        )


@dataclass(frozen=True)
class ReplayCopilotFrame:
    """One display-equivalent replay context bound to the full knowledge base."""

    tick_index: int
    branch: str
    selected_zone_id: str | None
    frame: Mapping[str, Any]
    selected_zone: Mapping[str, Any] | None
    decision_view: Mapping[str, Any]
    decision_views: Mapping[str, Any]
    decision_tick: int
    provenance: Mapping[str, Any]
    knowledge_base: ReplayKnowledgeBase

    @property
    def scope(self) -> str:
        return "district" if self.selected_zone_id is not None else "citywide"

    @property
    def scope_label(self) -> str:
        if self.selected_zone is None:
            return "City"
        return str(self.selected_zone.get("name") or self.selected_zone_id)

    @property
    def selected_zone_name(self) -> str:
        return self.scope_label

    @property
    def time_label(self) -> str:
        return str(self.frame.get("time_label") or "—")

    @classmethod
    def from_timeline(
        cls,
        timeline: Mapping[str, Any],
        *,
        tick_index: int,
        selected_zone_id: str | None,
        branch: str,
    ) -> ReplayCopilotFrame:
        if branch not in _ALLOWED_BRANCHES:
            raise ValueError(f"Unsupported replay branch: {branch!r}")
        knowledge_base = ReplayKnowledgeBase(timeline)
        frame = knowledge_base.frame(tick_index, branch)
        selected_zone = (
            knowledge_base.zone(frame, selected_zone_id)
            if selected_zone_id is not None
            else None
        )
        decision_views = (
            knowledge_base.decision_views
            if tick_index >= knowledge_base.decision_tick
            else {}
        )
        return cls(
            tick_index=tick_index,
            branch=branch,
            selected_zone_id=selected_zone_id,
            frame=frame,
            selected_zone=selected_zone,
            decision_view=(
                _mapping(decision_views.get(selected_zone_id))
                if selected_zone_id is not None
                else {}
            ),
            decision_views=decision_views,
            decision_tick=knowledge_base.decision_tick,
            provenance=_mapping(timeline.get("generated_from")),
            knowledge_base=knowledge_base,
        )


@dataclass(frozen=True)
class ReplayToolRequest:
    tool_name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class ReplayToolResult:
    tool_name: str
    facts: Mapping[str, Any]
    deterministic_answer: str


class ReplayCopilot:
    """One-call Gemini function router over deterministic replay tools."""

    def __init__(self, context: ReplayCopilotFrame):
        self.context = context
        self.knowledge = context.knowledge_base
        self.settings = Settings.from_env()

    def _frame_args(self, arguments: Mapping[str, Any]) -> tuple[int, str]:
        raw_tick = arguments.get("tick_index", self.context.tick_index)
        if isinstance(raw_tick, bool):
            raise ValueError("Replay tick must be an integer")
        tick = int(raw_tick)
        if not self.knowledge.start_tick <= tick <= self.context.tick_index:
            raise ValueError("Replay tool cannot read unavailable or future ticks")
        raw_branch = str(arguments.get("branch") or self.context.branch).upper()
        branch = raw_branch if raw_branch in _ALLOWED_BRANCHES else self.context.branch
        if tick <= self.knowledge.decision_tick:
            branch = "PRE_DECISION"
        elif branch == "PRE_DECISION":
            branch = (
                self.context.branch
                if self.context.branch in {"ACTIVATE", "CONTINUE"}
                else "CONTINUE"
            )
        return tick, branch

    def _zone_args(
        self, arguments: Mapping[str, Any], frame: Mapping[str, Any]
    ) -> tuple[str, Mapping[str, Any]]:
        zone_name = arguments.get("zone_name")
        if not zone_name:
            raise ValueError("A specific area must be named for an area-level request")
        zone_id = self.knowledge.resolve_zone_id(str(zone_name), "")
        return zone_id, self.knowledge.zone(frame, zone_id)

    def _mentioned_zone_name(self, question: str) -> str | None:
        plain_question = _plain(question)
        return next(
            (
                name
                for name in self.knowledge.zone_names()
                if _plain(name) in plain_question
            ),
            None,
        )

    def _normalize_request(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> ReplayToolRequest:
        if tool_name not in _ALLOWED_TOOLS:
            raise ValueError(f"Disallowed replay tool: {tool_name}")
        normalized = dict(arguments)
        tick, branch = self._frame_args(arguments)
        normalized["tick_index"] = tick
        normalized["branch"] = branch
        if tool_name in {
            "explain_replay_zone",
            "explain_replay_demand",
            "explain_safepause_decision",
            "compare_recorded_safepause_options",
            "compare_replay_branches",
        } and arguments.get("zone_name"):
            normalized["zone_name"] = self.knowledge.resolve_zone_id(
                str(arguments["zone_name"]), ""
            )
        else:
            normalized.pop("zone_name", None)
        if tool_name == "compare_replay_areas":
            metric = str(arguments.get("metric", "priority")).lower()
            normalized["metric"] = (
                metric
                if metric in {"priority", "heat", "urgent", "demand", "active"}
                else "priority"
            )
        if tool_name == "compare_recorded_safepause_options":
            scope = str(arguments.get("scope", "all_areas")).lower()
            normalized["scope"] = (
                scope if scope in {"selected_area", "all_areas"} else "all_areas"
            )
        if tool_name == "get_replay_events":
            start = int(arguments.get("start_tick", self.knowledge.decision_tick))
            end = int(arguments.get("end_tick", tick))
            normalized["start_tick"] = max(self.knowledge.start_tick, start)
            normalized["end_tick"] = min(tick, max(start, end))
        return ReplayToolRequest(tool_name, normalized)

    def _snapshot(self, request: ReplayToolRequest) -> ReplayToolResult:
        tick, branch = self._frame_args(request.arguments)
        frame = self.knowledge.frame(tick, branch)
        city = _mapping(frame.get("city"))
        facts = {
            "tick_index": tick,
            "branch": branch,
            "time": frame.get("time"),
            "status": frame.get("status"),
            "city": city,
            "zones": self.knowledge.zones(frame),
            "knowledge_inventory": self.knowledge.inventory,
            "provenance": self.context.provenance,
        }
        answer = (
            f"Current conditions at **{frame.get('time_label', '—')}**\n\n"
            f"- Status: **{frame.get('status', 'verified')}**\n"
            f"- Active drivers: **{int(city.get('active_drivers', 0)):,}**\n"
            f"- Need a break now: **{int(city.get('urgent_drivers', 0)):,}**\n"
            f"- Expected trip requests over the next 15 minutes: "
            f"**{int(city.get('requests_15m', 0)):,}**"
        )
        return ReplayToolResult(request.tool_name, facts, answer)

    def _zone(self, request: ReplayToolRequest) -> ReplayToolResult:
        tick, branch = self._frame_args(request.arguments)
        frame = self.knowledge.frame(tick, branch)
        if request.arguments.get("zone_name"):
            _, zone = self._zone_args(request.arguments, frame)
            facts = {
                "tick_index": tick,
                "branch": branch,
                "zone": dict(zone),
                "provenance": self.context.provenance,
            }
            answer = (
                f"**{zone.get('name')}** at **{frame.get('time_label', '—')}**\n\n"
                f"- Heat index: **{_number(zone.get('heat_index_c')):.1f}°C**\n"
                f"- Active drivers: **{int(_number(zone.get('active_drivers'))):,}**\n"
                f"- Need a break now: **{int(_number(zone.get('urgent_drivers'))):,}**\n"
                f"- Expected trip requests over the next 15 minutes: "
                f"**{int(_number(zone.get('requests_15m'))):,}**"
            )
            return ReplayToolResult(request.tool_name, facts, answer)

        rows = [dict(zone) for zone in self.knowledge.zones(frame)]
        table = [
            "| Area | Heat index | Active drivers | Need break | Requests/15m |",
            "|---|---:|---:|---:|---:|",
            *[
                f"| {zone.get('name')} | {_number(zone.get('heat_index_c')):.1f}°C | "
                f"{int(_number(zone.get('active_drivers'))):,} | "
                f"{int(_number(zone.get('urgent_drivers'))):,} | "
                f"{int(_number(zone.get('requests_15m'))):,} |"
                for zone in rows
            ],
        ]
        return ReplayToolResult(
            request.tool_name,
            {
                "tick_index": tick,
                "branch": branch,
                "areas": rows,
                "provenance": self.context.provenance,
            },
            f"Conditions across all areas at **{frame.get('time_label', '—')}**:\n\n"
            + "\n".join(table),
        )

    def _demand(self, request: ReplayToolRequest) -> ReplayToolResult:
        tick, branch = self._frame_args(request.arguments)
        frame = self.knowledge.frame(tick, branch)
        if request.arguments.get("zone_name"):
            _, zone = self._zone_args(request.arguments, frame)
            demand = zone.get("forecast_requests_30m", zone.get("requests_15m"))
            metric = (
                "forecast_requests_30m"
                if zone.get("forecast_requests_30m") is not None
                else "requests_15m"
            )
            facts = {
                "tick_index": tick,
                "branch": branch,
                "zone": zone.get("name"),
                "metric": metric,
                "recorded_demand": demand,
            }
            answer = (
                f"Expected trip requests for **{zone.get('name')}** over the next "
                f"15 minutes at **{frame.get('time_label', '—')}**: "
                f"**{int(_number(demand)):,}**."
            )
            return ReplayToolResult(request.tool_name, facts, answer)

        rows = [
            {
                "area": zone.get("name"),
                "recorded_demand": zone.get(
                    "forecast_requests_30m", zone.get("requests_15m")
                ),
            }
            for zone in self.knowledge.zones(frame)
        ]
        total_demand = sum(_number(row["recorded_demand"]) for row in rows)
        table = [
            "| Area | Expected trip requests |",
            "|---|---:|",
            *[
                f"| {row['area']} | {int(_number(row['recorded_demand'])):,} |"
                for row in rows
            ],
        ]
        return ReplayToolResult(
            request.tool_name,
            {
                "tick_index": tick,
                "branch": branch,
                "areas": rows,
                "total_recorded_demand": total_demand,
            },
            f"Expected trip requests across all areas at **{frame.get('time_label', '—')}**: "
            f"**{int(total_demand):,}**.\n\n" + "\n".join(table),
        )

    def _compare_areas(self, request: ReplayToolRequest) -> ReplayToolResult:
        tick, branch = self._frame_args(request.arguments)
        frame = self.knowledge.frame(tick, branch)
        metric = str(request.arguments.get("metric", "priority"))
        rows = [dict(zone) for zone in self.knowledge.zones(frame)]
        sort_key = {
            "heat": lambda row: _number(row.get("heat_index_c")),
            "urgent": lambda row: _number(row.get("urgent_drivers")),
            "demand": lambda row: _number(row.get("requests_15m")),
            "active": lambda row: _number(row.get("active_drivers")),
        }.get(metric)
        if metric == "priority":
            rows.sort(
                key=lambda row: (
                    row.get("priority_order") is None,
                    _number(row.get("priority_order"), 999.0),
                    str(row.get("name")),
                )
            )
        elif sort_key is not None:
            rows.sort(key=sort_key, reverse=True)
        metric_label = {
            "priority": "current operational risk",
            "heat": "heat index",
            "urgent": "drivers needing a break now",
            "demand": "trip demand",
            "active": "active drivers",
        }[metric]
        if metric == "priority":
            lines = [
                "| Rank | Area | Forecast at safety limit (+120m) | Expected crossers (+120m) |",
                "|---:|---|---:|---:|",
                *[
                    f"| {int(_number(row.get('priority_order')))} | {row.get('name')} | "
                    f"{int(_number(row.get('projected_mandatory_120m'))):,} | "
                    f"{_number(row.get('expected_crossers_120m')):.1f} |"
                    for row in rows
                ],
            ]
        else:
            lines = [
                "| Area | Heat index | Need break | Active | Requests/15m |",
                "|---|---:|---:|---:|---:|",
                *[
                    f"| {row.get('name')} | {_number(row.get('heat_index_c')):.1f}°C | "
                    f"{int(_number(row.get('urgent_drivers'))):,} | "
                    f"{int(_number(row.get('active_drivers'))):,} | "
                    f"{int(_number(row.get('requests_15m'))):,} |"
                    for row in rows
                ],
            ]
        return ReplayToolResult(
            request.tool_name,
            {
                "tick_index": tick,
                "branch": branch,
                "metric": metric,
                "areas": rows,
            },
            f"Area comparison at **{frame.get('time_label', '—')}**, sorted by "
            f"**{metric_label}**"
            + (
                ". The engine ranks future safety priority by forecast drivers at "
                "the safety limit, then expected threshold crossers over the next "
                "120 minutes."
                if metric == "priority"
                else "."
            )
            + "\n\n"
            + "\n".join(lines),
        )

    def _optimizer_priorities(self, request: ReplayToolRequest) -> ReplayToolResult:
        tick, branch = self._frame_args(request.arguments)
        decision_frame = self.knowledge.frame(
            self.knowledge.decision_tick, "PRE_DECISION"
        )
        decision_time = decision_frame.get("time_label", "—")
        if tick < self.knowledge.decision_tick:
            current_time = self.knowledge.frame(tick, branch).get("time_label", "—")
            return ReplayToolResult(
                "safepause_optimizer_no_action",
                {
                    "evaluated_at": current_time,
                    "status": "NO_ACTION_SELECTED",
                },
                f"The SafePause Optimizer is running at **{current_time}**, but it "
                "has not selected an actionable portfolio for this interval. There "
                "is no optimizer priority ranking to report.",
            )
        frame = self.knowledge.frame(tick, branch)
        rows: list[dict[str, Any]] = []
        for zone in self.knowledge.zones(frame):
            view = _mapping(self.knowledge.decision_views.get(str(zone.get("id"))))
            recommendation = _mapping(view.get("recommendation"))
            timing_options = _mapping(view.get("insights")).get("timing_options")
            selected_timing = next(
                (
                    item
                    for item in timing_options
                    if isinstance(item, Mapping) and item.get("selected")
                ),
                {},
            ) if isinstance(timing_options, list) else {}
            rows.append(
                {
                    "area": zone.get("name"),
                    "selected": bool(zone.get("included")),
                    "projected_drivers_at_limit": selected_timing.get(
                        "projected_drivers_at_limit"
                    ),
                    "planned_drivers": recommendation.get("driver_count"),
                }
            )
        rows.sort(
            key=lambda row: (
                not bool(row.get("selected")),
                -_number(row.get("projected_drivers_at_limit")),
                -_number(row.get("planned_drivers")),
                str(row.get("area")),
            )
        )
        table = [
            "| Rank | Area | In selected portfolio | Forecast drivers near limit | Planned SafePause |",
            "|---:|---|---|---:|---:|",
            *[
                f"| {rank} | {row['area']} | {'Yes' if row['selected'] else 'No'} | "
                f"{int(_number(row['projected_drivers_at_limit'])):,} | "
                f"{int(_number(row['planned_drivers'])):,} |"
                for rank, row in enumerate(rows, start=1)
            ],
        ]
        return ReplayToolResult(
            request.tool_name,
            {
                "decision_time": decision_time,
                "areas": rows,
                "rank_basis": (
                    "selected portfolio membership",
                    "forecast drivers near safety limit",
                    "planned SafePause drivers",
                ),
            },
            f"SafePause Optimizer priorities at **{decision_time}**. The optimizer "
            "selects a city portfolio under safety and guardrail constraints; this "
            "view ranks its verified area outputs without using Heat Index.\n\n"
            + "\n".join(table),
        )

    def _city_safepause_explanation(
        self, request: ReplayToolRequest
    ) -> ReplayToolResult:
        tick, branch = self._frame_args(request.arguments)
        if tick < self.knowledge.decision_tick:
            return self._safepause(request)
        frame = self.knowledge.frame(tick, branch)
        rows: list[dict[str, Any]] = []
        for zone in self.knowledge.zones(frame):
            view = _mapping(self.knowledge.decision_views.get(str(zone.get("id"))))
            recommendation = _mapping(view.get("recommendation"))
            insights = _mapping(view.get("insights"))
            timing_options = insights.get("timing_options")
            selected_timing = next(
                (
                    item
                    for item in timing_options
                    if isinstance(item, Mapping) and item.get("selected")
                ),
                {},
            ) if isinstance(timing_options, list) else {}
            rows.append(
                {
                    "area": zone.get("name"),
                    "planned_drivers": recommendation.get("driver_count"),
                    "projected_drivers_at_limit": selected_timing.get(
                        "projected_drivers_at_limit"
                    ),
                    "expected_demand": selected_timing.get("expected_demand"),
                    "start": recommendation.get("start_time_label"),
                }
            )
        rows.sort(
            key=lambda row: _number(row.get("projected_drivers_at_limit")),
            reverse=True,
        )
        portfolio_options = _mapping(
            next(iter(self.knowledge.decision_views.values()), {})
        )
        options = _mapping(portfolio_options.get("insights")).get(
            "portfolio_options"
        )
        selected_portfolio = next(
            (
                item
                for item in options
                if isinstance(item, Mapping) and item.get("selected")
            ),
            {},
        ) if isinstance(options, list) else {}
        projected_total = sum(
            _number(row.get("projected_drivers_at_limit")) for row in rows
        )
        planned_total = sum(_number(row.get("planned_drivers")) for row in rows)
        demand_total = sum(_number(row.get("expected_demand")) for row in rows)
        decision_time = self.knowledge.frame(
            self.knowledge.decision_tick, "PRE_DECISION"
        ).get("time_label", "—")
        table = [
            "| Area | Forecast drivers near limit | Expected demand | Planned SafePause |",
            "|---|---:|---:|---:|",
            *[
                f"| {row['area']} | {int(_number(row['projected_drivers_at_limit'])):,} | "
                f"{int(_number(row['expected_demand'])):,} | "
                f"{int(_number(row['planned_drivers'])):,} |"
                for row in rows
            ],
        ]
        facts = {
            "decision_time": decision_time,
            "all_area_decision_evidence": rows,
            "projected_drivers_at_limit_total": projected_total,
            "expected_demand_total": demand_total,
            "planned_safepause_drivers_total": planned_total,
            "selected_city_portfolio": selected_portfolio,
        }
        answer = (
            f"SafePause is recommended across the city at **{decision_time}** because "
            f"the forecast shows **{int(projected_total):,}** drivers approaching the "
            f"safety limit across all areas while expected demand is **{int(demand_total):,}**. "
            f"The plan schedules **{int(planned_total):,}** drivers in staggered SafePause waves "
            f"to protect drivers while maintaining operations.\n\n"
            + "\n".join(table)
            + (
                f"\n\n**City portfolio:** protects "
                f"**{int(_number(selected_portfolio.get('protected_drivers'))):,}** drivers, "
                f"costs **${_number(selected_portfolio.get('high_demand_cost_usd')):,.2f}**, and "
                f"adds **{_number(selected_portfolio.get('pickup_delay_minutes')):.1f} min** pickup delay."
                if selected_portfolio
                else ""
            )
        )
        return ReplayToolResult(request.tool_name, facts, answer)

    def _operational_impact(self, request: ReplayToolRequest) -> ReplayToolResult:
        tick, branch = self._frame_args(request.arguments)
        if tick < self.knowledge.decision_tick:
            return self._safepause(request)
        frame = self.knowledge.frame(tick, branch)
        def stress_metric(
            insights: Mapping[str, Any], label: str
        ) -> Mapping[str, Any]:
            metrics = insights.get("stress_metrics")
            if not isinstance(metrics, list):
                return {}
            return next(
                (
                    _mapping(metric)
                    for metric in metrics
                    if isinstance(metric, Mapping) and metric.get("label") == label
                ),
                {},
            )

        rows: list[dict[str, Any]] = []
        for zone in self.knowledge.zones(frame):
            view = _mapping(self.knowledge.decision_views.get(str(zone.get("id"))))
            insights = _mapping(view.get("insights"))
            fulfillment = stress_metric(insights, "Orders completed")
            pickup_eta = stress_metric(insights, "Expected pickup delay")
            timing_options = insights.get("timing_options")
            selected_timing = next(
                (
                    _mapping(option)
                    for option in timing_options
                    if isinstance(option, Mapping) and option.get("selected")
                ),
                {},
            ) if isinstance(timing_options, list) else {}
            rows.append(
                {
                    "area": zone.get("name"),
                    "expected_fulfillment_rate": fulfillment.get("expected_value"),
                    "high_demand_fulfillment_rate": fulfillment.get("high_demand_value"),
                    "fulfillment_limit": fulfillment.get("limit_value"),
                    "expected_pickup_eta_minutes": pickup_eta.get("expected_value"),
                    "high_demand_pickup_eta_minutes": pickup_eta.get("high_demand_value"),
                    "pickup_eta_limit_minutes": pickup_eta.get("limit_value"),
                    "expected_demand": selected_timing.get("expected_demand"),
                    "high_demand": selected_timing.get("high_demand"),
                }
            )

        def weighted_average(value_key: str, weight_key: str) -> float:
            total_weight = sum(_number(row.get(weight_key)) for row in rows)
            if total_weight <= 0:
                return 0.0
            return sum(
                _number(row.get(value_key)) * _number(row.get(weight_key))
                for row in rows
            ) / total_weight

        portfolio_view = _mapping(next(iter(self.knowledge.decision_views.values()), {}))
        options = _mapping(portfolio_view.get("insights")).get("portfolio_options")
        selected_portfolio = next(
            (
                option
                for option in options
                if isinstance(option, Mapping) and option.get("selected")
            ),
            {},
        ) if isinstance(options, list) else {}
        city_expected_fulfillment = weighted_average(
            "expected_fulfillment_rate", "expected_demand"
        )
        city_high_demand_fulfillment = weighted_average(
            "high_demand_fulfillment_rate", "high_demand"
        )
        city_expected_eta = weighted_average(
            "expected_pickup_eta_minutes", "expected_demand"
        )
        city_high_demand_eta = weighted_average(
            "high_demand_pickup_eta_minutes", "high_demand"
        )
        decision_time = self.knowledge.frame(
            self.knowledge.decision_tick, "PRE_DECISION"
        ).get("time_label", "—")
        table = [
            "| Area | Fulfillment: expected / high demand | Pickup ETA: expected / high demand |",
            "|---|---:|---:|",
            *[
                f"| {row['area']} | "
                f"{_number(row['expected_fulfillment_rate']):.1f}% / "
                f"{_number(row['high_demand_fulfillment_rate']):.1f}% | "
                f"+{_number(row['expected_pickup_eta_minutes']):.1f} / "
                f"+{_number(row['high_demand_pickup_eta_minutes']):.1f} min |"
                for row in rows
            ],
        ]
        answer = (
            f"**SafePause operational impact at {decision_time}**\n\n"
            "- **Fulfillment, city-wide demand-weighted:** "
            f"**{city_expected_fulfillment:.1f}%** expected; "
            f"**{city_high_demand_fulfillment:.1f}%** under high demand.\n"
            "- **Pickup ETA, city-wide demand-weighted:** "
            f"**+{city_expected_eta:.1f} min** expected; "
            f"**+{city_high_demand_eta:.1f} min** under high demand.\n"
            "- **Worst-area pickup ETA under the selected city plan:** "
            f"**+{_number(selected_portfolio.get('pickup_delay_minutes')):.1f} min**.\n"
            "- **High-demand cost:** "
            f"**${_number(selected_portfolio.get('high_demand_cost_usd')):,.2f}**; "
            f"**{int(_number(selected_portfolio.get('protected_drivers'))):,}** drivers protected.\n\n"
            + "\n".join(table)
        )
        return ReplayToolResult(
            request.tool_name,
            {
                "decision_time": decision_time,
                "selected_city_portfolio": selected_portfolio,
                "all_area_operational_impacts": rows,
                "city_expected_fulfillment_rate": city_expected_fulfillment,
                "city_high_demand_fulfillment_rate": city_high_demand_fulfillment,
                "city_expected_pickup_eta_minutes": city_expected_eta,
                "city_high_demand_pickup_eta_minutes": city_high_demand_eta,
            },
            answer,
        )

    def _safepause_explanation(self, request: ReplayToolRequest) -> ReplayToolResult:
        tick, branch = self._frame_args(request.arguments)
        if tick < self.knowledge.decision_tick:
            return self._safepause(request)
        if not request.arguments.get("zone_name"):
            return self._city_safepause_explanation(request)
        frame = self.knowledge.frame(tick, branch)
        _, zone = self._zone_args(request.arguments, frame)
        view = _mapping(self.knowledge.decision_views.get(str(zone.get("id"))))
        recommendation = _mapping(view.get("recommendation"))
        guardrails = recommendation.get("guardrails")
        guards = (
            [item for item in guardrails if isinstance(item, Mapping)]
            if isinstance(guardrails, list)
            else []
        )
        guard_lines = [
            f"- {item.get('label')}: **{item.get('value')}** · {item.get('status_label')}"
            for item in guards
        ]
        decision_time = self.knowledge.frame(
            self.knowledge.decision_tick, "PRE_DECISION"
        ).get("time_label", "—")
        facts = {
            "decision_time": decision_time,
            "selected_area": zone.get("name"),
            "recommendation": recommendation,
            "timing_options": _mapping(view.get("insights")).get("timing_options"),
            "guardrails": guards,
        }
        if not recommendation:
            return ReplayToolResult(
                "safepause_decision_unavailable",
                facts,
                "SafePause decision evidence is not available for the selected area.",
            )
        answer = (
            f"SafePause is recommended for **{zone.get('name')}** at **{decision_time}**.\n\n"
            f"**{recommendation.get('headline')}**\n\n"
            f"{recommendation.get('explanation')}"
            + ("\n\n**Guardrails**\n" + "\n".join(guard_lines) if guard_lines else "")
        )
        return ReplayToolResult(request.tool_name, facts, answer)

    def _safepause(self, request: ReplayToolRequest) -> ReplayToolResult:
        tick, branch = self._frame_args(request.arguments)
        if tick < self.knowledge.decision_tick:
            current_time = self.knowledge.frame(tick, branch).get("time_label", "—")
            return ReplayToolResult(
                "safepause_decision_pending",
                {
                    "evaluated_at": current_time,
                    "status": "NO_ACTION_SELECTED",
                },
                f"The Safety Optimizer has evaluated conditions at **{current_time}**. "
                "No SafePause scenario is needed at this time.",
            )
        frame = self.knowledge.frame(tick, branch)
        requested_zone_name = request.arguments.get("zone_name")
        if requested_zone_name:
            zone_id, zone = self._zone_args(request.arguments, frame)
            selected_view = _mapping(self.knowledge.decision_views.get(zone_id))
        else:
            zone_id = None
            zone = {}
            selected_view = _mapping(next(iter(self.knowledge.decision_views.values()), {}))
        selected_recommendation = _mapping(selected_view.get("recommendation"))
        selected_insights = _mapping(selected_view.get("insights"))
        portfolio_options = selected_insights.get("portfolio_options")
        options = (
            [dict(item) for item in portfolio_options if isinstance(item, Mapping)]
            if isinstance(portfolio_options, list)
            else []
        )
        area_rows: list[dict[str, Any]] = []
        zone_by_id = {
            str(item.get("id")): item for item in self.knowledge.zones(frame)
        }
        for area_id, raw_view in self.knowledge.decision_views.items():
            recommendation = _mapping(_mapping(raw_view).get("recommendation"))
            area = zone_by_id.get(str(area_id), {})
            area_rows.append(
                {
                    "zone_id": area_id,
                    "area": area.get("name", area_id),
                    "heat_index_c": area.get("heat_index_c"),
                    "included": area.get("included"),
                    "can_activate": recommendation.get("can_activate"),
                    "protected_drivers": recommendation.get("driver_count"),
                    "start": recommendation.get("start_time_label"),
                    "break_length": recommendation.get("break_length_label"),
                    "cost": recommendation.get("cost_summary"),
                    "order_impact": recommendation.get("order_impact_summary"),
                    "pickup_delay": recommendation.get("pickup_delay_summary"),
                    "headline": recommendation.get("headline"),
                    "guardrails": recommendation.get("guardrails"),
                }
            )
        area_rows.sort(
            key=lambda row: (
                bool(row.get("can_activate")),
                _number(row.get("protected_drivers")),
            ),
            reverse=True,
        )
        scope = str(request.arguments.get("scope", "all_areas"))
        displayed_area_rows = (
            [row for row in area_rows if row["zone_id"] == zone_id]
            if scope == "selected_area"
            else area_rows
        )
        area_table = [
            "| Area | SafePause plan | Guardrails (cost/ETA) |",
            "|---|---|---|",
            *[
                f"| {row['area']} | "
                f"{int(_number(row.get('protected_drivers'))):,} drivers | "
                f"{str(row.get('cost') or '—').split(' of ', 1)[0].replace('$', r'\$')} · "
                f"{row.get('pickup_delay') or '—'} |"
                for row in displayed_area_rows
            ],
        ]
        selected_options = [item for item in options if item.get("selected")]
        selected_option = selected_options[0] if selected_options else None
        option_note = (
            "\n\nThe selected **city portfolio** protects "
            f"**{int(_number(selected_option.get('protected_drivers'))):,}** drivers, "
            f"avoids **{_number(selected_option.get('exposure_hours_avoided')):.1f}** exposure-hours, "
            f"costs **${_number(selected_option.get('high_demand_cost_usd')):,.2f}**, and adds "
            f"**{_number(selected_option.get('pickup_delay_minutes')):.1f} min** pickup delay."
            if isinstance(selected_option, Mapping) and scope == "all_areas"
            else ""
        )
        facts = {
            "requested_at_tick": tick,
            "decision_evidence_tick": self.knowledge.decision_tick,
            "branch": branch,
            "selected_area": zone.get("name") if requested_zone_name else None,
            "selected_recommendation": selected_recommendation if requested_zone_name else {},
            "selected_area_insights": selected_insights if requested_zone_name else {},
            "city_portfolio_options": options,
            "all_area_recommendations": area_rows,
            "displayed_area_recommendations": displayed_area_rows,
            "scope": scope,
            "presentation_limits": self.knowledge.timeline.get("presentation_limits"),
        }
        return ReplayToolResult(
            request.tool_name,
            facts,
            f"SafePause comparison from the decision at **{self.knowledge.frame(self.knowledge.decision_tick, 'PRE_DECISION').get('time_label', '—')}**:\n\n"
            + "\n".join(area_table)
            + (
                f"\n\n**Selected area:** {selected_recommendation.get('headline')}. "
                f"{selected_recommendation.get('explanation')}"
                if requested_zone_name and selected_recommendation
                else ""
            )
            + option_note,
        )

    def _branches(self, request: ReplayToolRequest) -> ReplayToolResult:
        tick, _ = self._frame_args(request.arguments)
        if tick <= self.knowledge.decision_tick:
            return ReplayToolResult(
                "replay_branch_not_available",
                {"tick_index": tick, "status": "NOT_YET_AVAILABLE"},
                "ACTIVATE and CONTINUE outcomes are not available before the decision time.",
            )
        zone_id = request.arguments.get("zone_name")
        rows: list[dict[str, Any]] = []
        for branch in ("ACTIVATE", "CONTINUE"):
            frame = self.knowledge.frame(tick, branch)
            city = _mapping(frame.get("city"))
            zone = (
                self.knowledge.zone(frame, str(zone_id)) if zone_id else None
            )
            rows.append(
                {
                    "branch": branch,
                    "status": frame.get("status"),
                    "city": city,
                    "selected_zone": dict(zone) if zone else None,
                    "rolling_event": frame.get("rolling_event"),
                }
            )
        table = [
            "| Branch | Status | Need break | Active | Requests/15m |",
            "|---|---|---:|---:|---:|",
            *[
                f"| {row['branch']} | {row['status']} | "
                f"{int(_number(_mapping(row['city']).get('urgent_drivers'))):,} | "
                f"{int(_number(_mapping(row['city']).get('active_drivers'))):,} | "
                f"{int(_number(_mapping(row['city']).get('requests_15m'))):,} |"
                for row in rows
            ],
        ]
        return ReplayToolResult(
            request.tool_name,
            {"tick_index": tick, "zone_id": zone_id, "branches": rows},
            f"Outcome comparison at **{self.knowledge.frame(tick, 'ACTIVATE').get('time_label', '—')}**:\n\n" + "\n".join(table),
        )

    def _events(self, request: ReplayToolRequest) -> ReplayToolResult:
        start = int(request.arguments["start_tick"])
        end = int(request.arguments["end_tick"])
        events = self.knowledge.events(start, end)
        lines = [
            f"- **{event.get('time_label')}** · **{event.get('outcome')}** · "
            f"new drivers {int(_number(event.get('new_driver_count'))):,}"
            for event in events
        ]
        return ReplayToolResult(
            request.tool_name,
            {"start_tick": start, "end_tick": end, "events": events},
            "Operational events:\n\n" + ("\n".join(lines) or "No events in this range."),
        )

    def _policy(self, request: ReplayToolRequest) -> ReplayToolResult:
        policy = _mapping(self.knowledge.timeline.get("rolling_policy"))
        facts = {
            "rolling_policy": policy,
            "presentation_limits": self.knowledge.timeline.get("presentation_limits"),
            "knowledge_inventory": self.knowledge.inventory,
        }
        lines = [f"- **{key}**: {value}" for key, value in policy.items()]
        return ReplayToolResult(
            request.tool_name,
            facts,
            "Operating policy:\n\n" + "\n".join(lines),
        )

    def _execute(self, request: ReplayToolRequest) -> ReplayToolResult:
        handlers = {
            "get_replay_snapshot": self._snapshot,
            "explain_replay_zone": self._zone,
            "compare_replay_areas": self._compare_areas,
            "rank_safepause_optimizer_areas": self._optimizer_priorities,
            "explain_replay_demand": self._demand,
            "explain_city_safepause_decision": self._city_safepause_explanation,
            "explain_replay_operational_impact": self._operational_impact,
            "explain_safepause_decision": self._safepause_explanation,
            "compare_recorded_safepause_options": self._safepause,
            "compare_replay_branches": self._branches,
            "get_replay_events": self._events,
            "get_replay_policy": self._policy,
        }
        return handlers[request.tool_name](request)

    def _fallback_request(
        self,
        question: str,
        history: Sequence[Mapping[str, Any]] = (),
    ) -> ReplayToolRequest:
        """Deterministic fallback used only when Gemini function selection is unavailable."""
        plain = _plain(question)
        last_tool = next(
            (
                str(message.get("tool"))
                for message in reversed(history)
                if message.get("role") == "assistant" and message.get("tool")
            ),
            "",
        )
        mentioned_zone = self._mentioned_zone_name(question)
        base: dict[str, Any] = {
            "tick_index": self.context.tick_index,
            "branch": self.context.branch,
        }
        if mentioned_zone:
            base["zone_name"] = mentioned_zone
        if any(token in plain for token in ("policy", "rule", "quy tac", "chinh sach")):
            return self._normalize_request("get_replay_policy", base)
        if any(token in plain for token in ("event", "su kien", "rolling", "breach", "supplement")):
            return self._normalize_request("get_replay_events", base)
        if any(token in plain for token in ("activate", "continue", "branch", "outcome", "ket qua")):
            return self._normalize_request("compare_replay_branches", base)
        if any(
            token in plain
            for token in ("optimizer", "safepause priority", "priority areas")
        ):
            return self._normalize_request("rank_safepause_optimizer_areas", base)
        if any(token in plain for token in ("demand", "forecast", "nhu cau", "du bao")):
            return self._normalize_request("explain_replay_demand", base)
        if self._is_operational_impact_question(question) and (
            last_tool in {
                "explain_city_safepause_decision",
                "explain_replay_operational_impact",
                "explain_safepause_decision",
                "compare_recorded_safepause_options",
            }
            or any(token in plain for token in ("fulfillment", "fullfillment", "eta"))
        ):
            return self._normalize_request(
                "explain_replay_operational_impact", base
            )
        if any(
            token in plain
            for token in (
                "safepause",
                "safe pause",
                "phuong an",
                "tuy chon",
                "lua chon",
                "can thiep",
            )
        ):
            comparison_requested = any(
                token in plain
                for token in (
                    "compare",
                    "so sanh",
                    "tuy chon",
                    "lua chon",
                    "all",
                    "across",
                    "cac khu vuc",
                    "toan thanh pho",
                )
            )
            if comparison_requested:
                base["scope"] = (
                    "selected_area" if mentioned_zone else "all_areas"
                )
                return self._normalize_request(
                    "compare_recorded_safepause_options", base
                )
            return self._normalize_request(
                "explain_safepause_decision"
                if mentioned_zone
                else "explain_city_safepause_decision",
                base,
            )
        if any(
            token in plain
            for token in (
                "compare areas",
                "which area",
                "highest current risk",
                "current operational risk",
                "hotspot",
                "so sanh khu vuc",
                "khu vuc nao",
                "uu tien",
            )
        ):
            return self._normalize_request("compare_replay_areas", base)
        if mentioned_zone:
            follow_up_tool = {
                "explain_replay_demand": "explain_replay_demand",
                "compare_recorded_safepause_options": "compare_recorded_safepause_options",
                "explain_safepause_decision": "explain_safepause_decision",
                "explain_recorded_safepause": "explain_safepause_decision",
            }.get(last_tool, "explain_replay_zone")
            return self._normalize_request(follow_up_tool, base)
        return self._normalize_request("get_replay_snapshot", base)

    @staticmethod
    def _is_operational_impact_question(question: str) -> bool:
        plain = _plain(question)
        return any(
            token in plain
            for token in (
                "anh huong",
                "tac dong",
                "impact",
                "fulfillment",
                "fullfillment",
                "eta",
                "pickup",
                "giao hang",
                "thoi gian giao",
            )
        )

    def _gemini_declarations(self, types: Any) -> list[Any]:
        zone_schema = {
            "type": "string",
            "enum": list(self.knowledge.zone_names()),
            "description": "Exact operating area name; omit for a city-wide result.",
        }
        tick_schema = {
            "type": "integer",
            "minimum": self.knowledge.start_tick,
            "maximum": self.context.tick_index,
            "description": "Internal frame at or before the currently displayed time.",
        }
        common: dict[str, Any] = {}
        return [
            types.FunctionDeclaration(
                name="get_operational_snapshot",
                description="Get current city totals and all area observations.",
                parameters_json_schema={"type": "object", "properties": common},
            ),
            types.FunctionDeclaration(
                name="explain_area_conditions",
                description="Explain heat, drivers, urgency and requests city-wide, or for one explicitly named operating area.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {**common, "zone_name": zone_schema},
                },
            ),
            types.FunctionDeclaration(
                name="compare_operating_areas",
                description="Compare and rank every operating area using one verified metric.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        **common,
                        "metric": {
                            "type": "string",
                            "enum": ["priority", "heat", "urgent", "demand", "active"],
                        },
                    },
                },
            ),
            types.FunctionDeclaration(
                name="rank_safepause_optimizer_areas",
                description=(
                    "Rank areas using verified SafePause Optimizer output at the "
                    "decision time; never substitute Heat Index for optimizer priority."
                ),
                parameters_json_schema={"type": "object", "properties": common},
            ),
            types.FunctionDeclaration(
                name="explain_demand",
                description="Return verified request demand across all areas, or for one explicitly named operating area.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {**common, "zone_name": zone_schema},
                },
            ),
            types.FunctionDeclaration(
                name="explain_city_safepause_decision",
                description=(
                    "Explain why the city-wide SafePause plan is needed across all areas "
                    "at the decision time."
                ),
                parameters_json_schema={"type": "object", "properties": common},
            ),
            types.FunctionDeclaration(
                name="explain_operational_impact",
                description=(
                    "Explain city-wide SafePause impact on fulfillment/coverage, "
                    "order impact, pickup ETA and verified data limits."
                ),
                parameters_json_schema={"type": "object", "properties": common},
            ),
            types.FunctionDeclaration(
                name="explain_safepause_decision",
                description=(
                    "Explain why SafePause is recommended for one area at the decision time, "
                    "including its guardrails."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {**common, "zone_name": zone_schema},
                },
            ),
            types.FunctionDeclaration(
                name="compare_safepause_options",
                description=(
                    "Compare verified SafePause recommendations across all areas and "
                    "portfolio alternatives for one selected area."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        **common,
                        "zone_name": zone_schema,
                        "scope": {
                            "type": "string",
                            "enum": ["selected_area", "all_areas"],
                        },
                    },
                },
            ),
            types.FunctionDeclaration(
                name="compare_operational_outcomes",
                description="Compare ACTIVATE and CONTINUE operational outcomes at the same time.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {**common, "zone_name": zone_schema},
                },
            ),
            types.FunctionDeclaration(
                name="get_operational_events",
                description="List verified rolling intervention events over an operational interval.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        **common,
                        "start_tick": tick_schema,
                        "end_tick": tick_schema,
                    },
                },
            ),
            types.FunctionDeclaration(
                name="get_operating_policy",
                description="Explain operating limits, rolling policy and evidence coverage.",
                parameters_json_schema={"type": "object", "properties": common},
            ),
        ]

    @staticmethod
    def _bounded_history(history: Sequence[Mapping[str, Any]]) -> str:
        lines: list[str] = []
        for message in history[-10:]:
            role = str(message.get("role", "user")).upper()
            content = str(message.get("content", ""))[:1_500]
            tool = message.get("tool")
            external_tool = _INTERNAL_TO_GEMINI_TOOL.get(str(tool), str(tool))
            suffix = f" [tool={external_tool}]" if tool else ""
            lines.append(f"{role}{suffix}: {content}")
        return "\n".join(lines) or "No prior conversation."

    def _allowed_tools_for_question(
        self,
        question: str,
        history: Sequence[Mapping[str, Any]] = (),
    ) -> list[str]:
        """Narrow explicit and contextual operational intents before model selection."""
        plain = _plain(question)
        last_tool = next(
            (
                str(message.get("tool"))
                for message in reversed(history)
                if message.get("role") == "assistant" and message.get("tool")
            ),
            "",
        )
        if any(
            token in plain
            for token in ("optimizer", "safepause priority", "priority areas")
        ):
            return ["rank_safepause_optimizer_areas"]
        if self._is_operational_impact_question(question) and (
            last_tool in {
                "explain_city_safepause_decision",
                "explain_replay_operational_impact",
                "explain_safepause_decision",
                "compare_recorded_safepause_options",
            }
            or any(token in plain for token in ("fulfillment", "fullfillment", "eta"))
        ):
            return ["explain_replay_operational_impact"]
        if any(
            token in plain
            for token in ("highest current risk", "current operational risk", "hotspot")
        ):
            return ["compare_replay_areas"]
        safepause_intent = any(
            token in plain
            for token in (
                "safepause",
                "safe pause",
                "phuong an",
                "tuy chon",
                "lua chon",
                "can thiep",
            )
        )
        if not safepause_intent:
            return sorted(_ALLOWED_TOOLS)
        comparison_requested = any(
            token in plain
            for token in (
                "compare",
                "so sanh",
                "tuy chon",
                "lua chon",
                "all",
                "across",
                "cac khu vuc",
                "toan thanh pho",
            )
        )
        if comparison_requested:
            return ["compare_recorded_safepause_options"]
        mentions_area = self._mentioned_zone_name(question) is not None
        if mentions_area:
            return [
                "compare_recorded_safepause_options"
                if comparison_requested
                else "explain_safepause_decision"
            ]
        return [
            "compare_recorded_safepause_options"
            if comparison_requested
            else "explain_city_safepause_decision"
        ]

    def _select_with_gemini(
        self,
        question: str,
        history: Sequence[Mapping[str, Any]],
    ) -> ReplayToolRequest:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True,
            project=self.settings.project_id,
            location=self.settings.vertex_location,
            http_options=types.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT_MS),
        )
        response = client.models.generate_content(
            model=self.settings.gemini_model,
            contents=(
                "Conversation history:\n"
                + self._bounded_history(history)
                + "\n\nCurrent question: "
                + question
                + f"\nCurrent operational context: time={self.context.time_label}, "
                f"selected_area={self.context.selected_zone_name}."
            ),
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are a strict function router for HeatSafe AI Ops. "
                    "Call exactly one allowlisted function and never answer with text. "
                    "Use conversation history only to resolve follow-ups such as 'what about another area'. "
                    "Never invent an area or request unavailable future evidence."
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
                        allowed_function_names=[
                            _INTERNAL_TO_GEMINI_TOOL[name]
                            for name in self._allowed_tools_for_question(
                                question, history
                            )
                        ],
                    )
                ),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
        calls = response.function_calls or []
        if len(calls) != 1:
            raise RuntimeError("Gemini must select exactly one operational function")
        call = calls[0]
        internal_tool = _GEMINI_TOOL_TO_INTERNAL.get(str(call.name))
        if internal_tool is None:
            raise RuntimeError("Gemini selected an unknown operational function")
        return self._scope_request_to_question(
            self._normalize_request(internal_tool, dict(call.args or {})), question
        )

    def _scope_request_to_question(
        self, request: ReplayToolRequest, question: str
    ) -> ReplayToolRequest:
        """Bind every request to the displayed operational frame and explicit area."""
        arguments = dict(request.arguments)
        arguments["tick_index"] = self.context.tick_index
        arguments["branch"] = self.context.branch
        mentioned_zone = self._mentioned_zone_name(question)
        area_scoped_tools = {
            "explain_replay_zone",
            "explain_replay_demand",
            "explain_safepause_decision",
            "compare_recorded_safepause_options",
            "compare_replay_branches",
        }
        if request.tool_name in area_scoped_tools:
            if mentioned_zone:
                arguments["zone_name"] = mentioned_zone
            else:
                arguments.pop("zone_name", None)
        if request.tool_name == "compare_recorded_safepause_options":
            arguments["scope"] = (
                "selected_area" if mentioned_zone else "all_areas"
            )
        return self._normalize_request(request.tool_name, arguments)

    def answer(
        self,
        question: str,
        history: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[str, str]:
        plain = _plain(question)
        if any(token in plain for token in ("xoa", "delete", "drop", "truncate")):
            return (
                "I cannot modify operational data. Copilot is read-only.",
                "safety_guard",
            )
        request: ReplayToolRequest
        if self.settings.enable_ai:
            try:
                request = self._select_with_gemini(question, history)
            except Exception as exc:
                log_event(
                    "replay_copilot_function_fallback",
                    severity="WARNING",
                    error_type=type(exc).__name__,
                    replay_tick=self.context.tick_index,
                    replay_branch=self.context.branch,
                )
                request = self._fallback_request(question, history)
        else:
            request = self._fallback_request(question, history)
        try:
            result = self._execute(request)
        except (KeyError, TypeError, ValueError) as exc:
            log_event(
                "replay_copilot_tool_rejected",
                severity="WARNING",
                error_type=type(exc).__name__,
                tool=request.tool_name,
            )
            return (
                "The requested data could not be verified for the selected time and area.",
                "replay_tool_unavailable",
            )
        return result.deterministic_answer, result.tool_name


__all__ = [
    "ReplayCopilot",
    "ReplayCopilotFrame",
    "ReplayKnowledgeBase",
    "ReplayToolRequest",
    "ReplayToolResult",
]
