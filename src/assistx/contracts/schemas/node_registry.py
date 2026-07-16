"""Node (fleet) registry contract (canonical fleet truth owned by AssistX)."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class NodeStatus(str, Enum):
    UP = "up"
    DOWN = "down"
    DRAINING = "draining"
    UNKNOWN = "unknown"


class NodeRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(...)
    host: Optional[str] = None
    capabilities: list[str] = Field(default_factory=list)
    status: NodeStatus = NodeStatus.UNKNOWN
    last_heartbeat: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
