from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class Image(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    site_id: UUID
    article_id: Optional[UUID] = None
    prompt: Optional[str] = None
    url: str
    alt_text: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    aspect_ratio: Optional[str] = None
    cost_usd: Optional[Decimal] = None
    created_at: datetime
