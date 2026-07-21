from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import streamlit as st

from heatsafe.currency import usd_to_vnd
from heatsafe.models import DecisionConstraints, ZoneSnapshot

DEFAULT_BUDGET_USD = 120.0
DEFAULT_SPONSOR_USD = 0.32
DEFAULT_HORIZON_MINUTES = 240


def initialize_state(
    scenario: str,
    snapshot_id: str,
    zones: Sequence[ZoneSnapshot],
    *,
    ordered_zones: Sequence[ZoneSnapshot] | None = None,
) -> ZoneSnapshot | None:
    """Initialize UI controls and return the selected zone for this snapshot."""
    st.session_state.setdefault("decision_budget_cap", DEFAULT_BUDGET_USD)
    st.session_state.setdefault("decision_partner_credit", DEFAULT_SPONSOR_USD)
    st.session_state.setdefault("refresh_token", uuid4().hex)

    if not zones:
        st.session_state.zone_selection_context = f"{scenario}:{snapshot_id}"
        st.session_state.selected_zone_id = None
        return None

    valid_zone_ids = {zone.zone_id for zone in zones}
    context = f"{scenario}:{snapshot_id}"
    candidates = ordered_zones or zones
    default_zone = next(
        (zone for zone in candidates if zone.zone_id in valid_zone_ids), zones[0]
    )
    if (
        st.session_state.get("zone_selection_context") != context
        or st.session_state.get("selected_zone_id") not in valid_zone_ids
    ):
        st.session_state.zone_selection_context = context
        st.session_state.selected_zone_id = default_zone.zone_id

    selected_id = st.session_state.selected_zone_id
    return next(zone for zone in zones if zone.zone_id == selected_id)


def build_constraints(
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
) -> DecisionConstraints:
    """Build normalized domain constraints from the current USD UI controls."""
    budget_usd = float(
        st.session_state.get("decision_budget_cap", DEFAULT_BUDGET_USD)
    )
    sponsor_usd = float(
        st.session_state.get("decision_partner_credit", DEFAULT_SPONSOR_USD)
    )
    return DecisionConstraints(
        horizon_minutes=horizon_minutes,
        budget_cap_vnd=usd_to_vnd(budget_usd),
        sponsor_per_driver_vnd=usd_to_vnd(sponsor_usd),
    )


def advance_refresh_token() -> str:
    """Create a session-specific cache generation without clearing shared caches."""
    token = uuid4().hex
    st.session_state.refresh_token = token
    return token


__all__ = [
    "DEFAULT_BUDGET_USD",
    "DEFAULT_HORIZON_MINUTES",
    "DEFAULT_SPONSOR_USD",
    "advance_refresh_token",
    "build_constraints",
    "initialize_state",
]
