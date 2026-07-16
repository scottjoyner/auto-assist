"""Task authority contract (who is allowed to execute a task)."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AuthorityMode(str, Enum):
    PAPERCLIP = "paperclip"
    DIRECT = "direct"
    AUTO = "auto"


class TaskAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(...)
    mode: AuthorityMode = Field(..., description="Execution authority backend.")
    owner: Optional[str] = Field(
        None, description="Node/agent id currently holding authority."
    )
    lease_expire: Optional[str] = None
    required_capabilities: list[str] = Field(default_factory=list)
