from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class Modality(str, Enum):
    text = "text"
    image = "image"
    embedding = "embedding"


class ModelStatus(str, Enum):
    preview = "preview"
    active = "active"
    deprecated = "deprecated"


class ModelCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False, protected_namespaces=())

    id: UUID
    provider: str                              # gemini | openai | anthropic
    model_id: str
    display_name: str
    modality: Modality
    task_types: list[str]
    tier: Optional[str] = None                 # pro | flash | flash-lite
    input_cost_per_1m: Optional[Decimal] = None
    output_cost_per_1m: Optional[Decimal] = None
    per_image_cost: Optional[Decimal] = None
    context_window: Optional[int] = None
    supports_json_mode: bool = False
    status: ModelStatus = ModelStatus.active
    is_recommended: bool = False
    released_at: Optional[date] = None
    deprecate_at: Optional[date] = None
    last_verified_at: Optional[datetime] = None
    last_verify_error: Optional[str] = None
    notes: Optional[str] = None
    added_at: Optional[datetime] = None
