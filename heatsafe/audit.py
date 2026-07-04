from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .models import InterventionEvent, SafePauseProposal

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "interventions.db"


class InterventionAuditStore:
    def __init__(self, db_path: Path = DEFAULT_DB):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS interventions (
                    intervention_id TEXT PRIMARY KEY,
                    proposal_id TEXT UNIQUE NOT NULL,
                    approved_at TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    status TEXT NOT NULL,
                    zone_id TEXT NOT NULL,
                    eligible_drivers INTEGER NOT NULL,
                    exposure_minutes_avoided INTEGER NOT NULL,
                    net_platform_cost_vnd INTEGER NOT NULL,
                    proposal_json TEXT NOT NULL
                )
                """
            )

    def approve(self, proposal: SafePauseProposal, approved_by: str = "Ops Manager") -> InterventionEvent:
        event = InterventionEvent(
            intervention_id=str(uuid.uuid4()),
            proposal_id=proposal.proposal_id,
            approved_at=datetime.now(UTC),
            approved_by=approved_by,
            status="APPROVED",
            proposal=proposal,
        )
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO interventions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.intervention_id,
                    event.proposal_id,
                    event.approved_at.isoformat(),
                    event.approved_by,
                    event.status,
                    proposal.zone_id,
                    proposal.eligible_drivers,
                    proposal.exposure_minutes_avoided,
                    proposal.net_platform_cost_vnd,
                    json.dumps(proposal.to_dict(), ensure_ascii=False),
                ),
            )
        return event

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT intervention_id, approved_at, approved_by, status,
                       'LOCAL_SIMULATED' AS dispatch_status, zone_id,
                       eligible_drivers, exposure_minutes_avoided, net_platform_cost_vnd
                FROM interventions ORDER BY approved_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def protected_driver_count(self) -> int:
        with self._connect() as db:
            value = db.execute(
                "SELECT COALESCE(SUM(eligible_drivers), 0) FROM interventions WHERE status = 'APPROVED'"
            ).fetchone()[0]
        return int(value)


class BigQueryInterventionAuditStore:
    """Durable decision audit with a Pub/Sub dispatch command."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.dataset = self.settings.dataset_path

    def _client(self):
        from google.cloud import bigquery

        return bigquery.Client(project=self.settings.project_id)

    def _publisher(self):
        from google.cloud import pubsub_v1

        publisher = pubsub_v1.PublisherClient()
        topic = publisher.topic_path(self.settings.project_id, self.settings.dispatch_topic)
        publisher.get_topic(request={"topic": topic})
        return publisher, topic

    def _existing(self, proposal: SafePauseProposal) -> InterventionEvent | None:
        from google.cloud import bigquery

        query = f"""
            SELECT intervention_id, approved_at, approved_by, status
            FROM `{self.dataset}.intervention_events`
            WHERE proposal_id = @proposal_id
            ORDER BY approved_at DESC LIMIT 1
        """
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("proposal_id", "STRING", proposal.proposal_id)
            ],
            maximum_bytes_billed=10_000_000,
        )
        row = next(iter(self._client().query(query, job_config=config).result()), None)
        if not row:
            return None
        return InterventionEvent(
            intervention_id=row.intervention_id,
            proposal_id=proposal.proposal_id,
            approved_at=row.approved_at.astimezone(UTC),
            approved_by=row.approved_by,
            status=row.status,
            proposal=proposal,
        )

    def approve(self, proposal: SafePauseProposal, approved_by: str = "Ops Manager") -> InterventionEvent:
        existing = self._existing(proposal)
        if existing:
            return existing

        # Verify dispatch infrastructure before making the durable approval write.
        publisher, topic = self._publisher()
        event = InterventionEvent(
            intervention_id=str(uuid.uuid4()),
            proposal_id=proposal.proposal_id,
            approved_at=datetime.now(UTC),
            approved_by=approved_by,
            status="APPROVED",
            proposal=proposal,
        )
        client = self._client()
        proposal_errors = client.insert_rows_json(
            f"{self.dataset}.intervention_proposals",
            [
                {
                    "proposal_id": proposal.proposal_id,
                    "created_at": proposal.created_at.isoformat(),
                    "zone_id": proposal.zone_id,
                    "eligible_drivers": proposal.eligible_drivers,
                    "exposure_minutes_avoided": proposal.exposure_minutes_avoided,
                    "net_platform_cost_vnd": proposal.net_platform_cost_vnd,
                    "projected_fulfillment_rate": proposal.projected_fulfillment_rate,
                    "within_guardrails": proposal.within_guardrails,
                    "proposal_json": json.dumps(proposal.to_dict(), ensure_ascii=False),
                }
            ],
            row_ids=[proposal.proposal_id],
        )
        if proposal_errors:
            raise RuntimeError(f"BigQuery proposal insert failed: {proposal_errors}")
        command = {
            "schema_version": 1,
            "command": "ACTIVATE_SAFEPAUSE",
            "intervention_id": event.intervention_id,
            "proposal": proposal.to_dict(),
        }
        future = publisher.publish(
            topic,
            json.dumps(command, ensure_ascii=False).encode("utf-8"),
            intervention_id=event.intervention_id,
            zone_id=proposal.zone_id,
        )
        future.result(timeout=15)
        event_errors = client.insert_rows_json(
            f"{self.dataset}.intervention_events",
            [
                {
                    "intervention_id": event.intervention_id,
                    "proposal_id": event.proposal_id,
                    "approved_at": event.approved_at.isoformat(),
                    "approved_by": event.approved_by,
                    "status": event.status,
                    "dispatch_status": "PUBLISHED",
                    "zone_id": proposal.zone_id,
                    "eligible_drivers": proposal.eligible_drivers,
                    "exposure_minutes_avoided": proposal.exposure_minutes_avoided,
                    "net_platform_cost_vnd": proposal.net_platform_cost_vnd,
                }
            ],
            row_ids=[event.intervention_id],
        )
        if event_errors:
            raise RuntimeError(f"BigQuery event insert failed: {event_errors}")
        return event

    def list_recent(self, limit: int = 20) -> list[dict]:
        from google.cloud import bigquery

        limit = max(1, min(limit, 100))
        query = f"""
            SELECT intervention_id, approved_at, approved_by, status, dispatch_status,
                   zone_id, eligible_drivers, exposure_minutes_avoided, net_platform_cost_vnd
            FROM `{self.dataset}.intervention_events`
            ORDER BY approved_at DESC LIMIT {limit}
        """
        return [dict(row) for row in self._client().query(query).result()]

    def protected_driver_count(self) -> int:
        query = f"""
            SELECT COALESCE(SUM(eligible_drivers), 0) protected
            FROM `{self.dataset}.intervention_events`
            WHERE status = 'APPROVED'
        """
        row = next(iter(self._client().query(query).result()))
        return int(row.protected)


