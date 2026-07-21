from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..ai_decision import evaluate_rule_reference, recommend_ai_intervention
from ..models import (
    DecisionConstraints,
    DriverActionPrediction,
    RecommendationResult,
    SafePauseProposal,
    ZoneSnapshot,
)
from ..repository import DemandForecast

FORECAST_UNAVAILABLE = "FORECAST_UNAVAILABLE"
PREDICTIONS_UNAVAILABLE = "PREDICTIONS_UNAVAILABLE"
DECISION_UNAVAILABLE = "DECISION_UNAVAILABLE"
SNAPSHOT_MISMATCH = "SNAPSHOT_MISMATCH"


class DecisionRepository(Protocol):
    def forecast_demand(
        self, zone_id: str, horizon_minutes: int = 60
    ) -> DemandForecast: ...

    def forecast_demand_many(
        self, zone_ids: list[str], horizon_minutes: int = 60
    ) -> dict[str, DemandForecast]: ...

    def load_driver_predictions(
        self, zone_id: str, snapshot_id: str
    ) -> tuple[DriverActionPrediction, ...]: ...

    def load_driver_predictions_many(
        self, zone_ids: list[str], snapshot_id: str
    ) -> dict[str, tuple[DriverActionPrediction, ...]]: ...


@dataclass(frozen=True)
class SelectedZoneDecision:
    zone: ZoneSnapshot
    constraints: DecisionConstraints
    forecast: DemandForecast
    predictions: tuple[DriverActionPrediction, ...]
    recommendation: RecommendationResult
    rule_reference: SafePauseProposal | None

    @property
    def proposal(self) -> SafePauseProposal | None:
        return self.recommendation.recommended


@dataclass(frozen=True)
class CityPlanRow:
    zone: ZoneSnapshot
    forecast: DemandForecast
    predictions: tuple[DriverActionPrediction, ...]
    recommendation: RecommendationResult

    @property
    def zone_id(self) -> str:
        return self.zone.zone_id

    @property
    def proposal(self) -> SafePauseProposal:
        proposal = self.recommendation.recommended
        if proposal is None:  # City plans only retain actionable recommendations.
            raise RuntimeError("City plan row has no recommended proposal")
        return proposal


@dataclass(frozen=True)
class UnavailableZone:
    zone_id: str
    zone_name: str
    reason_code: str
    message: str

    @property
    def reason(self) -> str:
        return self.reason_code


@dataclass(frozen=True)
class CityWidePlan:
    rows: tuple[CityPlanRow, ...]
    unavailable_zones: tuple[UnavailableZone, ...]
    constraints: DecisionConstraints

    @property
    def successful_rows(self) -> tuple[CityPlanRow, ...]:
        return self.rows

    @property
    def unavailable(self) -> tuple[UnavailableZone, ...]:
        return self.unavailable_zones


def _constraints(value: DecisionConstraints | None) -> DecisionConstraints:
    return (value or DecisionConstraints()).normalized()


def _demand_intervals(forecast: DemandForecast) -> tuple[tuple[int, ...], tuple[int, ...]]:
    return (
        tuple(point.predicted_requests for point in forecast.points),
        tuple(point.upper_bound for point in forecast.points),
    )


def build_selected_zone_decision(
    repository: DecisionRepository,
    zone: ZoneSnapshot,
    constraints: DecisionConstraints | None = None,
) -> SelectedZoneDecision:
    """Build a selected-zone decision without UI or global state dependencies."""
    normalized = _constraints(constraints)
    forecast = repository.forecast_demand(zone.zone_id, normalized.horizon_minutes)
    predictions = repository.load_driver_predictions(zone.zone_id, zone.snapshot_id)
    demand, upper_demand = _demand_intervals(forecast)
    recommendation = recommend_ai_intervention(
        zone,
        predictions,
        demand_by_interval=demand,
        upper_demand_by_interval=upper_demand,
        budget_cap_vnd=normalized.budget_cap_vnd,
        sponsor_per_driver_vnd=normalized.sponsor_per_driver_vnd,
    )
    rule_reference = evaluate_rule_reference(
        zone,
        predictions,
        demand_by_interval=demand,
        upper_demand_by_interval=upper_demand,
        budget_cap_vnd=normalized.budget_cap_vnd,
        sponsor_per_driver_vnd=normalized.sponsor_per_driver_vnd,
    )
    return SelectedZoneDecision(
        zone=zone,
        constraints=normalized,
        forecast=forecast,
        predictions=predictions,
        recommendation=recommendation,
        rule_reference=rule_reference,
    )


def _unavailable(
    zone: ZoneSnapshot,
    reason_code: str,
    message: str,
) -> UnavailableZone:
    return UnavailableZone(
        zone_id=zone.zone_id,
        zone_name=zone.name,
        reason_code=reason_code,
        message=message,
    )


