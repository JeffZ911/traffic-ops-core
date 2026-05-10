from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AdCampaign(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    site_id: UUID
    fb_campaign_id: str
    name: Optional[str] = None
    status: Optional[str] = None         # active | paused | archived (not CHECK-enforced in DB)
    daily_budget: Optional[Decimal] = None
    objective: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    created_at: datetime
