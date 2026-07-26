"""Shared, Hanoi-local synthetic demand shape for snapshot and simulation paths.

The profile is deliberately documented as a synthetic operational prior.  It
encodes relationships the demo must preserve (night trough, commute/lunch/
dinner lifts) while keeping zone-level variation deterministic.
"""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo


HANOI_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


DEMAND_POINTS = (
    (0.0, 0.14),
    (4.0, 0.10),
    (6.0, 0.24),
    (8.0, 0.78),
    (10.0, 0.56),
    (12.25, 0.72),
    (15.0, 0.54),
    (18.5, 1.08),
    (21.0, 0.56),
    (24.0, 0.14),
)


def intraday_demand_factor(forecast_at: datetime, zone_seed: int) -> float:
    """Return a smooth, bounded local-time profile with modest zone variation."""
    local = forecast_at.astimezone(HANOI_TZ)
    hour = local.hour + local.minute / 60
    weekend = local.weekday() >= 5
    shifted_hour = hour - (0.65 if weekend else 0.0)
    if weekend and hour >= 20:
        shifted_hour = hour - 0.25
    shifted_hour = max(0.0, min(24.0, shifted_hour))
    for (left_hour, left), (right_hour, right) in zip(
        DEMAND_POINTS, DEMAND_POINTS[1:]
    ):
        if left_hour <= shifted_hour <= right_hour:
            fraction = (shifted_hour - left_hour) / (right_hour - left_hour)
            baseline = left + (right - left) * fraction
            break
    else:  # pragma: no cover - bounded above, retained as a safe fallback.
        baseline = DEMAND_POINTS[-1][1]
    phase = math.radians(zone_seed % 360)
    variation = 1.0 + 0.025 * math.sin(2 * math.pi * hour / 2.0 + phase)
    return max(0.08, baseline * variation)
