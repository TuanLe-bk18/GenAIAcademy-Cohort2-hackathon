from __future__ import annotations

from .models import ZoneSnapshot

HEAT_TIERS = (
    "NORMAL",
    "CAUTION",
    "EXTREME_CAUTION",
    "DANGER",
    "EXTREME_DANGER",
)

TIER_LABELS = {
    "NORMAL": "Normal",
    "CAUTION": "Caution",
    "EXTREME_CAUTION": "Extreme Caution",
    "DANGER": "Danger",
    "EXTREME_DANGER": "Extreme Danger",
}

TIER_COLORS = {
    "NORMAL": [47, 158, 68, 180],
    "CAUTION": [245, 197, 24, 190],
    "EXTREME_CAUTION": [255, 140, 0, 200],
    "DANGER": [235, 72, 52, 220],
    "EXTREME_DANGER": [143, 24, 55, 235],
}


def heat_tier(heat_index_c: float) -> str:
    """Heat Index screening tier; this is not a medical diagnosis."""
    if heat_index_c < 27:
        return "NORMAL"
    if heat_index_c < 32:
        return "CAUTION"
    if heat_index_c < 39:
        return "EXTREME_CAUTION"
    if heat_index_c < 52:
        return "DANGER"
    return "EXTREME_DANGER"


def operational_priority(zone: ZoneSnapshot) -> int:
    """Prioritize operations using environmental severity and exposure duration."""
    tier_points = HEAT_TIERS.index(heat_tier(zone.heat_index_c)) * 20
    active = max(zone.active_drivers, 1)
    exposed_2h_share = max(0.0, min(1.0, zone.exposed_2h / active))
    exposed_4h_share = max(0.0, min(exposed_2h_share, zone.exposed_4h / active))
    duration_points = min(20, round(40 * exposed_2h_share + 80 * exposed_4h_share))
    return min(100, tier_points + duration_points)


def priority_label(score: int) -> str:
    if score >= 90:
        return "Intervene immediately"
    if score >= 70:
        return "Schedule pause"
    if score >= 50:
        return "Monitor closely"
    return "Monitor"


def eligible_driver_cohorts(zone: ZoneSnapshot) -> tuple[int, int]:
    """Return high (4h+) and medium (2-4h) policy cohorts."""
    tier = heat_tier(zone.heat_index_c)
    if tier in {"DANGER", "EXTREME_DANGER"}:
        high = min(zone.exposed_4h, zone.exposed_2h)
        return high, max(0, zone.exposed_2h - high)
    if tier == "EXTREME_CAUTION":
        return zone.exposed_4h, 0
    return 0, 0


def eligible_driver_count(zone: ZoneSnapshot) -> int:
    high, medium = eligible_driver_cohorts(zone)
    return high + medium
