"""Google Search Console collector — daily aggregate + per-query breakdown.

API: searchanalytics.query on https://www.googleapis.com/webmasters/v3
Auth: user OAuth only (service accounts cannot read Search Console).

Two queries per day:
  1. Top-level: dimensions=['date'] → site-wide clicks / impressions / position
  2. Per-query: dimensions=['query'] → top 100 search queries (stored in raw
     payload only; metrics_daily keeps the aggregate)

The site is identified by its domain (the property must be configured in GSC
either as a URL prefix `https://ntecodex.com/` or as a Domain property
`sc-domain:ntecodex.com`; this collector tries both forms).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from src.collectors.base import site_config, store_raw
from src.utils.google_oauth import get_user_credentials


@dataclass
class GSCDaily:
    clicks: int
    impressions: int
    avg_position: float | None


def _site_property(site_id: UUID) -> str:
    """Authoritative domain is `sites.domain`; sites.config.site_name is the
    human label ("NTE Codex"), not a hostname — don't read it here."""
    from src.db.client import get_db_connection
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select domain from sites where id = %s", (str(site_id),))
        row = cur.fetchone()
        if not row or not row[0]:
            raise RuntimeError(f"sites.domain missing for site_id {site_id}")
        domain = row[0]
    # Prefer Domain-property form (covers all subdomains)
    return f"sc-domain:{domain}"


def _try_query(svc, site_url: str, request_body: dict) -> dict:
    """Run a searchanalytics.query, falling back from Domain to URL prefix."""
    try:
        return svc.searchanalytics().query(
            siteUrl=site_url, body=request_body
        ).execute()
    except Exception as e:
        msg = str(e)
        if "404" not in msg and "permission" not in msg.lower():
            raise
        # Try URL-prefix form
        prefix_url = "https://" + site_url.replace("sc-domain:", "") + "/"
        return svc.searchanalytics().query(
            siteUrl=prefix_url, body=request_body
        ).execute()


def fetch(site_id: UUID, target_date: date) -> tuple[dict[str, Any], GSCDaily | None]:
    from googleapiclient.discovery import build

    creds = get_user_credentials()
    svc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    site_url = _site_property(site_id)
    date_str = target_date.isoformat()

    # 1) Aggregate
    agg_body = {
        "startDate": date_str, "endDate": date_str,
        "dimensions": ["date"],
        "rowLimit": 1,
    }
    agg = _try_query(svc, site_url, agg_body)

    # 2) Per-query (top 100, used for keyword expansion later)
    top_body = {
        "startDate": date_str, "endDate": date_str,
        "dimensions": ["query"],
        "rowLimit": 100,
    }
    try:
        top = _try_query(svc, site_url, top_body)
    except Exception as e:
        top = {"error": str(e)[:300]}

    raw = {
        "site_url": site_url,
        "date": date_str,
        "aggregate": agg,
        "top_queries": top,
    }
    store_raw(site_id, "gsc", target_date, raw)

    rows = agg.get("rows", [])
    if not rows:
        return raw, None
    r = rows[0]
    parsed = GSCDaily(
        clicks=int(r.get("clicks", 0)),
        impressions=int(r.get("impressions", 0)),
        avg_position=float(r["position"]) if "position" in r else None,
    )
    return raw, parsed


def fetch_range(
    site_id: UUID, start_date: date, end_date: date
) -> dict[date, GSCDaily]:
    """Fetch clicks/impressions/position for every date in [start, end] in a
    SINGLE searchanalytics query (dimensions=['date']).

    GSC finalizes performance data 2-3 days late, so a daily collector that
    only queries 'yesterday' always hits the empty lag window and stores zeros
    that never get corrected. Re-fetching a trailing window each run lets the
    late-arriving real numbers overwrite those zeros. Only dates GSC actually
    returns are included — callers should NOT write zeros for missing dates
    (a missing date = "not settled yet", not "zero traffic").
    """
    from googleapiclient.discovery import build

    creds = get_user_credentials()
    svc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    site_url = _site_property(site_id)

    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["date"],
        "rowLimit": 1000,
    }
    resp = _try_query(svc, site_url, body)

    out: dict[date, GSCDaily] = {}
    for r in resp.get("rows", []):
        d = date.fromisoformat(r["keys"][0])
        out[d] = GSCDaily(
            clicks=int(r.get("clicks", 0)),
            impressions=int(r.get("impressions", 0)),
            avg_position=float(r["position"]) if "position" in r else None,
        )
    return out
