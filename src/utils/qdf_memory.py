"""QDF self-improving memory — the channel that lets learning FLOW.

The next-day retrospective (qdf_report) asks Gemini to analyse how each trend
page performed and produce concrete keyword-selection guidance. That guidance
is stored here and injected back into the NEXT trend generation
(keyword_gardener.run_trending), closing the loop:

    publish → measure (GSC) → AI retrospective + guidance → STORE (here)
       → next trend prompt reads guidance → better keywords → publish → …

Objective the AI optimises for (Jeff's core need): win IMPRESSIONS first on a
young site via the QDF freshness window, then convert to clicks.

No schema change: we reuse metrics_raw (append-only JSON store) with a distinct
payload key 'qdf_learning' — the same tactic the GSC collector uses for
indexing_coverage (the source CHECK constraint forbids a new source value).
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from src.collectors.base import store_raw
from src.db.client import get_db_connection


def save_qdf_learning(
    site_id: UUID | str, retrospective: str, guidance: str, *,
    model: str = "", summary: str = "",
) -> None:
    """Persist one day's AI retrospective + forward guidance (+ the full
    human-readable digest, which the dashboard panel renders)."""
    store_raw(
        site_id, "gsc", date.today(),
        {"qdf_learning": {
            "retrospective": retrospective[:4000],
            "guidance": guidance[:4000],
            "summary": summary[:8000],
            "model": model,
            "date": date.today().isoformat(),
        }},
    )


def latest_qdf_report(site_id: UUID | str) -> Optional[dict]:
    """Full latest qdf_learning payload (summary/retrospective/guidance/date)
    for the dashboard QDF panel. None if none stored yet."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select payload->'qdf_learning'
              from metrics_raw
             where site_id = %s and source = 'gsc' and payload ? 'qdf_learning'
             order by metric_date desc, id desc
             limit 1
            """,
            (str(site_id),),
        )
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def latest_qdf_guidance(site_id: UUID | str) -> Optional[str]:
    """Most-recent NON-EMPTY stored guidance for the site, or None. Injected
    into the next trend prompt so each generation builds on what performed.

    Must filter to non-empty: when the AI analyst is skipped (e.g. missing
    API key) the report still stores a row with guidance='' for the dashboard
    digest — without the filter, those daily empty rows permanently shadow
    the last real guidance (quvii's good 06-06 guidance was invisible)."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select payload->'qdf_learning'->>'guidance'
              from metrics_raw
             where site_id = %s and source = 'gsc'
               and payload ? 'qdf_learning'
               and coalesce(payload->'qdf_learning'->>'guidance', '') <> ''
             order by metric_date desc, id desc
             limit 1
            """,
            (str(site_id),),
        )
        row = cur.fetchone()
    return row[0] if row and row[0] else None
