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


def get_site_value(site_id: UUID, key: str) -> str | None:
    """Generic per-site value lookup with two-tier fallback.

    Resolution order (matches the Dashboard's "edit it inline" UX):
      1. ``sites.config.<key>`` — operator can edit via /sites page,
         takes effect immediately on the next request.
      2. ``<SLUG>_<KEY_UPPER>`` env var — legacy GitHub-secret path,
         for values that genuinely must stay outside the DB.

    Returns None when neither source has the value, so callers can
    decide whether that's fatal or skippable.

    Example:
        prop = get_site_value(site_id, "ga4_property_id")
        # → sites.config.ga4_property_id, else env NTECODEX_GA4_PROPERTY_ID
    """
    import os
    cfg = site_config(site_id)
    db_val = cfg.get(key)
    if db_val:
        return str(db_val)
    slug = cfg.get("site_slug") or ""
    if not slug:
        return None
    env_key = f"{slug.upper().replace('-', '_')}_{key.upper()}"
    return os.getenv(env_key) or None
