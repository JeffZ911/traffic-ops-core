"""Helpers for the ops_tasks human-action-item tracker.

Lets cron scripts auto-flag work that needs a human (source='auto')
and auto-resolve it when the condition clears — all keyed on a stable
(title, site_domain) pair so we never spam duplicate cards.

No schema change: dedup is done with a stable `title` per concern.
"""

from __future__ import annotations

from typing import Optional

from src.db.client import get_db_connection


def upsert_open_task(
    title: str,
    detail: str,
    *,
    priority: str = "normal",
    category: Optional[str] = None,
    site_domain: Optional[str] = None,
) -> str:
    """Ensure exactly one OPEN auto-card exists for (title, site_domain).

    - If an open card already exists → refresh its detail/priority
      (so e.g. the daily worklist updates in place).
    - Else insert a new one.
    Returns 'inserted' | 'updated'.
    """
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "select id from ops_tasks "
            "where title=%s and coalesce(site_domain,'')=coalesce(%s,'') "
            "and status='open' limit 1",
            (title, site_domain),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "update ops_tasks set detail=%s, priority=%s, category=%s, "
                "created_at=now() where id=%s",
                (detail, priority, category, row[0]),
            )
            return "updated"
        cur.execute(
            "insert into ops_tasks (title, detail, priority, category, site_domain, source) "
            "values (%s,%s,%s,%s,%s,'auto')",
            (title, detail, priority, category, site_domain),
        )
        return "inserted"


def resolve_open_task(title: str, *, site_domain: Optional[str] = None) -> int:
    """Auto-resolve (mark done) any open auto-card matching (title, site_domain).

    Used when the condition that opened it has cleared. Only touches
    source='auto' rows so we never auto-close a human-entered task.
    Returns the number of rows resolved.
    """
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "update ops_tasks set status='done', completed_at=now() "
            "where title=%s and coalesce(site_domain,'')=coalesce(%s,'') "
            "and status='open' and source='auto'",
            (title, site_domain),
        )
        return cur.rowcount


def refresh_daily_card(
    title: str,
    detail: str,
    *,
    priority: str = "normal",
    category: Optional[str] = None,
    site_domain: Optional[str] = None,
) -> str:
    """Same as upsert_open_task — kept as a named alias for daily-refresh
    callers so intent reads clearly at the call site."""
    return upsert_open_task(
        title, detail, priority=priority, category=category, site_domain=site_domain
    )
