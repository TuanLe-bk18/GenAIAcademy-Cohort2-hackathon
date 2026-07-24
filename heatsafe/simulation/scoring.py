"""Exact-snapshot scoring adapters for simulation ticks."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Protocol

from heatsafe.config import Settings

from .repository import Publication, SimulationRun
from .randomness import canonical_checksum


@dataclass(frozen=True, slots=True)
class SnapshotPrediction:
    driver_id_hash: str
    zone_id: str
    snapshot_id: str
    baseline_risk: float
    action_risk: float
    pause_start_delay_minutes: int
    pause_duration_minutes: int


@dataclass(frozen=True, slots=True)
class ScoringOutcome:
    prediction_run_id: str
    run_id: str
    tick_id: str
    snapshot_id: str
    simulation_time: object
    predictions: tuple[SnapshotPrediction, ...] = ()


class SimulationScorer(Protocol):
    def score(self, run: SimulationRun, publication: Publication) -> ScoringOutcome: ...


class DeterministicSnapshotScorer:
    """Local executable oracle preserving lineage and action ordering."""

    def score(self, run: SimulationRun, publication: Publication) -> ScoringOutcome:
        tick = publication.tick
        predictions = []
        for row in publication.driver_rows:
            if row["status"] not in {"IDLE", "TO_PICKUP", "ON_TRIP"}:
                continue
            exposure = float(row["continuous_exposure_minutes"])
            rest = float(row["rest_minutes_120m"])
            heat_dose = float(row["heat_dose_120m"])
            baseline = 1.0 / (
                1.0 + math.exp(-(-3.2 + exposure / 125.0 + heat_dose / 90.0 - rest / 80.0))
            )
            for duration in (15, 30):
                for delay in (0, 15, 30, 45):
                    benefit = (duration / 30.0) * math.exp(-delay / 60.0) * 0.18
                    predictions.append(
                        SnapshotPrediction(
                            driver_id_hash=str(row["driver_id_hash"]),
                            zone_id=str(row["zone_id"]),
                            snapshot_id=tick.snapshot_id,
                            baseline_risk=round(baseline, 6),
                            action_risk=round(max(0.0, baseline - benefit), 6),
                            pause_start_delay_minutes=delay,
                            pause_duration_minutes=duration,
                        )
                    )
        run_id = canonical_checksum(
            (run.run_id, tick.tick_id, tick.snapshot_id, "local-score-v1")
        )[:24]
        return ScoringOutcome(
            prediction_run_id=f"sim-{run_id}",
            run_id=run.run_id,
            tick_id=tick.tick_id,
            snapshot_id=tick.snapshot_id,
            simulation_time=tick.simulation_time,
            predictions=tuple(predictions),
        )


class BigQuerySnapshotScorer:
    def __init__(self, client: Any, *, settings: Settings):
        self.client = client
        self.settings = settings

    def score(self, run: SimulationRun, publication: Publication) -> ScoringOutcome:
        from infra.ml_pipeline import score_snapshot

        tick = publication.tick
        prediction_run_id = score_snapshot(
            self.settings,
            self.client,
            scenario=run.scenario_id,
            feature_source="simulation",
            simulation_run_id=run.run_id,
            tick_id=tick.tick_id,
            snapshot_id=tick.snapshot_id,
            simulation_time=tick.simulation_time,
        )
        return ScoringOutcome(
            prediction_run_id=prediction_run_id,
            run_id=run.run_id,
            tick_id=tick.tick_id,
            snapshot_id=tick.snapshot_id,
            simulation_time=tick.simulation_time,
        )
