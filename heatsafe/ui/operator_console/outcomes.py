"""Pure adapters for observed SafePause versus no-action simulation outcomes."""

from __future__ import annotations

from collections.abc import Sequence

from heatsafe.simulation.models import TickResult

from .view_models import OperatorOutcomePoint, OperatorOutcomeView


def _unrecovered_exposure_hours(result: TickResult) -> float:
    """Sum roster-wide continuous exposure so status changes cannot fake recovery."""
    return round(
        sum(
            max(0, driver.continuous_exposure_minutes)
            for driver in result.state.drivers
        )
        / 60.0,
        3,
    )


def build_safepause_outcome_view(
    with_safepause: Sequence[TickResult],
    without_safepause: Sequence[TickResult],
    *,
    decision_tick: int,
) -> OperatorOutcomeView | None:
    """Build a comparable observed outcome only from aligned activated branches.

    The caller must pass the activated branch first and the deterministic no-action
    branch second. Forecast projections and proposal probabilities are intentionally
    excluded from this observed history.
    """
    actual = tuple(with_safepause)
    baseline = tuple(without_safepause)
    if len(actual) != len(baseline) or len(actual) < 2:
        return None
    if not any(item.tick_index == decision_tick for item in actual):
        return None

    points: list[OperatorOutcomePoint] = []
    previous_tick: int | None = None
    for active, no_action in zip(actual, baseline, strict=True):
        if (
            active.tick_index != no_action.tick_index
            or active.simulation_time != no_action.simulation_time
            or (previous_tick is not None and active.tick_index <= previous_tick)
        ):
            return None
        previous_tick = active.tick_index
        points.append(
            OperatorOutcomePoint(
                at=active.simulation_time,
                with_safepause=_unrecovered_exposure_hours(active),
                without_safepause=_unrecovered_exposure_hours(no_action),
            )
        )

    post_decision = [
        point
        for point, result in zip(points, actual, strict=True)
        if result.tick_index > decision_tick
    ]
    if not post_decision:
        return None
    final = post_decision[-1]
    avoided = max(0.0, final.without_safepause - final.with_safepause)
    return OperatorOutcomeView(
        points=tuple(points),
        metric_label="Unrecovered heat exposure",
        unit="driver-hours",
        summary=f"SafePause avoided {avoided:,.1f} unrecovered driver-hours by the latest interval.",
    )


__all__ = ["build_safepause_outcome_view"]
