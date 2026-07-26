"""Normalized evidence adapters and lightweight preventive projection.

This module deliberately separates current observation evidence from accelerated
scenario evidence.  Current operations never loads a scenario fixture; callers
must pass the exact current snapshot, feature batch, predictions and demand
forecast through the repository protocol below.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ..ai_decision import ACTION_DELAYS, recommend_ai_intervention
from ..demand_profile import intraday_demand_factor
from ..ingestion import calculate_heat_index
from ..models import (
    AcceleratedForecastInput,
    CityForecastProjection,
    CurrentForecastInput,
    DecisionConstraints,
    DriverActionPrediction,
    DriverCurrentFeature,
    DriverForecastHorizon,
    DriverForecastProjection,
    ForecastDemandPoint,
    ForecastDriverAction,
    ForecastDriverInput,
    ForecastEvidenceLineage,
    ForecastHorizon,
    ForecastZoneInput,
    HeatForecastEvidence,
    InterventionWindow,
    PredictiveCityPlan,
    PredictiveZonePlanRow,
    SafePauseProposal,
    ZoneForecastProjection,
    ZoneSnapshot,
)
from ..repository import DemandForecast
from ..simulation.engine import weather_at
from ..simulation.models import ACTIVE_STATUSES, TickResult, ZonePrior
from ..simulation.randomness import stable_int
from ..simulation.scenario import ScenarioFixture

FORECAST_HORIZONS = (0, 60, 120)
FORECAST_PATH_COUNT = 64
FORECAST_SEED = 20_260_726
PROJECTED_MANDATORY_PROBABILITY = 0.50
PROJECTION_VERSION = "preventive-projection-v1"
PROJECTED_RISK_VERSION = "projected-risk-scorer-v1"
MICROCLIMATE_MODEL_VERSION = "hanoi-demo-snapshot-offset-v1"
RECOVERY_RESET_MINUTES = 15
MANDATORY_EXPOSURE_MINUTES = 240

# Reviewed, zero-centered synthetic district offsets.  Their source is the
# existing demo snapshot rather than a future scenario tick.  They are applied
# to Current only when the provider supplies one identical city-wide weather
# value for every district, and the result is labelled MODELED.
HANOI_MICROCLIMATE_OFFSETS_C = {
    "hoan-kiem": 0.9,
    "hai-ba-trung": 0.5,
    "dong-da": 0.4,
    "ba-dinh": 0.3,
    "cau-giay": 0.0,
    "thanh-xuan": -0.1,
    "hoang-mai": -0.2,
    "nam-tu-liem": -0.4,
    "ha-dong": -0.6,
    "bac-tu-liem": -0.8,
}


class ForecastInputError(RuntimeError):
    """Raised when exact-snapshot city evidence is incomplete or mixed."""


class CurrentEvidenceRepository(Protocol):
    def load_driver_features_many(
        self, zone_ids: list[str], snapshot_id: str
    ) -> dict[str, tuple[DriverCurrentFeature, ...]]: ...

    def load_driver_predictions_many(
        self, zone_ids: list[str], snapshot_id: str
    ) -> dict[str, tuple[DriverActionPrediction, ...]]: ...

    def forecast_demand_many(
        self, zone_ids: list[str], horizon_minutes: int = 120
    ) -> dict[str, DemandForecast]: ...


class ProjectedRiskScorerV1:
    """Transparent engineering projection anchored to current model risk."""

    version = PROJECTED_RISK_VERSION

    @staticmethod
    def score(
        *,
        baseline_risk: float,
        current_exposure_minutes: int,
        projected_exposure_minutes: int,
        current_heat_index_c: float,
        projected_heat_index_c: float,
        recovered: bool,
    ) -> float:
        base = min(1.0 - 1e-6, max(1e-6, float(baseline_risk)))
        logit = math.log(base / (1.0 - base))
        exposure_delta = (
            projected_exposure_minutes - current_exposure_minutes
        ) / 150.0
        heat_delta = (projected_heat_index_c - current_heat_index_c) * 0.06
        recovery_credit = -0.75 if recovered else 0.0
        value = 1.0 / (
            1.0
            + math.exp(
                -(logit + exposure_delta + heat_delta + recovery_credit)
            )
        )
        return min(1.0, max(0.0, value))


def _validated_zones(
    zones: Iterable[ZoneSnapshot],
    *,
    expected_zone_count: int,
) -> tuple[ZoneSnapshot, ...]:
    ordered = tuple(sorted(zones, key=lambda item: item.zone_id))
    if len(ordered) != expected_zone_count:
        raise ForecastInputError(
            f"expected {expected_zone_count} zones; found {len(ordered)}"
        )
    zone_ids = {zone.zone_id for zone in ordered}
    snapshot_ids = {zone.snapshot_id for zone in ordered}
    scenario_ids = {zone.scenario_id for zone in ordered}
    if len(zone_ids) != expected_zone_count:
        raise ForecastInputError("zone evidence contains duplicate zone IDs")
    if len(snapshot_ids) != 1 or len(scenario_ids) != 1:
        raise ForecastInputError("zone evidence has mixed snapshot/scenario lineage")
    return ordered


def _demand_points(forecast: DemandForecast) -> tuple[ForecastDemandPoint, ...]:
    points = tuple(
        ForecastDemandPoint(
            minutes_ahead=(index + 1) * 15,
            median_requests=max(0, int(point.predicted_requests)),
            upper_requests=max(0, int(point.upper_bound)),
        )
        for index, point in enumerate(forecast.points[:8])
    )
    if len(points) != 8:
        raise ForecastInputError(
            f"zone {forecast.zone_id} requires eight 15-minute demand points"
        )
    return points


def _current_heat(
    zones: tuple[ZoneSnapshot, ...],
) -> dict[str, tuple[HeatForecastEvidence, ...]]:
    one_city_value = len({round(zone.heat_index_c, 2) for zone in zones}) == 1
    result: dict[str, tuple[HeatForecastEvidence, ...]] = {}
    for zone in zones:
        if one_city_value:
            if zone.zone_id not in HANOI_MICROCLIMATE_OFFSETS_C:
                raise ForecastInputError(
                    f"no modeled microclimate offset for {zone.zone_id}"
                )
            temperature = round(
                zone.temperature_c + HANOI_MICROCLIMATE_OFFSETS_C[zone.zone_id],
                4,
            )
            heat_index = round(
                calculate_heat_index(temperature, zone.humidity_percent),
                4,
            )
            now_provenance = "MODELED_MICROCLIMATE_OFFSET"
            future_provenance = "MODELED_MICROCLIMATE_HELD_CONSTANT"
            model_version = MICROCLIMATE_MODEL_VERSION
        else:
            temperature = zone.temperature_c
            heat_index = zone.heat_index_c
            now_provenance = (
                "SIMULATED_OBSERVATION"
                if zone.weather_is_simulated
                else "OBSERVED"
            )
            future_provenance = (
                "SIMULATED_HELD_CONSTANT"
                if zone.weather_is_simulated
                else "OBSERVED_HELD_CONSTANT"
            )
            model_version = None
        result[zone.zone_id] = tuple(
            HeatForecastEvidence(
                minutes_ahead=horizon,
                temperature_c=temperature,
                humidity_percent=zone.humidity_percent,
                heat_index_c=heat_index,
                provenance=(
                    now_provenance if horizon == 0 else future_provenance
                ),
                model_version=model_version,
            )
            for horizon in FORECAST_HORIZONS
        )
    return result


def _baseline_by_driver(
    predictions: tuple[DriverActionPrediction, ...],
    *,
    zone_id: str,
    snapshot_id: str,
) -> tuple[dict[str, float], tuple[str, ...], tuple[str, ...]]:
    baseline: dict[str, float] = {}
    prediction_runs: set[str] = set()
    model_versions: set[str] = set()
    for prediction in predictions:
        if prediction.zone_id != zone_id or prediction.snapshot_id != snapshot_id:
            raise ForecastInputError(
                f"prediction lineage mismatch for zone {zone_id}"
            )
        current = baseline.setdefault(
            prediction.driver_id_hash, prediction.baseline_risk
        )
        if not math.isclose(
            current, prediction.baseline_risk, rel_tol=0.0, abs_tol=1e-9
        ):
            raise ForecastInputError(
                f"driver {prediction.driver_id_hash} has mixed baseline risk"
            )
        prediction_runs.add(prediction.prediction_run_id)
        model_versions.add(prediction.model_version)
    return (
        baseline,
        tuple(sorted(prediction_runs)),
        tuple(sorted(model_versions)),
    )


def build_current_forecast_input(
    repository: CurrentEvidenceRepository,
    zones: Iterable[ZoneSnapshot],
    *,
    expected_zone_count: int = 10,
) -> CurrentForecastInput:
    """Build Current evidence using only the supplied current repository."""
    ordered = _validated_zones(zones, expected_zone_count=expected_zone_count)
    zone_ids = [zone.zone_id for zone in ordered]
    snapshot_id = ordered[0].snapshot_id
    features_by_zone = repository.load_driver_features_many(zone_ids, snapshot_id)
    predictions_by_zone = repository.load_driver_predictions_many(
        zone_ids, snapshot_id
    )
    forecasts = repository.forecast_demand_many(zone_ids, 120)
    heat_by_zone = _current_heat(ordered)

    zone_inputs: list[ForecastZoneInput] = []
    prediction_run_ids: set[str] = set()
    model_versions: set[str] = set()
    for zone in ordered:
        features = tuple(features_by_zone.get(zone.zone_id, ()))
        predictions = tuple(predictions_by_zone.get(zone.zone_id, ()))
        forecast = forecasts.get(zone.zone_id)
        if not features or not predictions or forecast is None:
            raise ForecastInputError(
                f"incomplete current evidence for zone {zone.zone_id}"
            )
        baseline, runs, versions = _baseline_by_driver(
            predictions,
            zone_id=zone.zone_id,
            snapshot_id=snapshot_id,
        )
        prediction_run_ids.update(runs)
        model_versions.update(versions)
        if (
            not forecast.forecast_reused
            and forecast.forecast_source_snapshot_id is not None
            and forecast.forecast_source_snapshot_id != snapshot_id
        ):
            raise ForecastInputError(
                f"demand forecast lineage mismatch for zone {zone.zone_id}"
            )
        driver_inputs: list[ForecastDriverInput] = []
        seen: set[str] = set()
        for feature in sorted(features, key=lambda item: item.driver_id_hash):
            if (
                feature.zone_id != zone.zone_id
                or feature.snapshot_id != snapshot_id
                or feature.scenario_id != zone.scenario_id
            ):
                raise ForecastInputError(
                    f"driver feature lineage mismatch for zone {zone.zone_id}"
                )
            if feature.driver_id_hash in seen:
                raise ForecastInputError(
                    f"duplicate current feature for driver {feature.driver_id_hash}"
                )
            seen.add(feature.driver_id_hash)
            if feature.driver_id_hash not in baseline:
                raise ForecastInputError(
                    f"missing current BQML baseline for {feature.driver_id_hash}"
                )
            action_rows = tuple(
                ForecastDriverAction(
                    pause_start_delay_minutes=item.pause_start_delay_minutes,
                    pause_duration_minutes=item.pause_duration_minutes,
                    action_risk=min(1.0, max(0.0, item.action_risk)),
                    top_factors=item.top_factors,
                )
                for item in sorted(
                    (
                        prediction
                        for prediction in predictions
                        if prediction.driver_id_hash == feature.driver_id_hash
                    ),
                    key=lambda item: (
                        item.pause_start_delay_minutes,
                        item.pause_duration_minutes,
                    ),
                )
            )
            driver_inputs.append(
                ForecastDriverInput(
                    driver_id_hash=feature.driver_id_hash,
                    zone_id=feature.zone_id,
                    status=feature.driver_status,
                    continuous_exposure_minutes=max(
                        0, feature.continuous_exposure_minutes
                    ),
                    rest_minutes_120m=max(0, feature.rest_minutes_120m),
                    hydration_gap_minutes=max(0, feature.hydration_gap_minutes),
                    heat_dose_120m=max(0.0, feature.heat_dose_120m),
                    baseline_risk=min(
                        1.0, max(0.0, baseline[feature.driver_id_hash])
                    ),
                    actions=action_rows,
                )
            )
        if set(baseline) != seen:
            raise ForecastInputError(
                f"feature/prediction driver set mismatch for zone {zone.zone_id}"
            )
        zone_inputs.append(
            ForecastZoneInput(
                zone=zone,
                heat=heat_by_zone[zone.zone_id],
                demand=_demand_points(forecast),
                drivers=tuple(driver_inputs),
            )
        )
    generator_versions = {
        feature.generator_version
        for rows in features_by_zone.values()
        for feature in rows
        if feature.generator_version
    }
    simulation_run_ids = {
        feature.simulation_run_id
        for rows in features_by_zone.values()
        for feature in rows
        if feature.simulation_run_id
    }
    tick_ids = {
        feature.tick_id
        for rows in features_by_zone.values()
        for feature in rows
        if feature.tick_id
    }
    if len(simulation_run_ids) > 1 or len(tick_ids) > 1:
        raise ForecastInputError("current driver features have mixed run/tick lineage")
    return CurrentForecastInput(
        lineage=ForecastEvidenceLineage(
            mode="CURRENT",
            scenario_id=ordered[0].scenario_id,
            snapshot_id=snapshot_id,
            observed_at=max(zone.observed_at for zone in ordered),
            prediction_run_ids=tuple(sorted(prediction_run_ids)),
            model_versions=tuple(sorted(model_versions)),
            simulation_run_id=next(iter(simulation_run_ids), None),
            tick_id=next(iter(tick_ids), None),
            generator_version=(
                ",".join(sorted(generator_versions))
                if generator_versions
                else None
            ),
        ),
        zones=tuple(zone_inputs),
    )


def _accelerated_demand(
    result: TickResult,
    zone: ZonePrior,
) -> tuple[ForecastDemandPoint, ...]:
    projection = next(item for item in result.zones if item.zone_id == zone.zone_id)
    current_requests = max(1, projection.requests_15m)
    zone_seed = stable_int(zone.zone_id, bits=64)
    current_factor = max(
        0.08, intraday_demand_factor(result.simulation_time, zone_seed)
    )
    points = []
    for index in range(8):
        minutes_ahead = (index + 1) * 15
        at = result.simulation_time + timedelta(minutes=minutes_ahead)
        factor = intraday_demand_factor(at, zone_seed)
        median = max(0, round(current_requests * factor / current_factor))
        uncertainty = 0.12 + min(0.08, index * 0.01)
        points.append(
            ForecastDemandPoint(
                minutes_ahead=minutes_ahead,
                median_requests=median,
                upper_requests=round(median * (1 + uncertainty)),
            )
        )
    return tuple(points)


def _accelerated_baseline(
    *,
    exposure_minutes: int,
    heat_dose_120m: float,
    rest_minutes_120m: int,
) -> float:
    value = 1.0 / (
        1.0
        + math.exp(
            -(
                -3.2
                + exposure_minutes / 125.0
                + heat_dose_120m / 90.0
                - rest_minutes_120m / 80.0
            )
        )
    )
    return min(1.0, max(0.0, value))


def _accelerated_actions(baseline_risk: float) -> tuple[ForecastDriverAction, ...]:
    return tuple(
        ForecastDriverAction(
            pause_start_delay_minutes=delay,
            pause_duration_minutes=duration,
            action_risk=max(
                0.0,
                baseline_risk
                - (duration / 30.0) * math.exp(-delay / 60.0) * 0.18,
            ),
            top_factors=("projected_exposure", "heat_dose", "recovery_timing"),
        )
        for duration in (15, 30)
        for delay in (0, 15, 30, 45)
    )


def _simulation_tick_id(run_id: str, tick_index: int) -> str:
    payload = f"simulation-tick:{run_id}:{tick_index}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def _simulation_snapshot_id(run_id: str, tick_index: int) -> str:
    payload = f"simulation-snapshot:{run_id}:{tick_index}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def build_accelerated_forecast_input(
    result: TickResult,
    *,
    fixture: ScenarioFixture,
    zones: tuple[ZonePrior, ...],
    expected_zone_count: int = 10,
    durable_run_id: str | None = None,
    durable_tick_id: str | None = None,
    durable_snapshot_id: str | None = None,
) -> AcceleratedForecastInput:
    """Normalize exact-tick accelerated evidence from an explicit fixture."""
    if len(zones) != expected_zone_count:
        raise ForecastInputError(
            f"expected {expected_zone_count} accelerated zones; found {len(zones)}"
        )
    if len({zone.zone_id for zone in zones}) != expected_zone_count:
        raise ForecastInputError("accelerated zone priors contain duplicates")
    offsets = fixture.manifest["zone_weather_offsets"]
    projections = {item.zone_id: item for item in result.zones}
    run_id = durable_run_id or result.state.run_id
    tick_id = durable_tick_id or _simulation_tick_id(
        run_id, result.tick_index
    )
    snapshot_id = durable_snapshot_id or _simulation_snapshot_id(
        run_id, result.tick_index
    )
    zone_inputs: list[ForecastZoneInput] = []
    for zone in sorted(zones, key=lambda item: item.zone_id):
        if zone.zone_id not in projections or zone.zone_id not in offsets:
            raise ForecastInputError(
                f"incomplete accelerated evidence for zone {zone.zone_id}"
            )
        offset = float(offsets[zone.zone_id])
        heat_rows = []
        for horizon in FORECAST_HORIZONS:
            weather = (
                result.weather
                if horizon == 0
                else weather_at(
                    fixture,
                    min(95 * 15, result.state.minute_index + horizon),
                )
            )
            temperature = round(weather.temperature_c + offset, 4)
            heat_rows.append(
                HeatForecastEvidence(
                    minutes_ahead=horizon,
                    temperature_c=temperature,
                    humidity_percent=weather.humidity_percent,
                    heat_index_c=round(
                        calculate_heat_index(
                            temperature, weather.humidity_percent
                        ),
                        4,
                    ),
                    provenance="SIMULATED_MICROCLIMATE_FORECAST",
                    model_version=str(
                        fixture.manifest["zone_weather_offset_method"]["type"]
                    ),
                )
            )
        driver_inputs = []
        for driver in sorted(
            result.state.drivers, key=lambda item: item.driver_id_hash
        ):
            if driver.zone_id != zone.zone_id or driver.status not in ACTIVE_STATUSES:
                continue
            baseline_risk = _accelerated_baseline(
                    exposure_minutes=driver.continuous_exposure_minutes,
                    heat_dose_120m=driver.heat_dose_120m,
                    rest_minutes_120m=driver.rest_minutes_120m,
                )
            driver_inputs.append(
                ForecastDriverInput(
                    driver_id_hash=driver.driver_id_hash,
                    zone_id=driver.zone_id,
                    status=driver.status.value,
                    continuous_exposure_minutes=driver.continuous_exposure_minutes,
                    rest_minutes_120m=driver.rest_minutes_120m,
                    hydration_gap_minutes=driver.hydration_gap_minutes,
                    heat_dose_120m=driver.heat_dose_120m,
                    baseline_risk=baseline_risk,
                    actions=_accelerated_actions(baseline_risk),
                )
            )
        projection = projections[zone.zone_id]
        current_heat = heat_rows[0]
        demand_rows = _accelerated_demand(result, zone)
        zone_inputs.append(
            ForecastZoneInput(
                zone=ZoneSnapshot(
                    zone_id=zone.zone_id,
                    name=zone.name,
                    latitude=zone.latitude,
                    longitude=zone.longitude,
                    temperature_c=current_heat.temperature_c,
                    humidity_percent=current_heat.humidity_percent,
                    heat_index_c=current_heat.heat_index_c,
                    observed_at=result.simulation_time,
                    scenario_id=str(fixture.manifest["scenario_id"]),
                    snapshot_id=snapshot_id,
                    weather_observed_at=result.simulation_time,
                    operations_observed_at=result.simulation_time,
                    active_drivers=projection.active_drivers,
                    fresh_drivers=projection.fresh_drivers,
                    exposed_2h=projection.exposed_2h,
                    exposed_4h=projection.exposed_4h,
                    forecast_requests_30m=sum(
                        item.median_requests for item in demand_rows[:2]
                    ),
                    avg_platform_contribution_vnd=zone.avg_platform_contribution_vnd,
                    avg_driver_earnings_vnd=zone.avg_driver_earnings_vnd,
                    coolstop_name=zone.coolstop_name,
                    coolstop_latitude=zone.coolstop_latitude,
                    coolstop_longitude=zone.coolstop_longitude,
                    source=(
                        "Stateful production engine · simulated microclimate"
                    ),
                    weather_is_simulated=True,
                    operations_is_simulated=True,
                    simulation_run_id=run_id,
                    tick_id=tick_id,
                    generator_version=result.state.generator_version,
                ),
                heat=tuple(heat_rows),
                demand=demand_rows,
                drivers=tuple(driver_inputs),
            )
        )
    return AcceleratedForecastInput(
        lineage=ForecastEvidenceLineage(
            mode="ACCELERATED",
            scenario_id=str(fixture.manifest["scenario_id"]),
            snapshot_id=snapshot_id,
            observed_at=result.simulation_time,
            prediction_run_ids=(
                f"sim-{run_id}-tick-{result.tick_index:02d}",
            ),
            model_versions=(PROJECTED_RISK_VERSION,),
            simulation_run_id=run_id,
            tick_id=tick_id,
            tick_index=result.tick_index,
            scenario_version=result.state.scenario_version,
            generator_version=result.state.generator_version,
        ),
        zones=tuple(zone_inputs),
    )


def _unit_interval(*parts: object) -> float:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value / 2**64


def _demand_at(
    zone: ForecastZoneInput,
    horizon: int,
) -> ForecastDemandPoint:
    if horizon == 0:
        first = zone.demand[0]
        return ForecastDemandPoint(
            minutes_ahead=0,
            median_requests=first.median_requests,
            upper_requests=first.upper_requests,
        )
    try:
        return next(item for item in zone.demand if item.minutes_ahead == horizon)
    except StopIteration as exc:
        raise ForecastInputError(
            f"zone {zone.zone.zone_id} has no demand at +{horizon}m"
        ) from exc


def _demand_for_window(
    zone: ForecastZoneInput,
    start_delay_minutes: int,
) -> ForecastDemandPoint:
    index = min(
        len(zone.demand) - 1,
        max(0, start_delay_minutes // 15),
    )
    return zone.demand[index]


def _heat_at(
    zone: ForecastZoneInput,
    horizon: int,
) -> HeatForecastEvidence:
    try:
        return next(item for item in zone.heat if item.minutes_ahead == horizon)
    except StopIteration as exc:
        raise ForecastInputError(
            f"zone {zone.zone.zone_id} has no heat at +{horizon}m"
        ) from exc


def _online_probability(
    driver: ForecastDriverInput,
    *,
    horizon: int,
    demand_ratio: float,
) -> float:
    if horizon == 0:
        return 1.0
    status = driver.status.upper()
    if status == "OFFLINE":
        base = 0.05
    elif status in {"PAUSED", "TO_COOLSTOP"}:
        base = 0.35
    else:
        base = 0.90
    decay = 0.12 * (horizon / 120.0)
    exposure_penalty = max(
        0.0, driver.continuous_exposure_minutes - 180
    ) / 600.0
    demand_adjustment = max(-0.08, min(0.08, (demand_ratio - 1.0) * 0.08))
    return min(
        0.98,
        max(0.02, base - decay - exposure_penalty + demand_adjustment),
    )


def project_city_forecast(
    evidence: CurrentForecastInput | AcceleratedForecastInput,
    *,
    path_count: int = FORECAST_PATH_COUNT,
    seed: int = FORECAST_SEED,
) -> CityForecastProjection:
    """Project all zones using common deterministic path IDs."""
    if path_count <= 0:
        raise ValueError("path_count must be positive")
    path_ids = tuple(f"path-{index:02d}" for index in range(path_count))
    zone_results: list[ZoneForecastProjection] = []
    for zone in sorted(evidence.zones, key=lambda item: item.zone.zone_id):
        current_heat = _heat_at(zone, 0)
        current_demand = max(1, _demand_at(zone, 0).median_requests)
        drivers = tuple(
            sorted(zone.drivers, key=lambda item: item.driver_id_hash)
        )
        mandatory_now = sum(
            driver.continuous_exposure_minutes >= MANDATORY_EXPOSURE_MINUTES
            for driver in drivers
        )
        horizons: list[ForecastHorizon] = []
        driver_horizons: dict[str, list[DriverForecastHorizon]] = {
            driver.driver_id_hash: [] for driver in drivers
        }
        for horizon in evidence.horizons:
            heat = _heat_at(zone, horizon)
            demand = _demand_at(zone, horizon)
            if horizon == 0:
                for driver in drivers:
                    driver_horizons[driver.driver_id_hash].append(
                        DriverForecastHorizon(
                            minutes_ahead=0,
                            crossing_probability=(
                                1.0
                                if driver.continuous_exposure_minutes
                                >= MANDATORY_EXPOSURE_MINUTES
                                else 0.0
                            ),
                            online_probability=1.0,
                            projected_risk=driver.baseline_risk,
                        )
                    )
                horizons.append(
                    ForecastHorizon(
                        minutes_ahead=0,
                        heat=heat,
                        demand_median=demand.median_requests,
                        demand_upper=demand.upper_requests,
                        mandatory_now=mandatory_now,
                        projected_mandatory=0,
                        watchlist=0,
                        expected_crossers=0.0,
                        online_continuation_probability=1.0,
                        baseline_expected_risk=round(
                            sum(driver.baseline_risk for driver in drivers),
                            6,
                        ),
                    )
                )
                continue
            crossing_probabilities: list[float] = []
            online_probabilities: list[float] = []
            projected_risks: list[float] = []
            demand_ratio = demand.median_requests / current_demand
            for driver in drivers:
                probability = _online_probability(
                    driver,
                    horizon=horizon,
                    demand_ratio=demand_ratio,
                )
                online_paths = 0
                crossed_paths = 0
                risk_total = 0.0
                for path_id in path_ids:
                    stays_online = (
                        _unit_interval(
                            seed,
                            path_id,
                            driver.driver_id_hash,
                            horizon,
                            "online",
                        )
                        < probability
                    )
                    if stays_online:
                        online_paths += 1
                        online_minutes = horizon
                        recovered = False
                        projected_exposure = (
                            driver.continuous_exposure_minutes + horizon
                        )
                    else:
                        status = driver.status.upper()
                        if status in {"OFFLINE", "PAUSED", "TO_COOLSTOP"}:
                            online_minutes = 0
                        else:
                            slot_count = max(1, horizon // 15)
                            stop_slot = int(
                                _unit_interval(
                                    seed,
                                    path_id,
                                    driver.driver_id_hash,
                                    horizon,
                                    "stop",
                                )
                                * slot_count
                            )
                            online_minutes = min(horizon, stop_slot * 15)
                        recovered = (
                            horizon - online_minutes >= RECOVERY_RESET_MINUTES
                        )
                        projected_exposure = (
                            0
                            if recovered
                            else driver.continuous_exposure_minutes
                            + online_minutes
                        )
                    if (
                        driver.continuous_exposure_minutes
                        + online_minutes
                        >= MANDATORY_EXPOSURE_MINUTES
                    ):
                        crossed_paths += 1
                    risk_total += ProjectedRiskScorerV1.score(
                        baseline_risk=driver.baseline_risk,
                        current_exposure_minutes=driver.continuous_exposure_minutes,
                        projected_exposure_minutes=projected_exposure,
                        current_heat_index_c=current_heat.heat_index_c,
                        projected_heat_index_c=heat.heat_index_c,
                        recovered=recovered,
                    )
                online_probability = online_paths / path_count
                crossing_probability = (
                    crossed_paths / path_count
                    if driver.continuous_exposure_minutes
                    < MANDATORY_EXPOSURE_MINUTES
                    else 1.0
                )
                projected_risk = risk_total / path_count
                online_probabilities.append(online_probability)
                if driver.continuous_exposure_minutes < MANDATORY_EXPOSURE_MINUTES:
                    crossing_probabilities.append(crossing_probability)
                projected_risks.append(projected_risk)
                driver_horizons[driver.driver_id_hash].append(
                    DriverForecastHorizon(
                        minutes_ahead=horizon,
                        crossing_probability=crossing_probability,
                        online_probability=online_probability,
                        projected_risk=projected_risk,
                    )
                )
            projected_mandatory = sum(
                probability >= PROJECTED_MANDATORY_PROBABILITY
                for probability in crossing_probabilities
            )
            watchlist = sum(
                0.0 < probability < PROJECTED_MANDATORY_PROBABILITY
                for probability in crossing_probabilities
            )
            horizons.append(
                ForecastHorizon(
                    minutes_ahead=horizon,
                    heat=heat,
                    demand_median=demand.median_requests,
                    demand_upper=demand.upper_requests,
                    mandatory_now=mandatory_now,
                    projected_mandatory=projected_mandatory,
                    watchlist=watchlist,
                    expected_crossers=round(sum(crossing_probabilities), 6),
                    online_continuation_probability=round(
                        (
                            sum(online_probabilities)
                            / len(online_probabilities)
                            if online_probabilities
                            else 0.0
                        ),
                        6,
                    ),
                    baseline_expected_risk=round(sum(projected_risks), 6),
                )
            )
        zone_results.append(
            ZoneForecastProjection(
                zone_id=zone.zone.zone_id,
                zone_name=zone.zone.name,
                horizons=tuple(horizons),
                drivers=tuple(
                    DriverForecastProjection(
                        driver_id_hash=driver.driver_id_hash,
                        horizons=tuple(driver_horizons[driver.driver_id_hash]),
                    )
                    for driver in drivers
                ),
                source=zone,
            )
        )
    return CityForecastProjection(
        lineage=evidence.lineage,
        zones=tuple(zone_results),
        path_ids=path_ids,
        projection_version=PROJECTION_VERSION,
    )


def _nearest_rank_p95(values: tuple[int, ...]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _driver_predictions(
    city: CityForecastProjection,
    zone: ZoneForecastProjection,
) -> tuple[DriverActionPrediction, ...]:
    if zone.source is None:
        return ()
    prediction_run_id = (
        city.lineage.prediction_run_ids[0]
        if city.lineage.prediction_run_ids
        else "projection-unavailable"
    )
    model_version = (
        city.lineage.model_versions[0]
        if city.lineage.model_versions
        else PROJECTED_RISK_VERSION
    )
    return tuple(
        DriverActionPrediction(
            driver_id_hash=driver.driver_id_hash,
            zone_id=driver.zone_id,
            snapshot_id=city.lineage.snapshot_id,
            prediction_run_id=prediction_run_id,
            model_version=model_version,
            exposure_minutes=driver.continuous_exposure_minutes,
            baseline_risk=driver.baseline_risk,
            action_risk=action.action_risk,
            pause_start_delay_minutes=action.pause_start_delay_minutes,
            pause_duration_minutes=action.pause_duration_minutes,
            top_factors=action.top_factors,
        )
        for driver in zone.source.drivers
        for action in driver.actions
    )


def _driver_horizon(
    driver: DriverForecastProjection,
    horizon: int,
) -> DriverForecastHorizon:
    return next(item for item in driver.horizons if item.minutes_ahead == horizon)


def _window_outcome(
    city: CityForecastProjection,
    zone: ZoneForecastProjection,
    proposal: SafePauseProposal,
    *,
    seed: int,
) -> InterventionWindow:
    assert zone.source is not None
    start = min(wave.start_minute for wave in proposal.wave_plan)
    end = max(wave.end_minute for wave in proposal.wave_plan)
    demand = _demand_for_window(zone.source, start)
    demand_ratio = demand.upper_requests / max(1, demand.median_requests)
    path_costs: list[int] = []
    for path_id in city.path_ids:
        common_shock = _unit_interval(seed, path_id, "city-cost")
        zone_shock = _unit_interval(seed, path_id, zone.zone_id, "zone-cost")
        stress = 0.75 * common_shock + 0.25 * zone_shock
        lost_contribution = round(
            proposal.lost_contribution_vnd
            * (1.0 + stress * max(0.0, demand_ratio - 1.0))
        )
        path_costs.append(
            max(
                0,
                proposal.earnings_guard_cost_vnd
                + lost_contribution
                - proposal.partner_sponsorship_vnd,
            )
        )

    selected = {
        item.driver_id_hash: item for item in proposal.driver_decisions
    }

    def outcome(horizon: int) -> tuple[float, float]:
        projected_after = 0.0
        residual_risk = 0.0
        source_drivers = {
            item.driver_id_hash: item for item in zone.source.drivers
        }
        for projected_driver in zone.drivers:
            current = source_drivers[projected_driver.driver_id_hash]
            projected = _driver_horizon(projected_driver, horizon)
            decision = selected.get(projected_driver.driver_id_hash)
            valid_recovery = (
                decision is not None
                and decision.pause_duration_minutes >= RECOVERY_RESET_MINUTES
                and decision.pause_start_delay_minutes <= max(
                    0,
                    MANDATORY_EXPOSURE_MINUTES
                    - current.continuous_exposure_minutes,
                )
                and decision.pause_start_delay_minutes
                + RECOVERY_RESET_MINUTES
                <= horizon
            )
            if (
                current.continuous_exposure_minutes
                < MANDATORY_EXPOSURE_MINUTES
                and not valid_recovery
            ):
                projected_after += projected.crossing_probability
            if decision is None:
                residual_risk += projected.projected_risk
            else:
                reduction_ratio = (
                    decision.risk_reduction / decision.baseline_risk
                    if decision.baseline_risk > 0
                    else 0.0
                )
                residual_risk += projected.projected_risk * (
                    1.0 - min(1.0, reduction_ratio)
                )
        return round(projected_after, 6), round(residual_risk, 6)

    projected_60, residual_60 = outcome(60)
    projected_120, residual_120 = outcome(120)
    paths = tuple(path_costs)
    return InterventionWindow(
        start_delay_minutes=start,
        end_delay_minutes=end,
        proposal=proposal,
        path_costs_vnd=paths,
        expected_cost_vnd=round(sum(paths) / len(paths)),
        p95_reserved_cost_vnd=_nearest_rank_p95(paths),
        projected_mandatory_after_60m=projected_60,
        projected_mandatory_after_120m=projected_120,
        residual_risk_60m=residual_60,
        residual_risk_120m=residual_120,
    )


def _rank(
    items: list[tuple[str, tuple[object, ...]]],
) -> dict[str, int]:
    return {
        zone_id: index
        for index, (zone_id, _) in enumerate(
            sorted(items, key=lambda item: (*item[1], item[0])),
            start=1,
        )
    }


def build_predictive_city_plan(
    city: CityForecastProjection,
    constraints: DecisionConstraints,
    *,
    expected_zone_count: int = 10,
    seed: int = FORECAST_SEED,
) -> PredictiveCityPlan:
    """Build one deterministic, cap-compliant city plan from all-zone evidence."""
    constraints = constraints.normalized()
    if len(city.zones) != expected_zone_count:
        raise ForecastInputError(
            f"expected {expected_zone_count} projected zones; found {len(city.zones)}"
        )
    drafts: list[dict[str, object]] = []
    evidence_unavailable = False
    for zone in sorted(city.zones, key=lambda item: item.zone_id):
        source = zone.source
        now = next(item for item in zone.horizons if item.minutes_ahead == 0)
        future_120 = next(
            item for item in zone.horizons if item.minutes_ahead == 120
        )
        projections = {
            item.driver_id_hash: item for item in zone.drivers
        }
        preventive_ids = frozenset(
            driver_id
            for driver_id, driver in projections.items()
            if (
                source is not None
                and next(
                    item
                    for item in source.drivers
                    if item.driver_id_hash == driver_id
                ).continuous_exposure_minutes
                < MANDATORY_EXPOSURE_MINUTES
                and _driver_horizon(driver, 120).crossing_probability
                >= PROJECTED_MANDATORY_PROBABILITY
            )
        )
        predictions = _driver_predictions(city, zone)
        windows: list[InterventionWindow] = []
        unavailable_reason = ""
        if source is None or not predictions:
            evidence_unavailable = True
            unavailable_reason = "Snapshot-matched action evidence is unavailable."
        else:
            demand = tuple(item.median_requests for item in source.demand)
            upper = tuple(item.upper_requests for item in source.demand)
            for start in ACTION_DELAYS:
                result = recommend_ai_intervention(
                    source.zone,
                    predictions,
                    demand_by_interval=demand,
                    upper_demand_by_interval=upper,
                    budget_cap_vnd=constraints.budget_cap_vnd,
                    sponsor_per_driver_vnd=constraints.sponsor_per_driver_vnd,
                    candidate_start_delays=(start,),
                    preventive_ids=preventive_ids,
                )
                if result.recommended is not None:
                    windows.append(
                        _window_outcome(
                            city,
                            zone,
                            result.recommended,
                            seed=seed,
                        )
                    )
                elif result.status == "MODEL_UNAVAILABLE":
                    evidence_unavailable = True
                    unavailable_reason = result.message
        best_window = min(
            windows,
            key=lambda item: (
                -item.proposal.mandatory_selected_drivers,
                item.projected_mandatory_after_120m,
                item.residual_risk_120m,
                _demand_for_window(
                    source, item.start_delay_minutes
                ).median_requests
                if source is not None
                else math.inf,
                item.start_delay_minutes,
                -item.proposal.expected_risk_events_prevented,
                item.p95_reserved_cost_vnd,
            ),
            default=None,
        )
        drafts.append(
            {
                "zone": zone,
                "now": now,
                "future": future_120,
                "window": best_window,
                "unavailable_reason": unavailable_reason,
            }
        )

    severity_rank = _rank(
        [
            (
                draft["zone"].zone_id,
                (-draft["now"].baseline_expected_risk,),
            )
            for draft in drafts
        ]
    )
    future_rank = _rank(
        [
            (
                draft["zone"].zone_id,
                (
                    -draft["future"].projected_mandatory,
                    -draft["future"].expected_crossers,
                ),
            )
            for draft in drafts
        ]
    )
    opportunity_rank = _rank(
        [
            (
                draft["zone"].zone_id,
                (
                    -(
                        draft["window"].proposal.expected_risk_events_prevented
                        if draft["window"] is not None
                        else 0.0
                    ),
                ),
            )
            for draft in drafts
        ]
    )

    candidate_drafts = [draft for draft in drafts if draft["window"] is not None]
    total_mandatory = sum(draft["now"].mandatory_now for draft in drafts)
    portfolios: list[tuple[tuple[object, ...], int, tuple[int, ...]]] = []
    for mask in range(1 << len(candidate_drafts)):
        selected_indexes = tuple(
            index
            for index in range(len(candidate_drafts))
            if mask & (1 << index)
        )
        city_paths = tuple(
            sum(
                candidate_drafts[index]["window"].path_costs_vnd[path_index]
                for index in selected_indexes
            )
            for path_index in range(len(city.path_ids))
        )
        city_p95 = _nearest_rank_p95(city_paths)
        if city_p95 > constraints.budget_cap_vnd:
            continue
        selected_ids = tuple(
            candidate_drafts[index]["zone"].zone_id
            for index in selected_indexes
        )
        covered = sum(
            candidate_drafts[index]["window"].proposal.mandatory_selected_drivers
            for index in selected_indexes
        )
        projected_after = sum(
            (
                draft["window"].projected_mandatory_after_120m
                if draft["zone"].zone_id in selected_ids
                else draft["future"].expected_crossers
            )
            for draft in drafts
        )
        residual_by_zone = tuple(
            (
                draft["window"].residual_risk_120m
                if draft["zone"].zone_id in selected_ids
                else draft["future"].baseline_expected_risk
            )
            for draft in drafts
        )
        prevented = sum(
            candidate_drafts[index]["window"].proposal.expected_risk_events_prevented
            for index in selected_indexes
        )
        expected_cost = round(sum(city_paths) / len(city_paths))
        eta = sum(
            candidate_drafts[index]["window"].proposal.p90_eta_increase_minutes
            for index in selected_indexes
        )
        score = (
            -covered,
            round(projected_after, 6),
            round(max(residual_by_zone, default=0.0), 6),
            -round(prevented, 6),
            city_p95,
            expected_cost,
            round(eta, 6),
            selected_ids,
        )
        portfolios.append((score, mask, city_paths))
    _, selected_mask, selected_city_paths = min(portfolios, key=lambda item: item[0])
    selected_ids = tuple(
        candidate_drafts[index]["zone"].zone_id
        for index in range(len(candidate_drafts))
        if selected_mask & (1 << index)
    )
    mandatory_covered = sum(
        draft["window"].proposal.mandatory_selected_drivers
        for draft in candidate_drafts
        if draft["zone"].zone_id in selected_ids
    )

    rows: list[PredictiveZonePlanRow] = []
    for draft in drafts:
        zone = draft["zone"]
        window = draft["window"]
        if zone.zone_id in selected_ids:
            portfolio_status = "SELECTED"
            portfolio_reason = (
                "Selected by safety-first city optimization within the aligned-path "
                "P95 cost cap."
            )
        elif window is not None:
            portfolio_status = "DEFERRED"
            portfolio_reason = (
                "Feasible district action deferred by city safety priorities and "
                "the shared P95 cost cap."
            )
        elif draft["unavailable_reason"]:
            portfolio_status = "UNAVAILABLE"
            portfolio_reason = draft["unavailable_reason"]
        else:
            portfolio_status = "NO_ACTION"
            portfolio_reason = (
                "No district proposal satisfies the existing cost, fulfillment "
                "and ETA guardrails."
            )
        rows.append(
            PredictiveZonePlanRow(
                zone_id=zone.zone_id,
                zone_name=zone.zone_name,
                horizons=zone.horizons,
                current_raw_risk=draft["now"].baseline_expected_risk,
                expected_risk_prevented=(
                    window.proposal.expected_risk_events_prevented
                    if window is not None
                    else 0.0
                ),
                best_window=window,
                preventive_pauses=(
                    window.proposal.selected_drivers if window is not None else 0
                ),
                severity_rank=severity_rank[zone.zone_id],
                future_safety_rank=future_rank[zone.zone_id],
                opportunity_rank=opportunity_rank[zone.zone_id],
                portfolio_status=portfolio_status,
                portfolio_reason=portfolio_reason,
                path_costs_vnd=(
                    window.path_costs_vnd
                    if window is not None
                    else tuple(0 for _ in city.path_ids)
                ),
            )
        )

    status = (
        "EVIDENCE_UNAVAILABLE"
        if evidence_unavailable
        else (
            "SAFETY_CAPACITY_BREACH"
            if mandatory_covered < total_mandatory
            else "READY"
        )
    )
    fingerprint = "|".join(
        (
            city.lineage.mode,
            city.lineage.snapshot_id,
            str(constraints.budget_cap_vnd),
            ",".join(selected_ids),
            ",".join(
                row.best_window.proposal.proposal_id
                for row in rows
                if row.zone_id in selected_ids and row.best_window is not None
            ),
        )
    )
    portfolio_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
    created_at = datetime.now(UTC)
    return PredictiveCityPlan(
        portfolio_id=portfolio_id,
        mode=city.lineage.mode,
        rows=tuple(rows),
        selected_zone_ids=selected_ids,
        expected_cost_vnd=round(
            sum(selected_city_paths) / len(selected_city_paths)
        ),
        p95_reserved_cost_vnd=_nearest_rank_p95(selected_city_paths),
        budget_cap_vnd=constraints.budget_cap_vnd,
        status=status,
        evidence_lineage=city.lineage,
        forecast_version=city.projection_version,
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=15),
        mandatory_now_covered=mandatory_covered,
        mandatory_now_uncovered=max(0, total_mandatory - mandatory_covered),
    )


__all__ = [
    "FORECAST_HORIZONS",
    "FORECAST_PATH_COUNT",
    "ForecastInputError",
    "HANOI_MICROCLIMATE_OFFSETS_C",
    "MICROCLIMATE_MODEL_VERSION",
    "PROJECTED_MANDATORY_PROBABILITY",
    "PROJECTED_RISK_VERSION",
    "PROJECTION_VERSION",
    "ProjectedRiskScorerV1",
    "build_accelerated_forecast_input",
    "build_current_forecast_input",
    "build_predictive_city_plan",
    "project_city_forecast",
]
