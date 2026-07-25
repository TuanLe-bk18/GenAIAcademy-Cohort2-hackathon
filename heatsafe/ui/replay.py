from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from heatsafe.simulation.scenario import load_scenario

HANOI_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class ReplayRunLabelSource(Protocol):
    scenario_version: str
    simulation_start_at: datetime
    status: str
    simulation_run_id: str


def replay_scenario_start(run: ReplayRunLabelSource) -> datetime:
    """Resolve the authoritative replay epoch in Hanoi time."""
    try:
        replay_start = load_scenario(
            run.scenario_version
        ).weather[0]["local_time"]
    except (OSError, ValueError):
        replay_start = run.simulation_start_at.astimezone(HANOI_TZ)
    return replay_start.astimezone(HANOI_TZ)


def replay_tick_time(
    run: ReplayRunLabelSource, tick_index: int
) -> datetime:
    if not 0 <= tick_index <= 95:
        raise ValueError("tick_index must be in 0..95")
    return replay_scenario_start(run) + timedelta(minutes=tick_index * 15)


def replay_run_label(run: ReplayRunLabelSource) -> str:
    """Describe scenario time, never the wall-clock run creation time."""
    replay_start = replay_scenario_start(run)
    return (
        f"{replay_start:%d %b %H:%M} ICT · "
        f"{run.status.lower()} · {run.simulation_run_id[:8]}"
    )
