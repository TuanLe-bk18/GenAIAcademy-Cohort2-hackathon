from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .models import InterventionEvent, SafePauseProposal

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "interventions.db"
DEMO_ACTOR = "Public demo session"
DEMO_ACTOR_TYPE = "UNAUTHENTICATED_DEMO"
MINIMUM_QUERY_BYTES_BILLED = 10 * 1024 * 1024


def intervention_id_for(proposal_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"heatsafe:simulated:{proposal_id}"))


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
                    actor_type TEXT NOT NULL DEFAULT 'UNAUTHENTICATED_DEMO',
                    status TEXT NOT NULL,
                    dispatch_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
                    zone_id TEXT NOT NULL,
                    eligible_drivers INTEGER NOT NULL,
                    selected_drivers INTEGER NOT NULL,
                    exposure_minutes_avoided INTEGER NOT NULL,
                    net_platform_cost_vnd INTEGER NOT NULL,
                    proposal_json TEXT NOT NULL
                )
                """
            )
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(interventions)").fetchall()
            }
            if "actor_type" not in columns:
                db.execute(
                    "ALTER TABLE interventions ADD COLUMN actor_type TEXT NOT NULL "
                    "DEFAULT 'UNAUTHENTICATED_DEMO'"
                )
            if "dispatch_status" not in columns:
                db.execute(
                    "ALTER TABLE interventions ADD COLUMN dispatch_status TEXT NOT NULL "
                    "DEFAULT 'NOT_APPLICABLE'"
                )
            if "selected_drivers" not in columns:
                db.execute(
                    "ALTER TABLE interventions ADD COLUMN selected_drivers INTEGER "
                    "NOT NULL DEFAULT 0"
                )
                db.execute(
                    "UPDATE interventions SET selected_drivers = eligible_drivers "
                    "WHERE selected_drivers = 0"
                )

    def approve(
        self,
        proposal: SafePauseProposal,
        approved_by: str = DEMO_ACTOR,
        actor_type: str = DEMO_ACTOR_TYPE,
    ) -> InterventionEvent:
        event = InterventionEvent(
            intervention_id=intervention_id_for(proposal.proposal_id),
            proposal_id=proposal.proposal_id,
            approved_at=datetime.now(UTC),
            approved_by=approved_by,
            actor_type=actor_type,
            status="SIMULATED",
            dispatch_status="NOT_APPLICABLE",
            proposal=proposal,
        )
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO interventions (
                    intervention_id, proposal_id, approved_at, approved_by,
                    actor_type, status, dispatch_status, zone_id, eligible_drivers,
                    selected_drivers, exposure_minutes_avoided,
                    net_platform_cost_vnd, proposal_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.intervention_id,
                    event.proposal_id,
                    event.approved_at.isoformat(),
                    event.approved_by,
                    event.actor_type,
                    event.status,
                    event.dispatch_status,
                    proposal.zone_id,
                    proposal.eligible_drivers,
                    proposal.selected_drivers,
                    proposal.exposure_minutes_avoided,
                    proposal.net_platform_cost_vnd,
                    json.dumps(proposal.to_dict(), ensure_ascii=False),
                ),
            )
            row = db.execute(
                "SELECT intervention_id, approved_at, approved_by, actor_type, status, dispatch_status "
                "FROM interventions WHERE proposal_id = ?",
                (proposal.proposal_id,),
            ).fetchone()
        return InterventionEvent(
            intervention_id=row["intervention_id"],
            proposal_id=event.proposal_id,
            approved_at=datetime.fromisoformat(row["approved_at"]).astimezone(UTC),
            approved_by=row["approved_by"],
            actor_type=row["actor_type"],
            status=row["status"],
            dispatch_status=row["dispatch_status"],
            proposal=proposal,
        )

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT intervention_id, approved_at, approved_by, actor_type, status,
                       dispatch_status, zone_id, eligible_drivers, selected_drivers,
                       exposure_minutes_avoided, net_platform_cost_vnd
                FROM interventions ORDER BY approved_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def protected_driver_count(self) -> int:
        with self._connect() as db:
            value = db.execute(
                "SELECT COALESCE(SUM(selected_drivers), 0) FROM interventions "
                "WHERE status IN ('SIMULATED', 'APPROVED')"
            ).fetchone()[0]
        return int(value)


class BigQueryInterventionAuditStore:
    """Durable audit for simulated demo decisions; it never dispatches commands."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.dataset = self.settings.dataset_path
        self._client_instance = None

    def _client(self):
        if self._client_instance is None:
            from google.cloud import bigquery

            self._client_instance = bigquery.Client(project=self.settings.project_id)
        return self._client_instance

    @staticmethod
    def _config(
        parameters: list,
        maximum_bytes_billed: int = MINIMUM_QUERY_BYTES_BILLED,
    ):
        from google.cloud import bigquery

        return bigquery.QueryJobConfig(
            query_parameters=parameters,
            maximum_bytes_billed=maximum_bytes_billed,
            labels={"app": "heatsafe", "component": "audit"},
        )

    def approve(
        self,
        proposal: SafePauseProposal,
        approved_by: str = DEMO_ACTOR,
        actor_type: str = DEMO_ACTOR_TYPE,
    ) -> InterventionEvent:
        from google.cloud import bigquery

        intervention_id = intervention_id_for(proposal.proposal_id)
        approved_at = datetime.now(UTC)
        query = f"""
        BEGIN TRANSACTION;
        MERGE `{self.dataset}.intervention_proposals` target
        USING (SELECT @proposal_id proposal_id) source
        ON target.proposal_id = source.proposal_id
          AND target.created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
        WHEN MATCHED AND target.simulation_run_id IS NULL
          AND @simulation_run_id IS NOT NULL THEN UPDATE SET
          proposal_json = PARSE_JSON(@proposal_json),
          scenario_id = @scenario_id,
          source_snapshot_id = @source_snapshot_id,
          simulation_run_id = @simulation_run_id,
          source_tick_id = @source_tick_id,
          expires_at = @expires_at
        WHEN NOT MATCHED THEN INSERT (
          proposal_id, created_at, zone_id, eligible_drivers, selected_drivers,
          exposure_minutes_avoided, net_platform_cost_vnd,
          projected_fulfillment_rate, within_guardrails, proposal_json,
          scenario_id, source_snapshot_id, simulation_run_id, source_tick_id,
          expires_at
        ) VALUES (
          @proposal_id, @created_at, @zone_id, @eligible_drivers, @selected_drivers,
          @exposure_minutes_avoided, @net_platform_cost_vnd,
          @projected_fulfillment_rate, @within_guardrails,
          PARSE_JSON(@proposal_json), @scenario_id, @source_snapshot_id,
          @simulation_run_id, @source_tick_id, @expires_at
        );
        MERGE `{self.dataset}.intervention_events` target
        USING (SELECT @intervention_id intervention_id) source
        ON target.intervention_id = source.intervention_id
          AND target.approved_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
        WHEN MATCHED AND target.simulation_run_id IS NULL
          AND @simulation_run_id IS NOT NULL THEN UPDATE SET
          scenario_id = @scenario_id,
          source_snapshot_id = @source_snapshot_id,
          simulation_run_id = @simulation_run_id,
          source_tick_id = @source_tick_id,
          expires_at = @expires_at
        WHEN NOT MATCHED THEN INSERT (
          intervention_id, proposal_id, approved_at, approved_by, actor_type,
          status, dispatch_status, zone_id, eligible_drivers, selected_drivers,
          exposure_minutes_avoided, net_platform_cost_vnd,
          scenario_id, source_snapshot_id, simulation_run_id, source_tick_id,
          expires_at
        ) VALUES (
          @intervention_id, @proposal_id, @approved_at, @approved_by, @actor_type,
          'SIMULATED', 'NOT_APPLICABLE', @zone_id, @eligible_drivers, @selected_drivers,
          @exposure_minutes_avoided, @net_platform_cost_vnd,
          @scenario_id, @source_snapshot_id, @simulation_run_id, @source_tick_id,
          @expires_at
        );
        COMMIT TRANSACTION;
        """
        parameters = [
            bigquery.ScalarQueryParameter("intervention_id", "STRING", intervention_id),
            bigquery.ScalarQueryParameter("proposal_id", "STRING", proposal.proposal_id),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", proposal.created_at),
            bigquery.ScalarQueryParameter("approved_at", "TIMESTAMP", approved_at),
            bigquery.ScalarQueryParameter("approved_by", "STRING", approved_by),
            bigquery.ScalarQueryParameter("actor_type", "STRING", actor_type),
            bigquery.ScalarQueryParameter(
                "scenario_id", "STRING", proposal.scenario_id
            ),
            bigquery.ScalarQueryParameter(
                "source_snapshot_id", "STRING", proposal.source_snapshot_id
            ),
            bigquery.ScalarQueryParameter(
                "simulation_run_id", "STRING", proposal.simulation_run_id
            ),
            bigquery.ScalarQueryParameter(
                "source_tick_id", "STRING", proposal.source_tick_id
            ),
            bigquery.ScalarQueryParameter(
                "expires_at", "TIMESTAMP", proposal.expires_at
            ),
            bigquery.ScalarQueryParameter("zone_id", "STRING", proposal.zone_id),
            bigquery.ScalarQueryParameter("eligible_drivers", "INT64", proposal.eligible_drivers),
            bigquery.ScalarQueryParameter("selected_drivers", "INT64", proposal.selected_drivers),
            bigquery.ScalarQueryParameter(
                "exposure_minutes_avoided", "INT64", proposal.exposure_minutes_avoided
            ),
            bigquery.ScalarQueryParameter(
                "net_platform_cost_vnd", "INT64", proposal.net_platform_cost_vnd
            ),
            bigquery.ScalarQueryParameter(
                "projected_fulfillment_rate", "FLOAT64", proposal.projected_fulfillment_rate
            ),
            bigquery.ScalarQueryParameter(
                "within_guardrails", "BOOL", proposal.within_guardrails
            ),
            bigquery.ScalarQueryParameter(
                "proposal_json", "STRING", json.dumps(proposal.to_dict(), ensure_ascii=False)
            ),
        ]
        self._client().query(
            query, job_config=self._config(parameters, maximum_bytes_billed=50_000_000)
        ).result()
        return InterventionEvent(
            intervention_id=intervention_id,
            proposal_id=proposal.proposal_id,
            approved_at=approved_at,
            approved_by=approved_by,
            actor_type=actor_type,
            status="SIMULATED",
            dispatch_status="NOT_APPLICABLE",
            proposal=proposal,
        )

    def list_recent(self, limit: int = 20) -> list[dict]:
        from google.cloud import bigquery

        limit = max(1, min(limit, 100))
        query = f"""
            SELECT intervention_id, approved_at, approved_by, actor_type, status,
                   dispatch_status, zone_id, eligible_drivers, selected_drivers,
                   exposure_minutes_avoided, net_platform_cost_vnd
            FROM `{self.dataset}.intervention_events`
            WHERE approved_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
            ORDER BY approved_at DESC LIMIT @limit
        """
        config = self._config(
            [bigquery.ScalarQueryParameter("limit", "INT64", limit)]
        )
        return [dict(row) for row in self._client().query(query, job_config=config).result()]

    def protected_driver_count(self) -> int:
        query = f"""
            SELECT COALESCE(SUM(COALESCE(selected_drivers, eligible_drivers)), 0) protected
            FROM `{self.dataset}.intervention_events`
            WHERE approved_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
              AND status IN ('SIMULATED', 'APPROVED')
        """
        row = next(iter(self._client().query(query, job_config=self._config([])).result()))
        return int(row.protected)