def _exception_message(exc: Exception) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def build_city_wide_plan(
    repository: DecisionRepository,
    zones: list[ZoneSnapshot] | tuple[ZoneSnapshot, ...],
    snapshot_id: str | DecisionConstraints | None = None,
    constraints: DecisionConstraints | None = None,
) -> CityWidePlan:
    """Build actionable city rows and explicit per-zone unavailability results.

    Demand and prediction batches are each loaded exactly once. ``snapshot_id`` may
    be omitted (it is then derived from the first zone); passing constraints as the
    third positional argument is also supported for convenience.
    """
    if isinstance(snapshot_id, DecisionConstraints):
        if constraints is not None:
            raise TypeError("constraints were provided twice")
        constraints = snapshot_id
        snapshot_id = None

    normalized = _constraints(constraints)
    ordered_zones = tuple(zones)
    zone_ids = list(dict.fromkeys(zone.zone_id for zone in ordered_zones))
    active_snapshot_id = snapshot_id or (
        ordered_zones[0].snapshot_id if ordered_zones else ""
    )

    forecast_error: Exception | None = None
    prediction_error: Exception | None = None
    try:
        forecasts = repository.forecast_demand_many(
            zone_ids, normalized.horizon_minutes
        )
    except Exception as exc:
        forecasts = {}
        forecast_error = exc

    try:
        predictions_by_zone = repository.load_driver_predictions_many(
            zone_ids, active_snapshot_id
        )
    except Exception as exc:
        predictions_by_zone = {}
        prediction_error = exc

    rows: list[CityPlanRow] = []
    unavailable: list[UnavailableZone] = []
    seen: set[str] = set()
    for zone in ordered_zones:
        if zone.zone_id in seen:
            continue
        seen.add(zone.zone_id)

        if zone.snapshot_id != active_snapshot_id:
            unavailable.append(
                _unavailable(
                    zone,
                    SNAPSHOT_MISMATCH,
                    f"Zone snapshot {zone.snapshot_id} does not match {active_snapshot_id}",
                )
            )
            continue
        if forecast_error is not None:
            unavailable.append(
                _unavailable(
                    zone,
                    FORECAST_UNAVAILABLE,
                    _exception_message(forecast_error),
                )
            )
            continue
        forecast = forecasts.get(zone.zone_id)
        if forecast is None:
            unavailable.append(
                _unavailable(
                    zone,
                    FORECAST_UNAVAILABLE,
                    "No demand forecast was returned for the zone",
                )
            )
            continue
        if prediction_error is not None:
            unavailable.append(
                _unavailable(
                    zone,
                    PREDICTIONS_UNAVAILABLE,
                    _exception_message(prediction_error),
                )
            )
            continue
        predictions = predictions_by_zone.get(zone.zone_id, ())
        if not predictions:
            unavailable.append(
                _unavailable(
                    zone,
                    PREDICTIONS_UNAVAILABLE,
                    "No driver predictions were returned for the zone",
                )
            )
            continue

        demand, upper_demand = _demand_intervals(forecast)
        try:
            recommendation = recommend_ai_intervention(
                zone,
                predictions,
                demand_by_interval=demand,
                upper_demand_by_interval=upper_demand,
                budget_cap_vnd=normalized.budget_cap_vnd,
                sponsor_per_driver_vnd=normalized.sponsor_per_driver_vnd,
            )
        except Exception as exc:
            unavailable.append(
                _unavailable(zone, DECISION_UNAVAILABLE, _exception_message(exc))
            )
            continue

        if recommendation.recommended is None:
            unavailable.append(
                _unavailable(
                    zone,
                    recommendation.status or DECISION_UNAVAILABLE,
                    recommendation.message or "No actionable recommendation was returned",
                )
            )
            continue
        rows.append(
            CityPlanRow(
                zone=zone,
                forecast=forecast,
                predictions=predictions,
                recommendation=recommendation,
            )
        )

    return CityWidePlan(
        rows=tuple(rows),
        unavailable_zones=tuple(unavailable),
        constraints=normalized,
    )


# Readable aliases for callers that prefer verb-oriented service names.
decide_selected_zone = build_selected_zone_decision
plan_city_wide = build_city_wide_plan


__all__ = [
    "CityPlanRow",
    "CityWidePlan",
    "DECISION_UNAVAILABLE",
    "DecisionRepository",
    "FORECAST_UNAVAILABLE",
    "PREDICTIONS_UNAVAILABLE",
    "SNAPSHOT_MISMATCH",
    "SelectedZoneDecision",
    "UnavailableZone",
    "build_city_wide_plan",
    "build_selected_zone_decision",
    "decide_selected_zone",
    "plan_city_wide",
]
