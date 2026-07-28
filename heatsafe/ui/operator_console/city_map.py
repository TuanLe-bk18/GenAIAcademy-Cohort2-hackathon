"""Primary Hanoi map and accessible priority-area selection."""

from __future__ import annotations

from typing import Any, cast

import pandas as pd
import pydeck as pdk
import streamlit as st

from .view_models import OperatorAreaView

_SELECTION_RGBA = [67, 182, 110, 255]
_LIGHT_OUTLINE_RGBA = [114, 128, 121, 150]
_DARK_OUTLINE_RGBA = [232, 241, 234, 110]


def _heat_color(heat_index_c: float) -> list[int]:
    if heat_index_c >= 52:
        return [180, 35, 24, 225]
    if heat_index_c >= 39:
        return [217, 74, 58, 220]
    if heat_index_c >= 32:
        return [230, 126, 50, 215]
    if heat_index_c >= 27:
        return [198, 151, 0, 205]
    return [114, 128, 121, 190]


def map_records(
    areas: tuple[OperatorAreaView, ...],
    *,
    theme_type: str = "dark",
) -> list[dict[str, object]]:
    neutral_outline = (
        _LIGHT_OUTLINE_RGBA if theme_type == "light" else _DARK_OUTLINE_RGBA
    )
    return [
        {
            "zone_id": area.zone_id,
            "area": area.name,
            "lat": area.latitude,
            "lon": area.longitude,
            "heat": f"{area.heat_state_label} · {area.heat_index_c:.1f}°C",
            "drivers": area.drivers_needing_break_now,
            "plan_status": area.plan_status_label,
            "radius": 760 + min(900, area.active_drivers * 2),
            "fill_color": _heat_color(area.heat_index_c),
            "line_color": (
                _SELECTION_RGBA
                if area.selected or area.included_in_plan
                else neutral_outline
            ),
            "line_width": 6 if area.selected else 3 if area.included_in_plan else 1,
        }
        for area in areas
    ]


def _selected_objects(event: object) -> list[dict[str, object]]:
    if isinstance(event, dict):
        return (
            event.get("selection", {})
            .get("objects", {})
            .get("operator-areas", [])
        )
    selection = getattr(event, "selection", None)
    objects = getattr(selection, "objects", None)
    if isinstance(objects, dict):
        return list(objects.get("operator-areas", []))
    return []


def render_city_map(
    areas: tuple[OperatorAreaView, ...],
    priority_areas: tuple[OperatorAreaView, ...],
    *,
    key_prefix: str = "operator-map",
) -> str | None:
    """Render the primary map and return an area selection intent, if any."""
    st.subheader("Hanoi heat map")
    theme_type = st.context.theme.type or "dark"
    records = map_records(areas, theme_type=theme_type)
    is_light = theme_type == "light"
    selected_zone_id: str | None = None
    if not records:
        st.info("Area monitoring is temporarily unavailable.")
        return None
    event = st.pydeck_chart(
        pdk.Deck(
            layers=[
                pdk.Layer(
                    "ScatterplotLayer",
                    pd.DataFrame(records),
                    id="operator-areas",
                    get_position=["lon", "lat"],
                    get_fill_color="fill_color",
                    get_radius="radius",
                    pickable=True,
                    stroked=True,
                    get_line_color="line_color",
                    get_line_width="line_width",
                    line_width_min_pixels=1,
                )
            ],
            initial_view_state=pdk.ViewState(
                latitude=21.025,
                longitude=105.81,
                zoom=10.15,
                pitch=28,
            ),
            map_style=(
                "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
                if is_light
                else "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
            ),
            tooltip=cast(
                Any,
                {
                    "html": (
                        "<b>{area}</b><br/>Heat: {heat}<br/>"
                        "Drivers needing a break: {drivers}<br/>"
                        "Plan status: {plan_status}"
                    ),
                    "style": {
                        "backgroundColor": "#ffffff" if is_light else "#102018",
                        "color": "#26332c" if is_light else "#f2f7f3",
                        "border": (
                            "1px solid #d9deda"
                            if is_light
                            else "1px solid #2d4838"
                        ),
                    },
                },
            ),
        ),
        height=430,
        key=f"{key_prefix}:chart",
        on_select="rerun",
        selection_mode="single-object",
    )
    selected_objects = _selected_objects(event)
    valid_ids = {area.zone_id for area in areas}
    if selected_objects:
        candidate = str(selected_objects[0].get("zone_id", ""))
        if candidate in valid_ids:
            selected_zone_id = candidate

    st.markdown("**Priority areas**")
    for index, area in enumerate(priority_areas[:3], start=1):
        if st.button(
            f"{index}. {area.name} · {area.heat_state_label} · {area.plan_status_label}",
            key=f"{key_prefix}:priority:{area.zone_id}",
            width="stretch",
        ):
            selected_zone_id = area.zone_id
    st.caption(
        "Green outlines identify the selected area and areas included in the current plan."
    )
    return selected_zone_id


__all__ = ["map_records", "render_city_map"]
