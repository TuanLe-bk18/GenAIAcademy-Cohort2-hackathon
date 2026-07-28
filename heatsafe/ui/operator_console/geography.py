"""Licensed, offline map geometry for the HeatSafe operator presentation."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
HANOI_OPERATOR_DISTRICTS = (
    ROOT / "data" / "geo" / "heatsafe_hanoi_operator_districts.geojson"
)


@lru_cache(maxsize=1)
def load_hanoi_operator_districts() -> dict[str, Any]:
    """Load the fixed ten-district presentation asset with minimal validation."""
    value = json.loads(HANOI_OPERATOR_DISTRICTS.read_text(encoding="utf-8"))
    features = value.get("features")
    if value.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise ValueError("Hanoi operator district geometry is invalid")
    zone_ids = [
        feature.get("properties", {}).get("zone_id")
        for feature in features
        if isinstance(feature, dict)
    ]
    if len(features) != 10 or len(set(zone_ids)) != 10 or any(not item for item in zone_ids):
        raise ValueError("Hanoi operator district geometry must contain ten unique zones")
    return value


__all__ = ["HANOI_OPERATOR_DISTRICTS", "load_hanoi_operator_districts"]
