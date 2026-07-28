"""Multi-metric Hanoi district choropleth and accessible area selection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pandas as pd
import pydeck as pdk
import streamlit as st

from .geography import load_hanoi_operator_districts
from .view_models import OperatorAreaView

MAP_METRICS = {
    "Heat index": ("heat_index_c", "Heat index", "°C"),
    "Need a break now": ("drivers_needing_break_now", "Drivers needing a break", "drivers"),
    "2h exposure": ("exposed_2h", "Drivers with 2h exposure", "drivers"),
    "Forecast demand": ("forecast_requests_30m", "Forecast requests", "requests / 30 min"),
    "Needs protection by 120m": (
        "expected_needing_protection_count",
        "Projected drivers needing protection",
        "drivers",
    ),
    "SafePause status": ("safepause_status", "SafePause plan status", ""),
}

_SELECTION_RGBA = [77, 167, 179, 255]
_INCLUDED_RGBA = [240, 163, 90, 255]
_LIGHT_OUTLINE_RGBA = [76, 92, 88, 150]
_DARK_OUTLINE_RGBA = [232, 241, 234, 145]
_MISSING_RGBA = [114, 128, 121, 115]


def _sequential_color(value: float, *, lower: float, upper: float) -> list[int]:
    """Return a perceptually ordered cool-to-hot color without changing data."""
    if upper <= lower:
        fraction = 0.5
    else:
        fraction = min(1.0, max(0.0, (value - lower) / (upper - lower)))
    if fraction < 0.5:
        blend = fraction * 2
        start, end = (77, 167, 179), (240, 163, 90)
    else:
        blend = (fraction - 0.5) * 2
        start, end = (240, 163, 90), (239, 106, 91)
    return [round(start[index] + (end[index] - start[index]) * blend) for index in range(3)] + [215]


def _metric_value(area: OperatorAreaView, metric: str) -> tuple[float | None, str]:
    field, _, unit = MAP_METRICS[metric]
    if field == "safepause_status":
        if area.included_in_plan:
            return 2.0, "Included in the SafePause plan"
        if area.plan_status_label == "Data unavailable":
            return None, "Plan status is updating"
        return 1.0, "Monitoring only"
    value = getattr(area, field)
    if value is None:
        return None, "Updating"
    numeric = float(value)
    if field == "heat_index_c":
        return numeric, f"{numeric:.1f}°C"
    return numeric, f"{numeric:,.0f} {unit}"


def _metric_color(value: float | None, *, metric: str, lower: float, upper: float) -> list[int]:
    if value is None:
        return _MISSING_RGBA
    if metric == "SafePause status":
        return _INCLUDED_RGBA if value >= 2 else [77, 167, 179, 185]
    return _sequential_color(value, lower=lower, upper=upper)


def map_records(
    areas: tuple[OperatorAreaView, ...],
    *,
    metric: str = "Heat index",
    theme_type: str = "dark",
) -> list[dict[str, object]]:
    """Expose only already-authoritative area values to the map renderer."""
    if metric not in MAP_METRICS:
        raise ValueError(f"unknown map metric: {metric}")
    neutral_outline = _LIGHT_OUTLINE_RGBA if theme_type == "light" else _DARK_OUTLINE_RGBA
    values = [_metric_value(area, metric)[0] for area in areas]
    numeric = [value for value in values if value is not None]
    lower, upper = (min(numeric), max(numeric)) if numeric else (0.0, 1.0)
    records: list[dict[str, object]] = []
    for area, value in zip(areas, values, strict=True):
        metric_value, metric_label = _metric_value(area, metric)
        line_color = (
            _SELECTION_RGBA
            if area.selected
            else _INCLUDED_RGBA if area.included_in_plan else neutral_outline
        )
        records.append(
            {
                "zone_id": area.zone_id,
                "area": area.name,
                "lat": area.latitude,
                "lon": area.longitude,
                "metric_label": metric_label,
                "drivers": area.drivers_needing_break_now,
                "plan_status": area.plan_status_label,
                "radius": 760 + min(900, area.active_drivers * 2),
                "fill_color": _metric_color(
                    metric_value, metric=metric, lower=lower, upper=upper
                ),
                "line_color": line_color,
                "line_width": 5 if area.selected else 3 if area.included_in_plan else 1,
            }
        )
    return records


def district_geojson(records: list[dict[str, object]]) -> dict[str, Any]:
    """Join visual tokens to fixed geometry without re-ranking any area."""
    source = deepcopy(load_hanoi_operator_districts())
    record_by_id = {str(record["zone_id"]): record for record in records}
    features = []
    for feature in source["features"]:
        properties = feature["properties"]
        record = record_by_id.get(str(properties["zone_id"]))
        if record is None:
            continue
        properties.update(record)
        features.append(feature)
    source["features"] = features
    return source


def _selected_objects(event: object) -> list[dict[str, object]]:
    if isinstance(event, dict):
        return event.get("selection", {}).get("objects", {}).get("operator-districts", [])
    selection = getattr(event, "selection", None)
    objects = getattr(selection, "objects", None)
    if isinstance(objects, dict):
        return list(objects.get("operator-districts", []))
    return []


def _tooltip(metric_label: str, *, is_light: bool) -> dict[str, object]:
    return {
        "html": (
            "<b>{area}</b><br/>"
            f"{metric_label}: {{metric_label}}<br/>"
            "Drivers needing a break: {drivers}<br/>Plan status: {plan_status}"
        ),
        "style": {
            "backgroundColor": "#ffffff" if is_light else "#121d1d",
            "color": "#15201d" if is_light else "#f4f7f6",
            "border": "1px solid #cfd8d5" if is_light else "1px solid #35524d",
        },
    }


def render_city_map(
    areas: tuple[OperatorAreaView, ...],
    priority_areas: tuple[OperatorAreaView, ...],
    *,
    key_prefix: str = "operator-map",
) -> str | None:
    """Render a selectable, truth-preserving district choropleth."""
    st.subheader("Hanoi operating areas")
    metric = st.segmented_control(
        "Map layer",
        tuple(MAP_METRICS),
        default="Heat index",
        key=f"{key_prefix}:metric",
    )
    active_metric = metric if metric in MAP_METRICS else "Heat index"
    theme_type = st.context.theme.type or "dark"
    records = map_records(areas, metric=active_metric, theme_type=theme_type)
    is_light = theme_type == "light"
    selected_zone_id: str | None = None
    if not records:
        st.info("Area monitoring is temporarily unavailable.")
        return None

    geometry = district_geojson(records)
    event = st.pydeck_chart(
        pdk.Deck(
            layers=[
                pdk.Layer(
                    "GeoJsonLayer",
                    geometry,
                    id="operator-districts",
                    pickable=True,
                    filled=True,
                    stroked=True,
                    get_fill_color="properties.fill_color",
                    get_line_color="properties.line_color",
                    get_line_width="properties.line_width",
                    line_width_units="pixels",
                    line_width_min_pixels=2,
                    auto_highlight=True,
                    highlight_color=[77, 167, 179, 95],
                ),
                pdk.Layer(
                    "ScatterplotLayer",
                    pd.DataFrame(records),
                    id="operator-district-labels",
                    get_position=["lon", "lat"],
                    get_fill_color=[244, 247, 246, 0],
                    get_radius=1,
                    pickable=False,
                ),
            ],
            initial_view_state=pdk.ViewState(
                latitude=21.01,
                longitude=105.81,
                zoom=10.15,
                pitch=18,
            ),
            map_style=(
                "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
                if is_light
                else "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
            ),
            tooltip=cast(Any, _tooltip(MAP_METRICS[active_metric][1], is_light=is_light)),
        ),
        height=450,
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

    st.caption(
        f"Color shows {MAP_METRICS[active_metric][1]}. Cyan outline is the selected area; amber outline is included in the current plan."
    )
    st.markdown("**Priority areas**")
    for index, area in enumerate(priority_areas[:3], start=1):
        if st.button(
            f"{index}. {area.name} · {area.heat_state_label} · {area.plan_status_label}",
            key=f"{key_prefix}:priority:{area.zone_id}",
            width="stretch",
        ):
            selected_zone_id = area.zone_id
    return selected_zone_id


__all__ = ["MAP_METRICS", "district_geojson", "map_records", "render_city_map"]
