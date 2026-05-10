"""Pydantic models mirroring the 13 tables in src/db/migrations/001_initial_schema.sql."""

from src.models.ad_campaign import AdCampaign
from src.models.agent_run import AgentRun, AgentRunStatus
from src.models.agent_run_summary import AgentRunSummary
from src.models.alert import Alert, AlertLevel
from src.models.article import Article, ArticleKeyword, ArticleStatus, ArticleType
from src.models.daily_report import DailyReport
from src.models.image import Image
from src.models.keyword import Keyword, KeywordStatus
from src.models.metric import MetricDaily, MetricRaw, MetricSource
from src.models.model_catalog import Modality, ModelCatalogEntry, ModelStatus
from src.models.site import Site, SiteStatus

__all__ = [
    # tables
    "Site",
    "Keyword",
    "Article",
    "ArticleKeyword",
    "AgentRun",
    "Image",
    "MetricRaw",
    "MetricDaily",
    "AdCampaign",
    "Alert",
    "AgentRunSummary",
    "DailyReport",
    "ModelCatalogEntry",
    # enums
    "SiteStatus",
    "KeywordStatus",
    "ArticleStatus",
    "ArticleType",
    "AgentRunStatus",
    "MetricSource",
    "AlertLevel",
    "Modality",
    "ModelStatus",
]
