"""Fail-closed loader for the cloud-backed hackathon Production bundle."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .models import CurrentForecastInput, DecisionConstraints, PredictiveCityPlan
from .repository import BigQueryRepository
from .services.preventive_planning import (
    build_current_forecast_input,
    build_predictive_city_plan,
    project_city_forecast,
)

EVENT_REPLAY_START_TICK = 37
EVENT_REPLAY_DECISION_TICK = 40
EVENT_REPLAY_END_TICK = 41
EVENT_REPLAY_GENERATOR_VERSION = "stateful-replay-v2"


class ProductionBundleUnavailable(RuntimeError):
    """Raised when configured cloud evidence is absent or internally incoherent."""


@dataclass(frozen=True)
class CloudProductionBundle:
    repository: BigQueryRepository
    zones: tuple[Any, ...]
    forecast_input: CurrentForecastInput
    simulation_run_id: str
    tick_index: int
    dataset_id: str

    def build_plan(
        self, constraints: DecisionConstraints
    ) -> PredictiveCityPlan:
        return build_predictive_city_plan(
            project_city_forecast(self.forecast_input),
            constraints,
        )


def _verify_bundle_ledger(
    repository: BigQueryRepository,
    *,
    run_id: str,
    selected_tick_index: int,
) -> None:
    from google.cloud import bigquery

    query = f"""
      SELECT
        ANY_VALUE(run.status) AS run_status,
        ANY_VALUE(run.pending_score_tick_id) AS pending_score_tick_id,
        COUNTIF(
          tick.tick_index BETWEEN @start_tick AND @end_tick
          AND tick.status = 'SUCCEEDED'
          AND tick.scoring_outcome = 'SCORED'
        ) AS scored_tick_count,
        COUNTIF(
          tick.tick_index BETWEEN @start_tick AND @end_tick
          AND tick.generator_version != @generator_version
        ) AS wrong_generator_count,
        COUNTIF(
          tick.tick_index = @selected_tick_index
          AND tick.status = 'SUCCEEDED'
          AND tick.scoring_outcome = 'SCORED'
          AND tick.snapshot_id IS NOT NULL
          AND tick.forecast_source_prediction_run_id IS NOT NULL
        ) AS selected_tick_ready,
        ANY_VALUE(run.risk_model_version) AS risk_model_version,
        ANY_VALUE(run.forecast_context_version) AS forecast_context_version
      FROM `{repository.dataset}.simulation_runs` run
      JOIN `{repository.dataset}.simulation_ticks` tick
        USING (simulation_run_id)
      WHERE run.simulation_run_id = @run_id
      GROUP BY run.simulation_run_id
    """
    config = repository._job_config(
        [
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ScalarQueryParameter(
                "start_tick", "INT64", EVENT_REPLAY_START_TICK
            ),
            bigquery.ScalarQueryParameter(
                "end_tick", "INT64", EVENT_REPLAY_END_TICK
            ),
            bigquery.ScalarQueryParameter(
                "selected_tick_index", "INT64", selected_tick_index
            ),
            bigquery.ScalarQueryParameter(
                "generator_version",
                "STRING",
                EVENT_REPLAY_GENERATOR_VERSION,
            ),
        ],
        50_000_000,
    )
    rows = list(repository._client().query(query, job_config=config).result())
    if len(rows) != 1:
        raise ProductionBundleUnavailable("configured bundle run was not found")
    row = dict(rows[0])
    failures = []
    if row.get("run_status") != "PAUSED":
        failures.append("run is not PAUSED")
    if row.get("pending_score_tick_id") is not None:
        failures.append("run has pending scoring")
    if int(row.get("scored_tick_count") or 0) != 5:
        failures.append("ticks 37-41 are not all scored")
    if int(row.get("wrong_generator_count") or 0) != 0:
        failures.append("generator lineage is not v2")
    if int(row.get("selected_tick_ready") or 0) != 1:
        failures.append("selected tick is not ready")
    if not row.get("risk_model_version"):
        failures.append("BQML model lineage is missing")
    if row.get("forecast_context_version") != "timesfm-2.5-context-2048-v1":
        failures.append("TimesFM context lineage is missing")
    if failures:
        raise ProductionBundleUnavailable("; ".join(failures))


def load_cloud_production_bundle(
    settings: Settings | None = None,
) -> CloudProductionBundle:
    settings = settings or Settings.from_env()
    if not settings.production_bundle_enabled:
        raise ProductionBundleUnavailable("cloud Production bundle is not configured")
    assert settings.production_bundle_dataset_id is not None
    assert settings.production_bundle_run_id is not None
    repository = BigQueryRepository(
        project_id=settings.project_id,
        dataset_id=settings.production_bundle_dataset_id,
        scenario="heatwave",
    )
    with ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="heatsafe-bundle",
    ) as pool:
        ledger_future = pool.submit(
            _verify_bundle_ledger,
            repository,
            run_id=settings.production_bundle_run_id,
            selected_tick_index=settings.production_bundle_tick_index,
        )
        snapshot_future = pool.submit(
            repository.load_replay_tick,
            settings.production_bundle_run_id,
            settings.production_bundle_tick_index,
        )
        ledger_future.result()
        snapshot = snapshot_future.result()
    zones = tuple(snapshot.zones)
    forecast_input = build_current_forecast_input(repository, zones)
    if (
        forecast_input.lineage.simulation_run_id
        != settings.production_bundle_run_id
        or forecast_input.lineage.tick_index
        not in {None, settings.production_bundle_tick_index}
        or forecast_input.lineage.generator_version
        != EVENT_REPLAY_GENERATOR_VERSION
    ):
        raise ProductionBundleUnavailable(
            "bundle component lineage does not match configured run"
        )
    return CloudProductionBundle(
        repository=repository,
        zones=zones,
        forecast_input=forecast_input,
        simulation_run_id=settings.production_bundle_run_id,
        tick_index=settings.production_bundle_tick_index,
        dataset_id=settings.production_bundle_dataset_id,
    )


__all__ = [
    "CloudProductionBundle",
    "EVENT_REPLAY_DECISION_TICK",
    "EVENT_REPLAY_END_TICK",
    "EVENT_REPLAY_GENERATOR_VERSION",
    "EVENT_REPLAY_START_TICK",
    "ProductionBundleUnavailable",
    "load_cloud_production_bundle",
]
