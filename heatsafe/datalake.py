from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings


class CloudStorageDataLake:
    """Immutable raw landing and replay store for external payloads."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self._client = None

    def _storage_client(self):
        if self._client is None:
            from google.cloud import storage

            self._client = storage.Client(project=self.settings.project_id)
        return self._client

    def upload_json(
        self,
        category: str,
        payload: dict[str, Any] | list[Any],
        *,
        observed_at: datetime | None = None,
        object_name: str | None = None,
    ) -> str:
        timestamp = (observed_at or datetime.now(UTC)).astimezone(UTC)
        name = object_name or f"{timestamp.strftime('%H%M%S%f')}.json"
        path = (
            f"{category}/date={timestamp:%Y-%m-%d}/hour={timestamp:%H}/{name}"
        )
        blob = self._storage_client().bucket(self.settings.raw_bucket).blob(path)
        blob.upload_from_string(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            content_type="application/json",
            if_generation_match=0,
        )
        return f"gs://{self.settings.raw_bucket}/{path}"

    def upload_file(self, local_path: Path, destination: str) -> str:
        blob = self._storage_client().bucket(self.settings.raw_bucket).blob(destination)
        blob.upload_from_filename(str(local_path), if_generation_match=0)
        return f"gs://{self.settings.raw_bucket}/{destination}"
