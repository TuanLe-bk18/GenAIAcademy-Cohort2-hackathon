from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def log_event(event: str, *, severity: str = "INFO", **fields: Any) -> None:
    """Emit one-line structured JSON; Cloud Run collects stdout automatically."""
    payload = {
        "severity": severity,
        "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
        **fields,
    }
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
