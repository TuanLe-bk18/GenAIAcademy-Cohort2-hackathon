from __future__ import annotations

import os
import ipaddress
import re
from dataclasses import dataclass


_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATASET_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_QUALIFIED_DATASET_RE = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]\.[a-z][a-z0-9_]{0,127}$"
)
_IP_LIKE_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_VERSION_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_REGIONS = frozenset({"asia-southeast1"})
_VERTEX_LOCATIONS = frozenset({"global"})
_MODES = frozenset({"auto", "cloud", "snapshot"})
_SCENARIOS = frozenset({"heatwave", "live"})
_SNAPSHOT_TABLES = frozenset({"zone_snapshots_current"})
_GEMINI_MODELS = frozenset({"gemini-3.1-flash-lite"})
_SIMULATION_SCENARIO_VERSIONS = frozenset({"hanoi_heatwave_v1"})
_SIMULATION_GENERATOR_VERSIONS = frozenset({"stateful-replay-v1"})
_SIMULATION_STATE_MODES = frozenset({"oracle", "checkpoint"})


def _require_match(name: str, value: str, pattern: re.Pattern[str]) -> None:
    if not pattern.fullmatch(value):
        raise ValueError(f"{name} has an invalid value: {value!r}")


def _require_choice(name: str, value: str, choices: frozenset[str]) -> None:
    if value not in choices:
        raise ValueError(f"{name} must be one of {sorted(choices)}; got {value!r}")


def _validate_bucket(value: str) -> None:
    _require_match("HEATSAFE_RAW_BUCKET", value, _BUCKET_RE)
    if any(token in value for token in ("..", ".-", "-.")):
        raise ValueError("HEATSAFE_RAW_BUCKET contains a forbidden separator sequence")
    if _IP_LIKE_RE.fullmatch(value):
        raise ValueError("HEATSAFE_RAW_BUCKET must not be an IP-like name")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return
    raise ValueError("HEATSAFE_RAW_BUCKET must not be an IP address")


def _parse_bool(name: str, default: str) -> bool:
    value = os.getenv(name, default)
    if value not in {"0", "1"}:
        raise ValueError(f"{name} must be 0 or 1; got {value!r}")
    return value == "1"


