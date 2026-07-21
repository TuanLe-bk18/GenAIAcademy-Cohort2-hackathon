from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

from heatsafe.currency import vnd_to_usd
from heatsafe.models import SafePauseProposal, ZoneSnapshot
from heatsafe.services.decision_service import CityWidePlan

from .styles import PLOTLY_LAYOUT


def _city_rows(city_plan: CityWidePlan) -> list[dict[str, Any]]:
    return [
        {
            "Zone": row.zone.name,
            "Heat Index": row.zone.heat_index_c,
            "Eligible Drivers": row.proposal.eligible_drivers,
            "Selected Drivers": row.proposal.selected_drivers,
            "Mandatory Drivers": row.proposal.high_priority_drivers,
            "Prevented Risks": row.proposal.expected_risk_events_prevented,
            "Platform Cost": vnd_to_usd(row.proposal.net_platform_cost_vnd),
            "Fulfillment Drop": max(
                0.0,
                (
                    row.proposal.baseline_stress_fulfillment_rate
                    - row.proposal.p90_fulfillment_rate
                )
                * 100,
            ),
            "ETA Impact": row.proposal.p90_eta_increase_minutes,
        }
        for row in city_plan.rows
    ]


def render_city_intelligence(
    zones: Sequence[ZoneSnapshot],
    zone_risk: Mapping[str, float],
    city_plan: CityWidePlan,
    *,
    selection_context: str,
) -> None:
    """Render the selectable city map, tradeoffs, coverage, and unavailable zones."""
    selected_zone_id = st.session_state.get("selected_zone_id")
    valid_zone_ids = {zone.zone_id for zone in zones}
    selected = next(
        (zone for zone in zones if zone.zone_id == selected_zone_id),
        zones[0] if zones else None,
    )
    max_risk = max(zone_risk.values(), default=1.0) or 1.0
    map_rows = []
    for zone in zones:
        expected = zone_risk.get(zone.zone_id)
        intensity = max(0.0, (expected or 0.0) / max_risk)
        map_rows.append(
            {
                "zone_id": zone.zone_id,
                "name": zone.name,
                "lat": zone.latitude,
                "lon": zone.longitude,
                "expected_events": round(expected, 2) if expected is not None else None,
                "heat_index": zone.heat_index_c,
                "active": zone.active_drivers,
                "color": [
                    round(255 * max(0.35, min(1.0, intensity))),
                    round(150 * (1 - min(1.0, intensity))),
                    75,
                    210,
                ],
            }
        )

    map_column, scatter_column, bar_column = st.columns([1, 1, 1], gap="medium")
    with map_column:
        if map_rows:
            map_event = st.pydeck_chart(
                pdk.Deck(
                    layers=[
                        pdk.Layer(
                            "ScatterplotLayer",
                            pd.DataFrame(map_rows),
                            id="city-zones",
                            get_position=["lon", "lat"],
                            get_fill_color="color",
                            get_radius="800 + active * 2",
                            pickable=True,
                            stroked=True,
                            get_line_color=[255, 255, 255, 80],
                        )
                    ],
                    initial_view_state=pdk.ViewState(
                        latitude=21.025,
                        longitude=105.81,
                        zoom=10.1,
                        pitch=35,
                    ),
                    map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
                    tooltip=cast(
                        Any,
                        {
                            "html": "<b>{name}</b><br/>Expected escalations: {expected_events}<br/>Heat Index: {heat_index}°C<br/>Active: {active}",
                            "style": {"backgroundColor": "#211f1c", "color": "white"},
                        },
                    ),
                ),
                height=360,
                key=f"city-zone-map:{selection_context}:{selected_zone_id}",
                on_select="rerun",
                selection_mode="single-object",
            )
            selected_objects = (
                map_event.get("selection", {})
                .get("objects", {})
                .get("city-zones", [])
            )
            clicked_zone_id = (
                selected_objects[0].get("zone_id") if selected_objects else None
            )
            if (
                clicked_zone_id in valid_zone_ids
                and clicked_zone_id != selected_zone_id
            ):
                st.session_state.selected_zone_id = clicked_zone_id
                st.rerun()
        if selected is not None:
            st.caption(
                f"Selected: {selected.name}. Priority uses summed driver-level model probability."
            )

    records = _city_rows(city_plan)
    if records:
        frame = pd.DataFrame(records).sort_values(
            "Selected Drivers", ascending=False
        )
        highlighted_zone = selected.name if selected is not None else None
        opacities = [
            1.0 if not highlighted_zone or zone == highlighted_zone else 0.22
            for zone in frame["Zone"]
        ]
        with scatter_column:
            figure = px.scatter(
                frame,
                x="Fulfillment Drop",
                y="ETA Impact",
                size="Prevented Risks",
                color="Heat Index",
                hover_name="Zone",
                color_continuous_scale=px.colors.sequential.YlOrRd,
                title="City-wide intervention tradeoffs",
                labels={
                    "Fulfillment Drop": "Fulfillment Drop (%)",
                    "ETA Impact": "ETA Impact (mins)",
                    "Prevented Risks": "Expected Escalations Prevented",
                    "Heat Index": "Heat Index (°C)",
                },
            )
            figure.update_traces(marker_opacity=opacities)
            figure.update_layout(
                **PLOTLY_LAYOUT,
                margin={"l": 0, "r": 0, "t": 75, "b": 0},
                height=360,
                title_x=0.5,
                title_xanchor="center",
                xaxis={"gridcolor": "rgba(255,255,255,.06)"},
                yaxis={"gridcolor": "rgba(255,255,255,.06)"},
                coloraxis_colorbar={"title_side": "right"},
            )
            figure.add_vline(
                x=2.0, line_dash="dash", line_color="red", opacity=0.5
            )
            figure.add_hline(
                y=2.0, line_dash="dash", line_color="red", opacity=0.5
            )
            st.plotly_chart(figure, width="stretch")

        with bar_column:
            frame["Model-prioritized Drivers"] = (
                frame["Selected Drivers"] - frame["Mandatory Drivers"]
            )
            plan_figure = go.Figure()
            plan_figure.add_trace(
                go.Bar(
                    name="Mandatory Drivers",
                    x=frame["Zone"],
                    y=frame["Mandatory Drivers"],
                    marker={"opacity": opacities},
                    customdata=frame["Selected Drivers"],
                    hovertemplate="Mandatory: %{y}<br>Total Selected: %{customdata}",
                )
            )
            plan_figure.add_trace(
                go.Bar(
                    name="Model-prioritized Drivers",
                    x=frame["Zone"],
                    y=frame["Model-prioritized Drivers"],
                    marker={"opacity": opacities},
                    customdata=frame["Selected Drivers"],
                    hovertemplate="Model-prioritized: %{y}<br>Total Selected: %{customdata}",
                )
            )
            plan_figure.update_layout(
                **PLOTLY_LAYOUT,
                barmode="stack",
                title="SafePause Coverage & Estimated Risk Reduction",
                title_x=0.5,
                title_xanchor="center",
                margin={"l": 0, "r": 0, "t": 75, "b": 0},
                height=360,
                legend={
                    "orientation": "h",
                    "yanchor": "bottom",
                    "y": 1.05,
                    "xanchor": "right",
                    "x": 1,
                },
            )
            plan_figure.update_xaxes(
                title_text="Zone", gridcolor="rgba(255,255,255,.06)"
            )
            plan_figure.update_yaxes(
                title_text="Selected Drivers",
                gridcolor="rgba(255,255,255,.06)",
            )
            st.plotly_chart(plan_figure, width="stretch")
    else:
        with scatter_column:
            st.info("No city-wide actionable recommendations are currently available.")

    if city_plan.unavailable_zones:
        st.warning(
            f"{len(city_plan.unavailable_zones)} city zone(s) are unavailable for intervention planning."
        )
        unavailable_rows = [
            {
                "Zone": item.zone_name,
                "Zone ID": item.zone_id,
                "Reason": item.reason_code,
                "Detail": item.message,
            }
            for item in city_plan.unavailable_zones
        ]
        with st.expander("Unavailable city zones", expanded=True):
            st.dataframe(
                pd.DataFrame(unavailable_rows), hide_index=True, width="stretch"
            )


