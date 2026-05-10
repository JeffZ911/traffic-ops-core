from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AgentRunSummary(BaseModel):
    """Daily aggregate retained after agent_runs cleanup."""

    model_config = ConfigDict(extra="ignore")

    site_id: UUID
    summary_date: date
    agent_name: str
    total_runs: int
    success_count: int
    failure_count: int
    total_tokens_in: int      # bigint
    total_tokens_out: int
    total_cost_usd: Decimal
    avg_duration_ms: Optional[int] = None
