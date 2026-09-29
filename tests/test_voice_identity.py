"""Tests for the speaker identity registry."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx.voice_identity_routes import build_voice_identity_router
from assistx.voice_profile import (
    _speaker_key,
    cosine_similarity,
    is_trusted_speaker,
    upsert_speaker_verification,
)


class FakeSession:
    def __init__(self, store):
        self.store = store

    def run(self, query, **params):
        class R:
            def __init__(self, rows):
                self._rows = rows

            def single(self):
                return self._rows[0] if self._rows else None

            def data(self):
                return self._rows

        key = params.get("key")
        if "MERGE (s:Speaker" in query:
            spk = self.store.setdefault(key, {
                "key": key,
                "source": params["source"],
                "source_id": params["source_id"],
                "verify_count": 0,
                "avg_confidence": 0.0,
                "last_verified_at_ts": 0,
            })
            spk["verify_count"] += 1
            spk["avg_confidence"] = round(
                ((spk["avg_confidence"] * (spk["verify_count"] - 1)) + params["conf"])
                / spk["verify_count"], 4)
            spk["last_verified_at_ts"] = params["now"]
            return R([spk])
        if "MATCH (s:Speaker {key:$key})" in query:
            spk = self.store.get(key)
            return R([{"s": spk}] if spk else [])
        if "MATCH (s:Speaker)" in query:
            return R([{"s": v} for v in self.store.values()])
        raise AssertionError(f"unexpected query: {query[:60]}")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNeo:
    def __init__(self):
        self.store = {}

    def _session(self):
        return FakeSession(self.store)

    def close(self):
        pass


def test_cosine_similarity_basic():
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)


def test_verification_upsert_accumulates_confidence():
    neo = FakeNeo()
    for conf in (0.8, 0.9):
        rec = upsert_speaker_verification(
            neo, source="sophia-voice-agent", source_id="scott", confidence=conf
        )
    assert rec["verify_count"] == 2
    assert rec["avg_confidence"] == pytest.approx(0.85, abs=0.01)


def test_trusted_speaker_requires_recent_high_confidence():
    neo = FakeNeo()
    assert not is_trusted_speaker(neo, source="sophia-voice-agent", source_id="scott")
    upsert_speaker_verification(
        neo, source="sophia-voice-agent", source_id="scott", confidence=0.95
    )
    assert is_trusted_speaker(neo, source="sophia-voice-agent", source_id="scott")


def test_speaker_keys_are_stable_per_source():
    a = _speaker_key("sophia-voice-agent", "scott")
    b = _speaker_key("sophia-voice-agent", "scott")
    c = _speaker_key("other-source", "scott")
    assert a == b and a != c


def test_endpoints_roundtrip():
    store: dict = {}

    class FakeSession2(FakeSession):
        pass

    fake_neo = FakeNeo()
    app = FastAPI()
    app.include_router(build_voice_identity_router(lambda: fake_neo))
    tc = TestClient(app)

    r = tc.post("/api/voice-identity/verifications", json={"source_id": "scott", "confidence": 0.9})
    assert r.status_code == 200
    assert r.json()["registered"] is True

    r = tc.get("/api/voice-identity/trust", params={"source_id": "scott"})
    assert r.json()["trusted"] is True

    r = tc.post("/api/voice-identity/verifications", json={})
    assert r.status_code == 422