class HybridInterventionAuditStore:
    def __init__(self, mode: str | None = None, local_db_path: Path = DEFAULT_DB):
        self.settings = Settings.from_env()
        self.mode = (mode or self.settings.mode).lower()
        self.local = InterventionAuditStore(local_db_path)
        self.cloud = BigQueryInterventionAuditStore(self.settings)
        self.backend = "local" if self.mode == "snapshot" else "cloud"
        self.fallback_reason: str | None = None

    def _call(self, method: str, *args, **kwargs):
        if self.mode == "snapshot":
            self.backend = "local"
            return getattr(self.local, method)(*args, **kwargs)
        try:
            value = getattr(self.cloud, method)(*args, **kwargs)
            self.backend = "cloud"
            self.fallback_reason = None
            return value
        except Exception as exc:
            if self.mode == "cloud":
                raise
            self.backend = "local"
            self.fallback_reason = str(exc)
            return getattr(self.local, method)(*args, **kwargs)

    def approve(self, proposal: SafePauseProposal, approved_by: str = "Ops Manager") -> InterventionEvent:
        return self._call("approve", proposal, approved_by)

    def list_recent(self, limit: int = 20) -> list[dict]:
        return self._call("list_recent", limit)

    def protected_driver_count(self) -> int:
        return self._call("protected_driver_count")
