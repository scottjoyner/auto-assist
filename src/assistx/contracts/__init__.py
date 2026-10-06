"""Shared contract package for the unified fleet platform.

Owned by auto-assist (the hub). Other repos import from here instead of
re-declaring EventEnvelope / Lane / TraceEvent. See docs/LLD_UNIFIED_FLEET.md §1.
"""

from .version import SCHEMA_VERSION
from .event_envelope import (
    EventEnvelope,
    TraceEvent,
    TraceGroup,
    Actor,
    AuthState,
    EventLink,
)
from .schemas.repository_source_binding import (
    RepositorySourceBinding,
    DirtyStateExpectation,
    SourceBindingState,
)
from .repository_source_verifier import (
    ObservedSource,
    SourceBindingVerification,
    build_source_binding,
    observe_source_workspace,
    verify_repository_source,
    verify_source_workspace,
)

__all__ = [
    "SCHEMA_VERSION",
    "EventEnvelope",
    "TraceEvent",
    "TraceGroup",
    "Actor",
    "AuthState",
    "EventLink",
    "RepositorySourceBinding",
    "DirtyStateExpectation",
    "SourceBindingState",
    "ObservedSource",
    "SourceBindingVerification",
    "build_source_binding",
    "observe_source_workspace",
    "verify_repository_source",
    "verify_source_workspace",
]
