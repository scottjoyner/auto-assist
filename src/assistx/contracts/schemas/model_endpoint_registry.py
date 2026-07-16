"""Model endpoint registry contract (routing/placement truth)."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class EndpointStatus(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class ModelEndpointRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_id: str = Field(...)
    model_name: str = Field(...)
    base_url: str = Field(...)
    lane: Optional[str] = None
    status: EndpointStatus = EndpointStatus.UNKNOWN
    capabilities: list[str] = Field(default_factory=list)
    last_probed: Optional[str] = None
