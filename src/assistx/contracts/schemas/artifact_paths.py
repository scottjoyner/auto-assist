"""Artifact path contract (where work products live, shared with auto-ingest)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ArtifactPaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(...)
    base_dir: str = Field(..., description="Root directory for the task artifacts.")
    transcript_path: Optional[str] = None
    output_path: Optional[str] = None
    log_path: Optional[str] = None
    extra: dict = Field(default_factory=dict)
