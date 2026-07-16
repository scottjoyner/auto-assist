"""Pydantic v2 domain schemas for the unified fleet contract.

Each module is a self-contained contract that auto-assist (hub) owns and the
other repos import. See docs/LLD_UNIFIED_FLEET.md §1 / HLD §5.
"""

from .registered_speaker import RegisteredSpeaker, SpeakerStatus
from .voice_auth_decision import VoiceAuthDecision, AuthOutcome
from .task_authority import TaskAuthority, AuthorityMode
from .artifact_paths import ArtifactPaths
from .node_registry import NodeRegistryEntry, NodeStatus
from .model_endpoint_registry import ModelEndpointRegistryEntry, EndpointStatus
from .auto_ingest_memory_enrichment import (
    AutoIngestMemoryEnrichment,
    EnrichmentKind,
)

__all__ = [
    "RegisteredSpeaker",
    "SpeakerStatus",
    "VoiceAuthDecision",
    "AuthOutcome",
    "TaskAuthority",
    "AuthorityMode",
    "ArtifactPaths",
    "NodeRegistryEntry",
    "NodeStatus",
    "ModelEndpointRegistryEntry",
    "EndpointStatus",
    "AutoIngestMemoryEnrichment",
    "EnrichmentKind",
]
