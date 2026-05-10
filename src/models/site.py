from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SiteStatus(str, Enum):
    active = "active"
    paused = "paused"
    archived = "archived"


class Site(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False)

    id: UUID
    domain: str
    site_name: str
    status: SiteStatus = SiteStatus.active
    config: dict[str, Any] = Field(default_factory=dict)
    owner_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
