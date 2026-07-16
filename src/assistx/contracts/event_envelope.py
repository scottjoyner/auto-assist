"""Canonical event envelope + trace primitives for the unified fleet.

These pydantic v2 models are the single source of truth for cross-repo
events. Every repo (Sophia, auto-router, auto-assign, auto-ingest, lms,
hermes-agent) MUST emit/consume these instead of re-declaring their own
envelope. See docs/LLD_UNIFIED_FLEET.md §1 and §2 (Trace Observability).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuthState(str, Enum):
    """Speaker/actor authentication taxonomy (Sophia §5.6)."""

    AUTHENTICATED_SCOTT = "authenticated_scott"
    UNKNOWN_SPEAKER = "unknown_speaker"
    REGISTERED_USER_UNVERIFIED = "registered_user_unverified"
    ADMIN_VOICE_OVERRIDE = "admin_voice_override"
    REJECTED = "rejected"


class Actor(BaseModel):
    """Who/what produced an event."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(..., description="Stable identifier of the actor.")
    auth_state: AuthState = Field(..., description="Voice/identity auth outcome.")
    display_name: Optional[str] = None


class EventLink(BaseModel):
    """A typed relationship from one event to a domain entity."""

    model_config = ConfigDict(extra="forbid")

    rel: str = Field(..., description="Relationship name, e.g. FOR_TASK.")
    target_type: str = Field(..., description="Entity label, e.g. Task, Dispatch.")
    target_id: str = Field(..., description="Entity id the event is about.")


class EventEnvelope(BaseModel):
    """Canonical envelope every repo emits.

    ``correlation_id`` is REQUIRED (UUID) — a missing value fails validation and
    must be rejected at the boundary (HTTP 422). This directly addresses the
    trace-observability gap (LLD §2 / HLD G1).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(
        ..., description="Contract schema version, e.g. 2026-06-08.v1."
    )
    source_repo: str = Field(..., description="Originating repo, e.g. auto-assist.")
    event_type: str = Field(..., description="Domain event name, e.g. task.candidate.created.")
    correlation_id: str = Field(
        ...,
        description="Required UUID tying an event to a trace group.",
    )
    actor: Optional[Actor] = None
    ts: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Event timestamp (UTC).",
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    links: list[EventLink] = Field(default_factory=list)

    @field_validator("correlation_id")
    @classmethod
    def _valid_correlation_id(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except (ValueError, AttributeError, TypeError):
            raise ValueError(
                "correlation_id must be a valid UUID; trace linkage requires it."
            )
        return v


class TraceEvent(BaseModel):
    """A single observation within a trace group.

    Mirrors the Neo4j ``:TraceEvent`` node (LLD §4.2). ``correlation_id`` is the
    group key and ``links`` connect the event to the entities it touched.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"te_{uuid.uuid4().hex}")
    correlation_id: str = Field(...)
    event_type: str = Field(...)
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    links: list[EventLink] = Field(default_factory=list)

    @field_validator("correlation_id")
    @classmethod
    def _valid_correlation_id(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except (ValueError, AttributeError, TypeError):
            raise ValueError("correlation_id must be a valid UUID.")
        return v


class TraceGroup(BaseModel):
    """Aggregates trace events for one correlation_id and tracks current state."""

    model_config = ConfigDict(extra="forbid")

    correlation_id: str = Field(...)
    current_state: str = Field(default="PENDING")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_count: int = Field(default=0)

    @field_validator("correlation_id")
    @classmethod
    def _valid_correlation_id(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except (ValueError, AttributeError, TypeError):
            raise ValueError("correlation_id must be a valid UUID.")
        return v
