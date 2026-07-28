"""Operator-facing words, time, and number formatting.

This module is deliberately independent of Streamlit so presentation contracts can be
validated without starting an app runtime.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from heatsafe.currency import vnd_to_usd
from heatsafe.risk import TIER_LABELS, heat_tier

HANOI_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
FORBIDDEN_OPERATOR_TERMS = (
    "tick",
    "K=",
    "snapshot",
    "checksum",
    "shadow",
    "P95",
)

_MODE_LABELS = {
    "CURRENT": "Current plan",
    "PRODUCTION": "Current plan",
    "ACCELERATED": "Simulation playback",
    "ACCELERATED-PRODUCTION": "Simulation playback",
}

_READINESS_LABELS = {
    "READY": "Ready",
    "EVIDENCE_UNAVAILABLE": "Monitoring only",
    "MODEL_UNAVAILABLE": "Monitoring only",
    "SAFETY_CAPACITY_BREACH": "Decision needed",
    "NO_FEASIBLE": "Decision needed",
    "RUNNING": "Running",
    "COMPLETED": "Complete",
    "COMPLETE": "Complete",
    "AWAITING_DECISION": "Decision needed",
}

_PLAN_STATUS_LABELS = {
    "SELECTED": "Included",
    "INCLUDED": "Included",
    "DEFERRED": "Watch",
    "NO_ACTION": "Watch",
    "UNAVAILABLE": "Data unavailable",
    "EVIDENCE_UNAVAILABLE": "Data unavailable",
}


def as_hanoi_time(value: datetime) -> datetime:
    """Return an aware Hanoi datetime; naive inputs are treated as UTC evidence."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(HANOI_TZ)


def format_hanoi_time(value: datetime | None, *, include_date: bool = False) -> str:
    """Format one operational time using the console clock contract."""
    if value is None:
        return "—"
    local = as_hanoi_time(value)
    return local.strftime("%d %b · %H:%M" if include_date else "%H:%M").lstrip("0")


def format_hanoi_range(start: datetime, end: datetime) -> str:
    """Format a clock range, adding dates only when it crosses a local day."""
    local_start = as_hanoi_time(start)
    local_end = as_hanoi_time(end)
    if local_start.date() == local_end.date():
        return f"{local_start:%H:%M}–{local_end:%H:%M}"
    return (
        f"{local_start.strftime('%d %b · %H:%M').lstrip('0')}–"
        f"{local_end.strftime('%d %b · %H:%M').lstrip('0')}"
    )


def format_hanoi_after(value: datetime, minutes: int) -> str:
    """Format the clock endpoint after a non-negative operational duration."""
    return format_hanoi_time(value + timedelta(minutes=max(0, int(minutes))))


def format_duration(minutes: float) -> str:
    value = max(0, round(float(minutes)))
    if value < 60:
        return f"{value} min"
    hours, remainder = divmod(value, 60)
    if remainder == 0:
        return f"{hours} hr" if hours == 1 else f"{hours} hrs"
    return f"{hours} hr {remainder} min"


def format_currency_vnd(value_vnd: float | None, *, decimals: bool = False) -> str:
    if value_vnd is None:
        return "—"
    value = vnd_to_usd(value_vnd)
    return f"${value:,.2f}" if decimals else f"${value:,.0f}"


def format_mode_label(mode: str) -> str:
    normalized = str(mode).strip().replace("_", "-").upper()
    return _MODE_LABELS.get(normalized, "Current plan")


def format_readiness_label(status: str | None) -> str:
    if not status:
        return "Monitoring only"
    return _READINESS_LABELS.get(str(status).upper(), "Monitoring only")


def format_plan_status_label(status: str | None) -> str:
    if not status:
        return "Data unavailable"
    return _PLAN_STATUS_LABELS.get(str(status).upper(), "Watch")


def format_heat_state(heat_index_c: float) -> str:
    """Return sentence-case heat state text backed by the existing screening tiers."""
    label = TIER_LABELS[heat_tier(float(heat_index_c))]
    return label.replace("Caution", "caution").replace("Danger", "danger")


def format_freshness(updated_at: datetime | None, now: datetime | None = None) -> str:
    if updated_at is None:
        return "Update time unavailable"
    now_value = now or datetime.now(UTC)
    elapsed = max(0, int((as_hanoi_time(now_value) - as_hanoi_time(updated_at)).total_seconds()))
    if elapsed < 60:
        return "Updated just now"
    minutes = elapsed // 60
    if minutes < 60:
        return f"Updated {minutes} min ago"
    hours = minutes // 60
    return f"Updated {hours} hr ago" if hours == 1 else f"Updated {hours} hrs ago"


def format_risk_level(value: float) -> str:
    probability = max(0.0, min(1.0, float(value)))
    if probability >= 0.70:
        return "High"
    if probability >= 0.40:
        return "Elevated"
    return "Moderate"


def operator_copy_violations(*values: object) -> tuple[str, ...]:
    """Return forbidden terms found in recursively supplied operator copy."""
    text = " ".join(_flatten_copy(values))
    found: list[str] = []
    for term in FORBIDDEN_OPERATOR_TERMS:
        pattern = re.escape(term)
        if term == "tick":
            pattern = r"\btick(?:s)?\b"
        if re.search(pattern, text, flags=re.IGNORECASE):
            found.append(term)
    return tuple(found)


def _flatten_copy(values: object) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return [values]
    if isinstance(values, dict):
        return [part for item in values.items() for part in _flatten_copy(item)]
    if isinstance(values, (tuple, list, set, frozenset)):
        return [part for item in values for part in _flatten_copy(item)]
    return [str(values)]


__all__ = [
    "FORBIDDEN_OPERATOR_TERMS",
    "HANOI_TZ",
    "as_hanoi_time",
    "format_currency_vnd",
    "format_duration",
    "format_freshness",
    "format_hanoi_after",
    "format_hanoi_range",
    "format_hanoi_time",
    "format_heat_state",
    "format_mode_label",
    "format_plan_status_label",
    "format_readiness_label",
    "format_risk_level",
    "operator_copy_violations",
]
