from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MetricSource(str, Enum):
    ga4 = "ga4"
    gsc = "gsc"
    adsense = "adsense"
    fb_ads = "fb_ads"
    cloudflare = "cloudflare"


class MetricRaw(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False)

    id: int                              # bigserial
    site_id: UUID
    source: MetricSource
    metric_date: date
    payload: dict[str, Any]
    fetched_at: datetime


class MetricDaily(BaseModel):
    model_config = ConfigDict(extra="ignore")

    site_id: UUID
    metric_date: date

    # Traffic
    sessions: Optional[int] = None
    pageviews: Optional[int] = None
    pv_per_session: Optional[Decimal] = None
    avg_duration_sec: Optional[int] = None
    bounce_rate: Optional[Decimal] = None

    # AdSense
    adsense_revenue_usd: Optional[Decimal] = None
    adsense_pageviews: Optional[int] = None
    adsense_impressions: Optional[int] = None
    adsense_ctr: Optional[Decimal] = None
    page_rpm_usd: Optional[Decimal] = None
    invalid_traffic_pct: Optional[Decimal] = None

    # FB Ads
    fb_spend_usd: Optional[Decimal] = None
    fb_clicks: Optional[int] = None
    fb_impressions: Optional[int] = None
    fb_cpc_usd: Optional[Decimal] = None
    fb_ctr: Optional[Decimal] = None
    fb_frequency: Optional[Decimal] = None

    # Derived
    ecpc_usd: Optional[Decimal] = None
    roi: Optional[Decimal] = None

    # SEO
    gsc_clicks: Optional[int] = None
    gsc_impressions: Optional[int] = None
    gsc_avg_position: Optional[Decimal] = None

    computed_at: datetime
