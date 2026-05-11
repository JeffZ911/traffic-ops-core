"""GA4 collector — pulls daily traffic metrics for one site.

API: Google Analytics Data API (analyticsdata.googleapis.com) — `runReport`.
Auth: user OAuth (src.utils.google_oauth.get_user_credentials).

Reads from sites.config:
  - site_slug → tells us which `<SLUG>_GA4_PROPERTY_ID` env var to read

Writes:
  - metrics_raw row (source='ga4', payload = the raw API response)
  - returns the parsed core metrics so the aggregator can update
    metrics_daily directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from src.collectors.base import site_env_prefix, store_raw
from src.utils.google_oauth import get_user_credentials


@dataclass
class GA4Daily:
    sessions: int
    pageviews: int
    avg_duration_sec: int
    bounce_rate: float | None


def _property_id_for(site_id: UUID) -> str:
    prefix = site_env_prefix(site_id)
    env_key = f"{prefix}_GA4_PROPERTY_ID"
    val = os.getenv(env_key)
    if not val:
        raise RuntimeError(f"{env_key} not set in env")
    return val


def fetch(site_id: UUID, target_date: date) -> tuple[dict[str, Any], GA4Daily | None]:
    """Pull one day of GA4 data. Returns (raw_payload, parsed)."""
    # Lazy import — only present in environments with the dep installed
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Metric, RunReportRequest,
    )

    creds = get_user_credentials()
    client = BetaAnalyticsDataClient(credentials=creds)
    property_id = _property_id_for(site_id)

    date_str = target_date.isoformat()
    req = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
        dimensions=[Dimension(name="date")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="screenPageViews"),
            Metric(name="averageSessionDuration"),
            Metric(name="bounceRate"),
        ],
    )
    resp = client.run_report(req)

    # Serialise the whole response for raw archival
    raw = {
        "property_id": property_id,
        "date": date_str,
        "rows": [
            {
                "dimensions": [dv.value for dv in row.dimension_values],
                "metrics": [mv.value for mv in row.metric_values],
            }
            for row in resp.rows
        ],
        "metric_headers": [h.name for h in resp.metric_headers],
        "row_count": resp.row_count,
    }
    store_raw(site_id, "ga4", target_date, raw)

    if not resp.rows:
        return raw, None  # legitimately no traffic that day

    r = resp.rows[0]
    vals = [mv.value for mv in r.metric_values]
    parsed = GA4Daily(
        sessions=int(float(vals[0] or 0)),
        pageviews=int(float(vals[1] or 0)),
        avg_duration_sec=int(float(vals[2] or 0)),
        bounce_rate=(float(vals[3]) if vals[3] not in ("", None) else None),
    )
    return raw, parsed
