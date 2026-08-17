"""In-memory registry semantics for procedural-memory experiments."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .procedural_memory import ProceduralMemoryCandidate, eligible_for_promotion


@dataclass(frozen=True)
class ProceduralMemoryRecord:
    memory_id: str
    candidate: ProceduralMemoryCandidate
    active: bool = False
    superseded_by: str | None = None
    invalidated_reason: str | None = None


def activate_candidate(memory_id: str, candidate: ProceduralMemoryCandidate) -> ProceduralMemoryRecord:
    """Activate only candidates that have crossed the deterministic eligibility floor."""
    if not eligible_for_promotion(candidate):
        raise ValueError("candidate is not eligible for held-out promotion")
    return ProceduralMemoryRecord(memory_id=memory_id, candidate=candidate, active=True)


def invalidate(record: ProceduralMemoryRecord, reason: str) -> ProceduralMemoryRecord:
    if not reason.strip():
        raise ValueError("invalidation reason is required")
    return replace(record, active=False, invalidated_reason=reason)


def supersede(
    record: ProceduralMemoryRecord,
    replacement: ProceduralMemoryRecord,
) -> tuple[ProceduralMemoryRecord, ProceduralMemoryRecord]:
    if not replacement.active:
        raise ValueError("replacement must be active")
    if record.memory_id == replacement.memory_id:
        raise ValueError("record cannot supersede itself")
    old = replace(record, active=False, superseded_by=replacement.memory_id)
    return old, replacement
