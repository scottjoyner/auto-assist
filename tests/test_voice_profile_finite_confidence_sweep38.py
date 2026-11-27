"""Sweep38: non-finite verification confidence must fail closed.

Regression for the voice-profile authorization boundary: the old
``max(0.0, min(1.0, float(confidence)))`` clamp turned NaN/infinity (and the
strings ``"nan"`` / ``"inf"``) into a full-confidence verification, so any
caller could mark an arbitrary speaker as trusted via
``POST /api/voice-identity/verifications``.

All fixtures here are tiny and in-memory: no network, DB, Neo4j or Docker.
"""

from __future__ import annotations

import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx.voice_identity_routes import build_voice_identity_router
from assistx.voice_profile import is_trusted_speaker, upsert_speaker_verification


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return self._rows


class _FakeSession:
    """Faithful in-memory stand-in for the Cypher used by voice_profile."""

    def __init__(self, store):
        self.store = store

    def run(self, query, **params):
        key = params.get("key")
        if "MERGE (s:Speaker" in query:
            spk = self.store.setdefault(
                key,
                {
                    "key": key,
                    "source": params["source"],
                    "source_id": params["source_id"],
                    "verify_count": 0,
                    "avg_confidence": 0.0,
                    "last_verified_at_ts": 0,
                },
            )
            prior = spk["verify_count"]
            spk["verify_count"] = prior + 1
            spk["avg_confidence"] = round(((spk["avg_confidence"] * prior) + params["conf"]) / (prior + 1), 4)
            spk["last_verified_at_ts"] = params["now"]
            return _Result([spk])
        if "MATCH (s:Speaker {key:$key})" in query:
            spk = self.store.get(key)
            return _Result([{"s": spk}] if spk else [])
        if "MATCH (s:Speaker)" in query:
            return _Result([{"s": v} for v in self.store.values()])
        raise AssertionError(f"unexpected query: {query[:60]}")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeNeo:
    def __init__(self):
        self.store = {}

    def _session(self):
        return _FakeSession(self.store)

    def close(self):
        pass


def _upsert(neo, confidence, source_id="scott"):
    return upsert_speaker_verification(
        neo,
        source="sophia-voice-agent",
        source_id=source_id,
        confidence=confidence,
    )


# --- direct boundary: unusable confidence must be rejected (fail closed) ---


@pytest.mark.parametrize("bad", [float("nan"), "nan", float("inf"), "inf", "-inf"])
def test_non_finite_confidence_is_rejected(bad):
    neo = _FakeNeo()
    with pytest.raises(ValueError):
        _upsert(neo, bad)
    assert neo.store == {}


def test_bool_confidence_is_rejected():
    neo = _FakeNeo()
    with pytest.raises(ValueError):
        _upsert(neo, True)
    assert neo.store == {}


def test_garbage_confidence_is_rejected():
    neo = _FakeNeo()
    with pytest.raises(ValueError):
        _upsert(neo, "high")
    assert neo.store == {}


@pytest.mark.parametrize("value,expected", [(2.5, 1.0), (-1.0, 0.0), (0.9, 0.9), (None, 1.0)])
def test_valid_confidence_still_clamps(value, expected):
    neo = _FakeNeo()
    rec = _upsert(neo, value)
    assert rec["avg_confidence"] == pytest.approx(expected)
    assert not math.isnan(float(rec["avg_confidence"]))


# --- authorization end state: NaN must never grant trust ---


def test_nan_confidence_does_not_grant_trust():
    neo = _FakeNeo()
    try:
        _upsert(neo, float("nan"))
    except ValueError:
        pass
    assert is_trusted_speaker(neo, source="sophia-voice-agent", source_id="scott") is False


def test_string_nan_confidence_does_not_grant_trust():
    neo = _FakeNeo()
    try:
        _upsert(neo, "nan")
    except ValueError:
        pass
    assert is_trusted_speaker(neo, source="sophia-voice-agent", source_id="scott") is False


# --- route contract: invalid confidence is a client error, not a grant ---


def _client(neo):
    app = FastAPI()
    app.include_router(build_voice_identity_router(lambda: neo))
    return TestClient(app)


@pytest.mark.parametrize("bad", ["nan", "inf", "high"])
def test_route_rejects_unusable_confidence(bad):
    neo = _FakeNeo()
    tc = _client(neo)
    r = tc.post(
        "/api/voice-identity/verifications",
        json={"source_id": "scott", "confidence": bad},
    )
    assert r.status_code == 422
    assert neo.store == {}
    t = tc.get("/api/voice-identity/trust", params={"source_id": "scott"})
    assert t.json()["trusted"] is False


def test_route_still_accepts_valid_confidence():
    neo = _FakeNeo()
    tc = _client(neo)
    r = tc.post(
        "/api/voice-identity/verifications",
        json={"source_id": "scott", "confidence": 0.9},
    )
    assert r.status_code == 200
    assert r.json()["registered"] is True
    t = tc.get("/api/voice-identity/trust", params={"source_id": "scott"})
    assert t.json()["trusted"] is True
