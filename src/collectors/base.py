"""Shared infrastructure for all data collectors (CODE-SPEC §4.1)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID

from src.db.client import get_db_connection


def store_raw(
    site_id: UUID,
    source: str,
    target_date: date,
    payload: dict[str, Any],
) -> None:
    """Append one row to metrics_raw. Idempotent? No — multiple runs of the
    same (source, date) produce multiple rows. Aggregation step dedupes by
    using the latest row per (site_id, source, metric_date)."""
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into metrics_raw (site_id, source, metric_date, payload)
            values (%s, %s, %s, %s::jsonb)
            """,
            (str(site_id), source, target_date, json.dumps(payload)),
        )


def site_config(site_id: UUID) -> dict[str, Any]:
    """Read sites.config jsonb."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select config from sites where id = %s", (str(site_id),))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"site_id {site_id} not in sites")
        return row[0] or {}


def site_env_prefix(site_id: UUID) -> str:
    """Look up the env-var prefix for this site (e.g. 'NTECODEX')."""
    cfg = site_config(site_id)
    slug = cfg.get("site_slug")
    if not slug:
        raise RuntimeError(f"sites.config.site_slug missing for {site_id}")
    return slug.upper().replace("-", "_")