class HybridInterventionAuditStore:
    def __init__(self, mode: str | None = None, local_db_path: Path = DEFAULT_DB):
        self.settings = Settings.from_env()
        self.mode = (mode or self.settings.mode).lower()
        self.local = InterventionAuditStore(local_db_path)
        self.cloud = None
        self.backend = "local" if self.mode == "snapshot" else "cloud"
        self.fallback_reason: str | None = None

    def _cloud(self) -> BigQueryInterventionAuditStore:
        if self.cloud is None:
            self.cloud = BigQueryInterventionAuditStore(self.settings)
        return self.cloud

    def _call(self, method: str, *args, **kwargs):
        if self.mode == "snapshot":
            self.backend = "local"
            return getattr(self.local, method)(*args, **kwargs)
        try:
            value = getattr(self._cloud(), method)(*args, **kwargs)
            self.backend = "cloud"
            self.fallback_reason = None
            return value
        except Exception as exc:
            if self.mode == "cloud":
                raise
            self.backend = "local"
            self.fallback_reason = str(exc)
            return getattr(self.local, method)(*args, **kwargs)

    def approve(
        self,
        proposal: SafePauseProposal,
        approved_by: str = DEMO_ACTOR,
        actor_type: str = DEMO_ACTOR_TYPE,
    ) -> InterventionEvent:
        return self._call("approve", proposal, approved_by, actor_type)

    def list_recent(self, limit: int = 20) -> list[dict]:
        return self._call("list_recent", limit)

    def protected_driver_count(self) -> int:
        return self._call("protected_driver_count")
