"""Voice authentication decision contract (Sophia emits, AssistX consumes)."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AuthOutcome(str, Enum):
    AUTHENTICATED_SCOTT = "authenticated_scott"
    UNKNOWN_SPEAKER = "unknown_speaker"
    REGISTERED_USER_UNVERIFIED = "registered_user_unverified"
    ADMIN_VOICE_OVERRIDE = "admin_voice_override"
    REJECTED = "rejected"


class VoiceAuthDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_id: Optional[str] = None
    outcome: AuthOutcome = Field(..., description="Auth taxonomy result.")
    score: float = Field(..., ge=0.0, le=1.0, description="Voiceprint confidence.")
    threshold: float = Field(0.60, ge=0.0, le=1.0)
    decided_at: Optional[str] = None
    reason: Optional[str] = None
