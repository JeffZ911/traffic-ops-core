"""Pydantic model tests — pure-Python, no DB."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models import (
    AdCampaign,
    AgentRun,
    AgentRunStatus,
    AgentRunSummary,
    Alert,
    AlertLevel,
    Article,
    ArticleKeyword,
    ArticleStatus,
    ArticleType,
    DailyReport,
    Image,
    Keyword,
    KeywordStatus,
    MetricDaily,
    MetricRaw,
    MetricSource,
    Modality,
    ModelCatalogEntry,
    ModelStatus,
    Site,
    SiteStatus,
)


NOW = datetime.now(timezone.utc)
TODAY = date.today()
SITE_ID = uuid4()
ARTICLE_ID = uuid4()
KEYWORD_ID = uuid4()


# ---------------------------------------------------------------------------
# Construction from dicts (the read-from-DB happy path)
# ---------------------------------------------------------------------------

def test_site_from_dict():
    s = Site.model_validate({
        "id": uuid4(), "domain": "x.com", "site_name": "X",
        "status": "active", "config": {"k": 1},
        "created_at": NOW, "updated_at": NOW,
    })
    assert s.status is SiteStatus.active
    assert s.config == {"k": 1}


def test_keyword_from_dict():
    k = Keyword.model_validate({
        "id": uuid4(), "site_id": SITE_ID, "keyword": "best build",
        "competition": "0.42", "status": "in_progress",
        "created_at": NOW, "updated_at": NOW,
    })
    assert k.status is KeywordStatus.in_progress
    assert k.competition == Decimal("0.42")


def test_article_from_dict():
    a = Article.model_validate({
        "id": ARTICLE_ID, "site_id": SITE_ID, "slug": "x",
        "article_type": "build", "status": "qa_pending",
        "qa_score": "8.5", "total_cost_usd": "0.1234",
        "created_at": NOW, "updated_at": NOW,
    })
    assert a.article_type is ArticleType.build
    assert a.status is ArticleStatus.qa_pending
    assert a.qa_score == Decimal("8.5")


def test_article_keyword_from_dict():
    ak = ArticleKeyword.model_validate({
        "article_id": ARTICLE_ID, "keyword_id": KEYWORD_ID, "is_primary": True
    })
    assert ak.is_primary is True


def test_agent_run_from_dict():
    r = AgentRun.model_validate({
        "id": uuid4(), "site_id": SITE_ID,
        "agent_name": "writing", "status": "started",
        "created_at": NOW,
    })
    assert r.status is AgentRunStatus.started


def test_image_from_dict():
    img = Image.model_validate({
        "id": uuid4(), "site_id": SITE_ID,
        "url": "/img/x.webp", "created_at": NOW,
    })
    assert img.url == "/img/x.webp"


def test_metric_raw_from_dict():
    m = MetricRaw.model_validate({
        "id": 1, "site_id": SITE_ID, "source": "ga4",
        "metric_date": TODAY, "payload": {"x": 1}, "fetched_at": NOW,
    })
    assert m.source is MetricSource.ga4


def test_metric_daily_from_dict():
    m = MetricDaily.model_validate({
        "site_id": SITE_ID, "metric_date": TODAY,
        "sessions": 100, "pageviews": 250, "computed_at": NOW,
    })
    assert m.sessions == 100
    assert m.fb_clicks is None  # optional, unset


def test_ad_campaign_from_dict():
    c = AdCampaign.model_validate({
        "id": uuid4(), "site_id": SITE_ID,
        "fb_campaign_id": "fb_123", "created_at": NOW,
    })
    assert c.fb_campaign_id == "fb_123"


def test_alert_from_dict():
    a = Alert.model_validate({
        "id": uuid4(), "site_id": None,
        "level": "critical", "category": "qa_failure",
        "title": "T", "message": "M", "created_at": NOW,
    })
    assert a.level is AlertLevel.critical
    assert a.site_id is None  # system-level


def test_agent_run_summary_from_dict():
    s = AgentRunSummary.model_validate({
        "site_id": SITE_ID, "summary_date": TODAY, "agent_name": "writing",
        "total_runs": 10, "success_count": 9, "failure_count": 1,
        "total_tokens_in": 1000, "total_tokens_out": 500,
        "total_cost_usd": "0.12",
    })
    assert s.success_count == 9


def test_daily_report_from_dict():
    r = DailyReport.model_validate({
        "id": uuid4(), "site_id": SITE_ID, "report_date": TODAY,
        "markdown": "# x", "sent_to": ["dashboard", "email"],
        "created_at": NOW,
    })
    assert r.sent_to == ["dashboard", "email"]


def test_model_catalog_from_dict():
    m = ModelCatalogEntry.model_validate({
        "id": uuid4(), "provider": "gemini", "model_id": "gemini-3-flash-preview",
        "display_name": "Flash", "modality": "text",
        "task_types": ["writing"], "status": "preview",
    })
    assert m.modality is Modality.text
    assert m.status is ModelStatus.preview


# ---------------------------------------------------------------------------
# Invalid enum values must be rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model_cls,base,bad_field,bad_value", [
    (Site, {"id": uuid4(), "domain": "x", "site_name": "X",
            "created_at": NOW, "updated_at": NOW}, "status", "deleted"),
    (Keyword, {"id": uuid4(), "site_id": SITE_ID, "keyword": "k",
               "created_at": NOW, "updated_at": NOW}, "status", "used"),  # 'used' was REJECTED in spec
    (Article, {"id": ARTICLE_ID, "site_id": SITE_ID, "slug": "x",
               "created_at": NOW, "updated_at": NOW}, "status", "halfway"),
    (Article, {"id": ARTICLE_ID, "site_id": SITE_ID, "slug": "x",
               "created_at": NOW, "updated_at": NOW}, "article_type", "rant"),
    (AgentRun, {"id": uuid4(), "site_id": SITE_ID, "agent_name": "writing",
                "created_at": NOW}, "status", "success_partial"),
    (Alert, {"id": uuid4(), "category": "x", "title": "t",
             "message": "m", "created_at": NOW}, "level", "trivial"),
    (MetricRaw, {"id": 1, "site_id": SITE_ID, "metric_date": TODAY,
                 "payload": {}, "fetched_at": NOW}, "source", "tiktok"),
    (ModelCatalogEntry, {"id": uuid4(), "provider": "gemini",
                         "model_id": "x", "display_name": "X",
                         "task_types": []}, "modality", "audio"),
    (ModelCatalogEntry, {"id": uuid4(), "provider": "gemini",
                         "model_id": "x", "display_name": "X",
                         "modality": "text", "task_types": []}, "status", "alpha"),
])
def test_invalid_enum_rejected(model_cls, base, bad_field, bad_value):
    payload = {**base, bad_field: bad_value}
    with pytest.raises(ValidationError):
        model_cls.model_validate(payload)


# ---------------------------------------------------------------------------
# Defaults on fields the DB also defaults
# ---------------------------------------------------------------------------

def test_article_defaults():
    a = Article.model_validate({
        "id": ARTICLE_ID, "site_id": SITE_ID, "slug": "x",
        "created_at": NOW, "updated_at": NOW,
    })
    assert a.status is ArticleStatus.draft
    assert a.qa_attempts == 0
    assert a.total_tokens == 0
    assert a.total_cost_usd == Decimal("0")


def test_site_defaults():
    s = Site.model_validate({
        "id": uuid4(), "domain": "x", "site_name": "X",
        "created_at": NOW, "updated_at": NOW,
    })
    assert s.status is SiteStatus.active
    assert s.config == {}
