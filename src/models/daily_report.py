from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DailyReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    site_id: UUID
    report_date: date
    markdown: str
    ai_summary: Optional[str] = None
    data_snapshot: Optional[dict[str, Any]] = None
    sent_to: Optional[list[str]] = None       # ['dashboard'] / ['email'] / both
    created_at: datetime
