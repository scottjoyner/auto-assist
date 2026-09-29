"""Speaker voice profiles: enrollment, embedding, and verification.

Goal: replace self-claimed ``speaker_identity`` strings with graph-backed,
biometrically matched speaker identity so voice authorization stops failing.

Auth layering:
- Tailscale / SSO / basic auth establishes *who is calling the API*.
- Voice prints establish *who is speaking in the audio*.
A caller may be a trusted operator while the speaker in the audio is unknown;
both facts are recorded independently.

Embedders are pluggable via env ``ASSISTX_VOICE_EMBEDDER``:
``auto`` (default) prefers resemblyzer when installed, else the offline
MFCC-statistics embedder; ``mfcc`` and ``resemblyzer`` force a choice.
"""

from __future__ import annotations

import hashlib
import json
import time
import os
import wave
import io
import math
from dataclasses import dataclass
from typing import Any



class EmbedderError(RuntimeError):
    """Raised when audio cannot be embedded."""


@dataclass(frozen=True)
class Embedding:
    vector: tuple[float, ...]
    embedder: str
    quality: float = 1.0


def cosine_similarity(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return max(-1.0, min(1.0, num / (na * nb)))


DEFAULT_MATCH_THRESHOLD = float(os.getenv("ASSISTX_VOICE_MATCH_THRESHOLD", "0.72"))


# ---------------------------------------------------------------------------
# Decoding helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Graph-backed speaker registry (AssistX side)
#
# Sophia's voice-agent owns the biometric pipeline (SpeechBrain ECAPA
# embeddings, enrollment, adaptive thresholds - see its auth/voiceprint_graph).
# AssistX consumes its verdicts: when a caller presents a verification receipt
# for a user_id, we upsert an authorization-grade Speaker node in the assistx
# graph so downstream policy can answer "is this speaker trusted?" without
# re-running inference or trusting self-claimed strings.
# ---------------------------------------------------------------------------


def _speaker_key(source: str, source_id: str) -> str:
    raw = f"{source}:{source_id}".lower()
    return hashlib.sha256(raw.encode()).hexdigest()[:32]




def upsert_speaker_verification(neo, *, source: str, source_id: str,
                                confidence: float | None = None,
                                metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record a successful third-party speaker verification in the graph."""
    key = _speaker_key(source, source_id)
    conf = max(0.0, min(1.0, float(confidence if confidence is not None else 1.0)))
    with neo._session() as s:
        rec = s.run(
            """
            MERGE (s:Speaker {key:$key})
            ON CREATE SET s.source=$source, s.source_id=$source_id,
                          s.created_at_ts=$now, s.verify_count=0, s.avg_confidence=0.0
            SET s.last_verified_at_ts=$now,
                s.display_name = coalesce(s.display_name, $source_id),
                s.verify_count = coalesce(s.verify_count,0)+1,
                s.avg_confidence = round(
                    ((coalesce(s.avg_confidence,0.0) * coalesce(s.verify_count,0)) + $conf)
                    / (coalesce(s.verify_count,0)+1), 4),
                s.updated_at_ts=$now,
                s.metadata = $metadata
            RETURN s.key AS key, s.verify_count AS verify_count,
                   s.avg_confidence AS avg_confidence
            """,
            key=key, source=source, source_id=source_id, conf=conf,
            now=int(time.time() * 1000),
            metadata=json.dumps(metadata or {}),
        ).single()
        return dict(rec) if rec else {}


def get_speaker(neo, *, source: str, source_id: str) -> dict[str, Any] | None:
    key = _speaker_key(source, source_id)
    with neo._session() as s:
        rec = s.run(
            "MATCH (s:Speaker {key:$key}) RETURN s LIMIT 1", key=key
        ).single()
        return dict(rec["s"]) if rec else None


def is_trusted_speaker(neo, *, source: str, source_id: str,
                       max_age_days: int = 30,
                       min_avg_confidence: float = 0.6) -> bool:
    """A speaker is trusted when enrolled+verified recently with good confidence."""
    spk = get_speaker(neo, source=source, source_id=source_id)
    if not spk:
        return False
    import time as _t
    age_ms = (_t.time() * 1000) - float(spk.get("last_verified_at_ts") or 0)
    if age_ms > max_age_days * 86_400_000:
        return False
    return float(spk.get("avg_confidence") or 0) >= min_avg_confidence


def list_speakers(neo) -> list[dict[str, Any]]:
    with neo._session() as s:
        recs = s.run(
            "MATCH (s:Speaker) RETURN s ORDER BY s.last_verified_at_ts DESC LIMIT 500"
        ).data()
        return [dict(r["s"]) for r in recs]
