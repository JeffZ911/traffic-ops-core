from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AgentRunStatus(str, Enum):
    started = "started"
    success = "success"
    failed = "failed"
    retried = "retried"


class AgentRun(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False)

    id: UUID
    site_id: UUID
    article_id: Optional[UUID] = None
    agent_name: str        # keyword_research | outline | writing | qa | image | publish
    status: AgentRunStatus
    input: Optional[dict[str, Any]] = None
    output: Optional[dict[str, Any]] = None
    error_msg: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[Decimal] = None
    duration_ms: Optional[int] = None
    model: Optional[str] = None
    created_at: datetime
