"""Auto-ingest memory enrichment contract (ingest -> AssistX events)."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class EnrichmentKind(str, Enum):
    DIARIZE = "diarize"
    SUMMARIZE = "summarize"
    DASHCAM = "dashcam"
    KNOWLEDGE_VAULT = "knowledge_vault"
    TRANSCRIPT = "transcript"


class AutoIngestMemoryEnrichment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enrichment_id: str = Field(...)
    kind: EnrichmentKind = Field(...)
    source_ref: Optional[str] = Field(
        None, description="Capture/artifact the enrichment derives from."
    )
    linked_node_id: Optional[str] = Field(
        None, description="Neo4j node this evidence was linked to."
    )
    summary: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
