from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ArticleStatus(str, Enum):
    draft = "draft"
    writing = "writing"
    qa_pending = "qa_pending"
    qa_failed = "qa_failed"
    qa_passed = "qa_passed"
    published = "published"
    archived = "archived"
    failed = "failed"


class ArticleType(str, Enum):
    build = "build"
    tier_list = "tier_list"
    boss_guide = "boss_guide"
    reroll = "reroll"
    character_db = "character_db"
    weapon_db = "weapon_db"
    news = "news"
    faq = "faq"
    comparison = "comparison"


class Article(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False)

    id: UUID
    site_id: UUID
    slug: str
    title: Optional[str] = None
    article_type: Optional[ArticleType] = None
    outline: Optional[dict[str, Any]] = None
    content_md: Optional[str] = None
    word_count: Optional[int] = None
    status: ArticleStatus = ArticleStatus.draft
    qa_score: Optional[Decimal] = None        # numeric(3,1)
    qa_attempts: int = 0
    qa_feedback: Optional[dict[str, Any]] = None
    published_url: Optional[str] = None
    published_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    total_tokens: int = 0
    total_cost_usd: Decimal = Field(default=Decimal("0"))  # numeric(8,4)
    created_at: datetime
    updated_at: datetime


class ArticleKeyword(BaseModel):
    """Join row between articles and keywords (article_keywords table)."""

    model_config = ConfigDict(extra="ignore")

    article_id: UUID
    keyword_id: UUID
    is_primary: bool = False
