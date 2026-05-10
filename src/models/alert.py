from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AlertLevel(str, Enum):
    critical = "critical"
    warning = "warning"
    info = "info"


class Alert(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False)

    id: UUID
    site_id: Optional[UUID] = None       # nullable: system-level alerts
    level: AlertLevel
    category: str                        # e.g. adsense_invalid_traffic | fb_overspend | qa_failure
    title: str
    message: str
    context: Optional[dict[str, Any]] = None
    acknowledged: bool = False
    acknowledged_by: Optional[UUID] = None
    acknowledged_at: Optional[datetime] = None
    created_at: datetime