def render_driver_evidence(proposal: SafePauseProposal | None) -> None:
    """Render ordered driver-level decision evidence for a SafePause proposal."""
    if proposal is None:
        st.info("Driver-level evidence requires a valid recommendation.")
        return
    st.markdown("#### Selected Driver list for SafePause")
    rows = [
        {
            "Driver": item.driver_id_hash[:10],
            "Priority": (
                "Mandatory 4h+"
                if item.priority_tier == "MANDATORY_4H"
                else "Model eligible"
            ),
            "Exposure": f"{item.exposure_minutes}m",
            "Risk before": f"{item.baseline_risk:.1%}",
            "Risk after": f"{item.action_risk:.1%}",
            "Wait cost": f"{item.risk_of_waiting:.1%}",
            "Start": f"+{item.pause_start_delay_minutes}m",
            "Pause": f"{item.pause_duration_minutes}m",
            "Evidence": ", ".join(item.top_factors[:3]),
        }
        for item in sorted(
            proposal.driver_decisions,
            key=lambda item: (
                item.pause_start_delay_minutes,
                item.priority_tier != "MANDATORY_4H",
                -item.baseline_risk,
            ),
        )
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "Factors explain the no-action prediction; they do not prove why a pause works."
    )


def render_model_performance(
    evaluations: Sequence[Mapping[str, Any]],
    proposal: SafePauseProposal | None = None,
) -> None:
    """Render active BigQuery ML evaluation metrics and recent model history."""
    if not evaluations:
        st.info(
            "Model evaluation metrics are available in cloud mode after BigQuery ML training."
        )
        return
    active_version = proposal.model_version if proposal else evaluations[0]["model_version"]
    active = next(
        (row for row in evaluations if row["model_version"] == active_version),
        evaluations[0],
    )
    metric_specs = (
        ("ROC AUC", "roc_auc"),
        ("F1", "f1_score"),
        ("Precision", "precision"),
        ("Recall", "recall"),
        ("Log loss", "log_loss"),
    )
    for column, (label, field) in zip(st.columns(len(metric_specs)), metric_specs):
        value = active.get(field)
        column.metric(label, "—" if value is None else f"{float(value):.3f}")

    history = pd.DataFrame(evaluations)
    history["evaluated_at"] = pd.to_datetime(history["evaluated_at"])
    table = history.rename(
        columns={
            "model_version": "Model version",
            "evaluated_at": "Evaluated at",
            "roc_auc": "ROC AUC",
            "f1_score": "F1",
            "precision": "Precision",
            "recall": "Recall",
            "accuracy": "Accuracy",
            "log_loss": "Log loss",
        }
    )
    display_columns = [
        "Model version",
        "Evaluated at",
        "ROC AUC",
        "F1",
        "Precision",
        "Recall",
        "Accuracy",
        "Log loss",
    ]
    styled = table[display_columns].style.set_properties(
        **{"font-weight": "bold", "color": "#72cbd0"},
        subset=["Model version"],
    )
    st.dataframe(styled, hide_index=True, width="stretch")
    prediction_run = proposal.prediction_run_id if proposal else "not available"
    st.caption(
        f"Active risk model: {active['model_version']} · Prediction run: {prediction_run} · "
        "BigQuery ML boosted-tree · Evaluation data: "
        f"{'simulated' if active.get('is_simulated') else 'production'}"
    )


def render_evidence_tabs(
    zones: Sequence[ZoneSnapshot],
    zone_risk: Mapping[str, float],
    city_plan: CityWidePlan,
    evaluations: Sequence[Mapping[str, Any]],
    proposal: SafePauseProposal | None,
    *,
    selection_context: str,
) -> None:
    """Compose city, driver, and model evidence into progressive-disclosure tabs."""
    city_tab, driver_tab, model_tab = st.tabs(
        ["CITY INTELLIGENCE", "DRIVER EVIDENCE", "MODEL PERFORMANCE"]
    )
    with city_tab:
        render_city_intelligence(
            zones, zone_risk, city_plan, selection_context=selection_context
        )
    with driver_tab:
        render_driver_evidence(proposal)
    with model_tab:
        render_model_performance(evaluations, proposal)


__all__ = [
    "render_city_intelligence",
    "render_driver_evidence",
    "render_evidence_tabs",
    "render_model_performance",
]
