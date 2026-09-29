"""Speaker identity registry endpoints.

Sophia's voice-agent performs biometric enrollment/verification (ECAPA
embeddings over .caf uploads from kipnerter-ios). When a verification
succeeds, callers register the verdict here so AssistX authorization policy
can trust graph-backed speaker identity instead of self-claimed strings.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException


def build_voice_identity_router(
    neo_factory: Callable[[], Any],
    auth_dependency: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/voice-identity", tags=["voice-identity"])

    @router.get("/speakers")
    def list_speakers(user: Any = None) -> dict:
        from .voice_profile import list_speakers as _list

        neo = neo_factory()
        try:
            return {"speakers": _list(neo)}
        finally:
            neo.close()

    @router.post("/verifications")
    def register_verification(payload: dict, user: Any = None) -> dict:
        """Record a successful verification from the voice-agent.

        Body: {source_id, confidence?, source?, metadata?}
        """
        from .voice_profile import upsert_speaker_verification

        source_id = str(payload.get("source_id") or "").strip()
        if not source_id:
            raise HTTPException(status_code=422, detail="source_id is required")
        neo = neo_factory()
        try:
            record = upsert_speaker_verification(
                neo,
                source=str(payload.get("source") or "sophia-voice-agent"),
                source_id=source_id,
                confidence=payload.get("confidence"),
                metadata=payload.get("metadata") or {},
            )
            return {"registered": True, "speaker": record}
        finally:
            neo.close()

    @router.get("/trust")
    def trust(source_id: str, source: str = "sophia-voice-agent", user: Any = None) -> dict:
        from .voice_profile import is_trusted_speaker

        neo = neo_factory()
        try:
            trusted = is_trusted_speaker(neo, source=source, source_id=source_id)
            return {"source_id": source_id, "trusted": trusted}
        finally:
            neo.close()

    return router
