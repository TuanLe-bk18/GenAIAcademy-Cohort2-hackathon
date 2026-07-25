"""Pure Phase 5R execution and forecast cadence policy."""

from __future__ import annotations

from dataclasses import dataclass


_TIERS = {
    "NORMAL": 0,
    "CAUTION": 1,
    "EXTREME_CAUTION": 2,
    "DANGER": 3,
    "EXTREME_DANGER": 4,
}


@dataclass(frozen=True, slots=True)
class TickExecutionInputs:
    current_tier: str
    lookahead_tier: str
    exposed_2h: int
    exposed_4h: int
    pending_controls: int = 0
    active_interventions: int = 0
    previous_mode: str = "FULL"
    low_risk_streak: int = 0
    recovery_streak: int = 0
    persisted_scoring_failure: bool = False
    demand_anomaly: bool = False
    danger_prewarm: bool = False
    forecast_available: bool = False
    forecast_expired: bool = False
    forecast_failed: bool = False
    full_ticks_since_generation: int = 0


@dataclass(frozen=True, slots=True)
class TickExecutionPlan:
    mode: str
    reason_codes: tuple[str, ...]
    next_low_risk_streak: int
    next_recovery_streak: int
    project_features: bool
    run_ml_inference: bool
    generate_forecast: bool
    reuse_forecast: bool


def plan_tick_execution(inputs: TickExecutionInputs) -> TickExecutionPlan:
    if inputs.current_tier not in _TIERS or inputs.lookahead_tier not in _TIERS:
        raise ValueError("execution policy received an unknown heat tier")
    if inputs.previous_mode not in {"FULL", "RECOVERY", "MONITOR"}:
        raise ValueError("execution policy received an unknown previous mode")
    if min(
        inputs.exposed_2h,
        inputs.exposed_4h,
        inputs.pending_controls,
        inputs.active_interventions,
        inputs.low_risk_streak,
        inputs.recovery_streak,
        inputs.full_ticks_since_generation,
    ) < 0:
        raise ValueError("execution policy counts must be non-negative")

    reasons: set[str] = set()
    if inputs.persisted_scoring_failure and inputs.previous_mode == "FULL":
        reasons.add("PERSISTED_FULL_RETRY")
    if inputs.pending_controls:
        reasons.add("CONTROL_PENDING")
    if inputs.active_interventions:
        reasons.add("INTERVENTION_ACTIVE")
    if _TIERS[inputs.current_tier] >= _TIERS["EXTREME_CAUTION"]:
        reasons.add("CURRENT_HEAT_TIER")
    if _TIERS[inputs.lookahead_tier] >= _TIERS["EXTREME_CAUTION"]:
        reasons.add("LOOKAHEAD_HEAT_TIER")
    if inputs.exposed_4h:
        reasons.add("EXPOSED_4H")
    if inputs.demand_anomaly:
        reasons.add("DEMAND_ANOMALY")
    if inputs.danger_prewarm:
        reasons.add("DANGER_PREWARM")

    full_trigger = bool(reasons)
    if full_trigger:
        mode = "FULL"
        low_risk_streak = 0
        recovery_streak = 0
    elif inputs.previous_mode == "FULL" and inputs.low_risk_streak < 2:
        mode = "FULL"
        reasons.add("FULL_EXIT_HYSTERESIS")
        low_risk_streak = inputs.low_risk_streak + 1
        recovery_streak = 0
    elif inputs.previous_mode == "FULL":
        mode = "RECOVERY"
        reasons.add("RECOVERY_COOLDOWN")
        low_risk_streak = inputs.low_risk_streak
        recovery_streak = 1
    elif inputs.previous_mode == "RECOVERY" and inputs.recovery_streak < 2:
        mode = "RECOVERY"
        reasons.add("RECOVERY_COOLDOWN")
        low_risk_streak = inputs.low_risk_streak + 1
        recovery_streak = inputs.recovery_streak + 1
    else:
        mode = "MONITOR"
        reasons.add("LOW_RISK_STABLE")
        low_risk_streak = inputs.low_risk_streak + 1
        recovery_streak = inputs.recovery_streak

    force_forecast = (
        mode == "FULL"
        and (
            not inputs.forecast_available
            or inputs.forecast_expired
            or inputs.forecast_failed
            or inputs.demand_anomaly
            or inputs.previous_mode != "FULL"
        )
    )
    cadence_forecast = (
        mode == "FULL" and inputs.full_ticks_since_generation >= 4
    )
    generate_forecast = force_forecast or cadence_forecast
    reuse_forecast = (
        not generate_forecast
        and inputs.forecast_available
        and not inputs.forecast_expired
        and not inputs.forecast_failed
    )
    return TickExecutionPlan(
        mode=mode,
        reason_codes=tuple(sorted(reasons)),
        next_low_risk_streak=low_risk_streak,
        next_recovery_streak=recovery_streak,
        project_features=True,
        run_ml_inference=mode == "FULL",
        generate_forecast=generate_forecast,
        reuse_forecast=reuse_forecast,
    )
