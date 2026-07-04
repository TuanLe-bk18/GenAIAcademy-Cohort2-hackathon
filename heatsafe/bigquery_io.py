from __future__ import annotations

import uuid


def merge_rows(
    client,
    table_id: str,
    rows: list[dict],
    schema: list,
    key_fields: list[str],
    *,
    target_predicate: str | None = None,
    maximum_bytes_billed: int = 250_000_000,
) -> None:
    """Atomically upsert a small batch through a short-lived staging table."""
    if not rows:
        return
    from google.cloud import bigquery

    staging_id = f"{table_id}__staging_{uuid.uuid4().hex}"
    try:
        config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        client.load_table_from_json(rows, staging_id, job_config=config).result()
        columns = [field.name for field in schema]
        match_parts = [f"target.{name} = source.{name}" for name in key_fields]
        if target_predicate:
            match_parts.append(f"({target_predicate})")
        match = " AND ".join(match_parts)
        updates = ", ".join(
            f"target.{name} = source.{name}" for name in columns if name not in key_fields
        )
        names = ", ".join(columns)
        values = ", ".join(f"source.{name}" for name in columns)
        query = f"""
            MERGE `{table_id}` target
            USING `{staging_id}` source
            ON {match}
            WHEN MATCHED THEN UPDATE SET {updates}
            WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({values})
        """
        query_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=maximum_bytes_billed,
            labels={"app": "heatsafe", "component": "small_batch_merge"},
        )
        client.query(query, job_config=query_config).result()
    finally:
        client.delete_table(staging_id, not_found_ok=True)
