from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    project_id: str = "cohort2track2"
    region: str = "asia-southeast1"
    vertex_location: str = "global"
    dataset_id: str = "heatsafe_data"
    raw_bucket: str = ""
    dispatch_topic: str = "heatsafe-dispatch-commands"
    mode: str = "auto"
    scenario: str = "heatwave"
    max_data_age_hours: int = 3
    enable_ai: bool = False
    gemini_model: str = "gemini-3.1-flash-lite"

    @classmethod
    def from_env(cls) -> "Settings":
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "cohort2track2")
        return cls(
            project_id=project_id,
            region=os.getenv("GOOGLE_CLOUD_REGION", "asia-southeast1"),
            vertex_location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            dataset_id=os.getenv("HEATSAFE_DATASET", "heatsafe_data"),
            raw_bucket=os.getenv("HEATSAFE_RAW_BUCKET", f"{project_id}-heatsafe-raw"),
            dispatch_topic=os.getenv("HEATSAFE_DISPATCH_TOPIC", "heatsafe-dispatch-commands"),
            mode=os.getenv("HEATSAFE_MODE", "auto").lower(),
            scenario=os.getenv("HEATSAFE_SCENARIO", "heatwave").lower(),
            max_data_age_hours=int(os.getenv("HEATSAFE_MAX_DATA_AGE_HOURS", "3")),
            enable_ai=os.getenv("HEATSAFE_ENABLE_AI", "0") == "1",
            gemini_model=os.getenv("HEATSAFE_GEMINI_MODEL", "gemini-3.1-flash-lite"),
        )

    @property
    def dataset_path(self) -> str:
        return f"{self.project_id}.{self.dataset_id}"
