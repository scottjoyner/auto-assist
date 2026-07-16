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

__all__ = [
    "SCHEMA_VERSION",
    "EventEnvelope",
    "TraceEvent",
    "TraceGroup",
    "Actor",
    "AuthState",
    "EventLink",
]
