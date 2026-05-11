"""Upsert metrics_daily for a (site, date) from parsed collector output.

CODE-SPEC §2.2.8 defines metrics_daily with a composite PK (site_id,
metric_date). Each collector returns a typed dataclass; this module merges
them into a single upsert that only touches the columns the collector
actually produced (others remain NULL or keep their prior value).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any
from uuid import UUID

from src.collectors.ga4 import GA4Daily
from src.collectors.gsc import GSCDaily
from src.db.client import get_db_connection


# Map our dataclass field names → metrics_daily column names
_GA4_COL_MAP = {
    "sessions": "sessions",
    "pageviews": "pageviews",
    "avg_duration_sec": "avg_duration_sec",
    "bounce_rate": "bounce_rate",
}
_GSC_COL_MAP = {
    "clicks": "gsc_clicks",
    "impressions": "gsc_impressions",
    "avg_position": "gsc_avg_position",
}


def _upsert(site_id: UUID, target_date: date, values: dict[str, Any]) -> None:
    if not values:
        return
    cols = list(values.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    set_clause = ", ".join(f"{c} = excluded.{c}" for c in cols)
    sql = (
        f"insert into metrics_daily (site_id, metric_date, {', '.join(cols)}) "
        f"values (%s, %s, {placeholders}) "
        f"on conflict (site_id, metric_date) do update set {set_clause}, "
        f"  computed_at = now()"
    )
    params = [str(site_id), target_date, *values.values()]
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)


def merge_ga4(site_id: UUID, target_date: date, daily: GA4Daily | None) -> None:
    if daily is None:
        # collector found no rows that day → write zeros so the dashboard
        # shows "real 0" not "NULL / never measured"
        zero = {col: 0 for col in _GA4_COL_MAP.values() if col != "bounce_rate"}
        zero["bounce_rate"] = None
        _upsert(site_id, target_date, zero)
        return
    vals: dict[str, Any] = {}
    d = asdict(daily)
    for field, col in _GA4_COL_MAP.items():
        vals[col] = d.get(field)
    _upsert(site_id, target_date, vals)


def merge_gsc(site_id: UUID, target_date: date, daily: GSCDaily | None) -> None:
    if daily is None:
        _upsert(
            site_id, target_date,
            {"gsc_clicks": 0, "gsc_impressions": 0, "gsc_avg_position": None},
        )
        return
    vals: dict[str, Any] = {}
    d = asdict(daily)
    for field, col in _GSC_COL_MAP.items():
        vals[col] = d.get(field)
    _upsert(site_id, target_date, vals)
