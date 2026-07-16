"""Registered speaker identity contract (shared with Sophia + auto-ingest)."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SpeakerStatus(str, Enum):
    UNKNOWN = "unknown"
    REGISTERED = "registered"
    VERIFIED = "verified"
    REJECTED = "rejected"


class RegisteredSpeaker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_id: str = Field(..., description="Stable global speaker id.")
    name: Optional[str] = None
    status: SpeakerStatus = SpeakerStatus.UNKNOWN
    voiceprint_ref: Optional[str] = Field(
        None, description="Reference to stored voiceprint embedding."
    )
    is_me: bool = False
    metadata: dict = Field(default_factory=dict)