def _parse_int(name: str, default: str, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer; got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in {minimum}..{maximum}; got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    project_id: str = "cohort2track2"
    region: str = "asia-southeast1"
    vertex_location: str = "global"
    dataset_id: str = "heatsafe_data"
    raw_bucket: str = "cohort2track2-heatsafe-raw"
    current_snapshot_table: str = "zone_snapshots_current"
    mode: str = "auto"
    scenario: str = "heatwave"
    live_freshness_minutes: int = 30
    enable_ai: bool = True
    gemini_model: str = "gemini-3.1-flash-lite"
    simulation_enabled: bool = False
    simulation_scenario_version: str = "hanoi_heatwave_v1"
    simulation_seed: int = 42
    simulation_tick_minutes: int = 15
    simulation_lease_seconds: int = 360
    simulation_generator_version: str = "stateful-replay-v1"
    simulation_staging_dataset_id: str = "heatsafe_sim_staging"
    simulation_checkpoint_bucket: str = "cohort2track2-heatsafe-sim-checkpoints"
    simulation_state_mode: str = "oracle"
    simulation_staging_workers: int = 1
    simulation_model_dataset: str | None = None

    def __post_init__(self) -> None:
        _require_match("GOOGLE_CLOUD_PROJECT", self.project_id, _PROJECT_ID_RE)
        _require_choice("GOOGLE_CLOUD_REGION", self.region, _REGIONS)
        _require_choice("GOOGLE_CLOUD_LOCATION", self.vertex_location, _VERTEX_LOCATIONS)
        _require_match("HEATSAFE_DATASET", self.dataset_id, _DATASET_ID_RE)
        _validate_bucket(self.raw_bucket)
        _require_choice(
            "HEATSAFE_CURRENT_SNAPSHOT_TABLE",
            self.current_snapshot_table,
            _SNAPSHOT_TABLES,
        )
        _require_choice("HEATSAFE_MODE", self.mode, _MODES)
        _require_choice("HEATSAFE_SCENARIO", self.scenario, _SCENARIOS)
        if not 1 <= self.live_freshness_minutes <= 1_440:
            raise ValueError("HEATSAFE_LIVE_FRESHNESS_MINUTES must be in 1..1440")
        _require_choice("HEATSAFE_GEMINI_MODEL", self.gemini_model, _GEMINI_MODELS)
        _require_match(
            "HEATSAFE_SIMULATION_SCENARIO_VERSION",
            self.simulation_scenario_version,
            _VERSION_RE,
        )
        _require_choice(
            "HEATSAFE_SIMULATION_SCENARIO_VERSION",
            self.simulation_scenario_version,
            _SIMULATION_SCENARIO_VERSIONS,
        )
        if not 0 <= self.simulation_seed <= 9_223_372_036_854_775_807:
            raise ValueError("HEATSAFE_SIMULATION_SEED must fit a non-negative INT64")
        if self.simulation_tick_minutes != 15:
            raise ValueError("HEATSAFE_SIMULATION_TICK_MINUTES must be 15 in P0")
        if not 60 <= self.simulation_lease_seconds <= 3_600:
            raise ValueError("HEATSAFE_SIMULATION_LEASE_SECONDS must be in 60..3600")
        _require_match(
            "HEATSAFE_SIMULATION_GENERATOR_VERSION",
            self.simulation_generator_version,
            _VERSION_RE,
        )
        _require_choice(
            "HEATSAFE_SIMULATION_GENERATOR_VERSION",
            self.simulation_generator_version,
            _SIMULATION_GENERATOR_VERSIONS,
        )
        _require_match(
            "HEATSAFE_SIMULATION_STAGING_DATASET",
            self.simulation_staging_dataset_id,
            _DATASET_ID_RE,
        )
        if self.simulation_staging_dataset_id == self.dataset_id:
            raise ValueError(
                "HEATSAFE_SIMULATION_STAGING_DATASET must be separate from "
                "HEATSAFE_DATASET"
            )
        _validate_bucket(self.simulation_checkpoint_bucket)
        _require_choice(
            "HEATSAFE_SIMULATION_STATE_MODE",
            self.simulation_state_mode,
            _SIMULATION_STATE_MODES,
        )
        if not 1 <= self.simulation_staging_workers <= 4:
            raise ValueError("HEATSAFE_SIMULATION_STAGING_WORKERS must be in 1..4")
        if (
            self.simulation_model_dataset is not None
            and not _QUALIFIED_DATASET_RE.fullmatch(self.simulation_model_dataset)
        ):
            raise ValueError(
                "HEATSAFE_SIMULATION_MODEL_DATASET must be project.dataset"
            )

    @classmethod
    def from_env(cls) -> "Settings":
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "cohort2track2")
        return cls(
            project_id=project_id,
            region=os.getenv("GOOGLE_CLOUD_REGION", "asia-southeast1"),
            vertex_location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            dataset_id=os.getenv("HEATSAFE_DATASET", "heatsafe_data"),
            raw_bucket=os.getenv("HEATSAFE_RAW_BUCKET", f"{project_id}-heatsafe-raw"),
            current_snapshot_table=os.getenv(
                "HEATSAFE_CURRENT_SNAPSHOT_TABLE", "zone_snapshots_current"
            ),
            mode=os.getenv("HEATSAFE_MODE", "auto").lower(),
            scenario=os.getenv("HEATSAFE_SCENARIO", "heatwave").lower(),
            live_freshness_minutes=_parse_int(
                "HEATSAFE_LIVE_FRESHNESS_MINUTES",
                "30",
                minimum=1,
                maximum=1_440,
            ),
            enable_ai=_parse_bool("HEATSAFE_ENABLE_AI", "1"),
            gemini_model=os.getenv("HEATSAFE_GEMINI_MODEL", "gemini-3.1-flash-lite"),
            simulation_enabled=_parse_bool("HEATSAFE_SIMULATION_ENABLED", "0"),
            simulation_scenario_version=os.getenv(
                "HEATSAFE_SIMULATION_SCENARIO_VERSION", "hanoi_heatwave_v1"
            ),
            simulation_seed=_parse_int(
                "HEATSAFE_SIMULATION_SEED",
                "42",
                minimum=0,
                maximum=9_223_372_036_854_775_807,
            ),
            simulation_tick_minutes=_parse_int(
                "HEATSAFE_SIMULATION_TICK_MINUTES",
                "15",
                minimum=1,
                maximum=1_440,
            ),
            simulation_lease_seconds=_parse_int(
                "HEATSAFE_SIMULATION_LEASE_SECONDS",
                "360",
                minimum=1,
                maximum=86_400,
            ),
            simulation_generator_version=os.getenv(
                "HEATSAFE_SIMULATION_GENERATOR_VERSION", "stateful-replay-v1"
            ),
            simulation_staging_dataset_id=os.getenv(
                "HEATSAFE_SIMULATION_STAGING_DATASET", "heatsafe_sim_staging"
            ),
            simulation_checkpoint_bucket=os.getenv(
                "HEATSAFE_SIMULATION_CHECKPOINT_BUCKET",
                f"{project_id}-heatsafe-sim-checkpoints",
            ),
            simulation_state_mode=os.getenv(
                "HEATSAFE_SIMULATION_STATE_MODE", "oracle"
            ).lower(),
            simulation_staging_workers=_parse_int(
                "HEATSAFE_SIMULATION_STAGING_WORKERS",
                "1",
                minimum=1,
                maximum=4,
            ),
            simulation_model_dataset=(
                os.getenv("HEATSAFE_SIMULATION_MODEL_DATASET") or None
            ),
        )

    @property
    def dataset_path(self) -> str:
        return f"{self.project_id}.{self.dataset_id}"

    @property
    def simulation_staging_dataset_path(self) -> str:
        return f"{self.project_id}.{self.simulation_staging_dataset_id}"
