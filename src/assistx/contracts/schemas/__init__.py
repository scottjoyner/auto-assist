"""Pydantic v2 domain schemas for the unified fleet contract.

Each module is a self-contained contract that auto-assist (hub) owns and the
other repos import. See docs/LLD_UNIFIED_FLEET.md §1 / HLD §5.
"""

from .artifact_paths import ArtifactPaths
from .auto_ingest_memory_enrichment import (
    AutoIngestMemoryEnrichment,
    EnrichmentKind,
)
from .model_endpoint_registry import EndpointStatus, ModelEndpointRegistryEntry
from .node_registry import NodeRegistryEntry, NodeStatus
from .registered_speaker import RegisteredSpeaker, SpeakerStatus
from .repository_source_binding import (
    SOURCE_BINDING_SCHEMA,
    DirtyStateExpectation,
    RepositorySourceBinding,
)
from .task_authority import AuthorityMode, TaskAuthority
from .voice_auth_decision import AuthOutcome, VoiceAuthDecision

__all__ = [
    "RegisteredSpeaker",
    "SpeakerStatus",
    "VoiceAuthDecision",
    "AuthOutcome",
    "TaskAuthority",
    "AuthorityMode",
    "SOURCE_BINDING_SCHEMA",
    "DirtyStateExpectation",
    "RepositorySourceBinding",
    "ArtifactPaths",
    "NodeRegistryEntry",
    "NodeStatus",
    "ModelEndpointRegistryEntry",
    "EndpointStatus",
    "AutoIngestMemoryEnrichment",
    "EnrichmentKind",
]
