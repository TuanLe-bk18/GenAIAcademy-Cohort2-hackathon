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
    "NORMAL": "Bình thường",
    "CAUTION": "Cần chú ý",
    "EXTREME_CAUTION": "Cảnh giác cao",
    "DANGER": "Nguy hiểm",
    "EXTREME_DANGER": "Nguy hiểm cực độ",
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
    duration_points = 20 if zone.exposed_4h > 0 else 10 if zone.exposed_2h > 0 else 0
    return min(100, tier_points + duration_points)


def priority_label(score: int) -> str:
    if score >= 70:
        return "Can thiệp ngay"
    if score >= 50:
        return "Lên lịch nghỉ"
    if score >= 30:
        return "Theo dõi sát"
    return "Theo dõi"


def eligible_driver_count(zone: ZoneSnapshot) -> int:
    tier = heat_tier(zone.heat_index_c)
    if tier in {"DANGER", "EXTREME_DANGER"}:
        return zone.exposed_2h
    if tier == "EXTREME_CAUTION":
        return zone.exposed_4h
    return 0
