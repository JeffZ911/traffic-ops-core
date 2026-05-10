from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class KeywordStatus(str, Enum):
    planned = "planned"
    in_progress = "in_progress"
    completed = "completed"
    skipped = "skipped"
    archived = "archived"


class Keyword(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False)

    id: UUID
    site_id: UUID
    keyword: str
    intent: Optional[str] = None              # informational | comparison | how-to | list
    search_volume: Optional[int] = None
    competition: Optional[Decimal] = None     # numeric(3,2)
    status: KeywordStatus = KeywordStatus.planned
    priority_score: Optional[Decimal] = None  # numeric(5,2)
    source: Optional[str] = None              # initial_research | gsc_expansion | manual | competitor_gap
    cluster_id: Optional[UUID] = None
    notes: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
