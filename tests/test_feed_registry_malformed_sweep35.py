"""Sweep35: malformed feed-registry entries must not be silently admitted.

Hermetic/offline: exercises only the pure parsing/summary helpers in
``assistx.feed_registry`` with monkeypatched environment strings. No Neo4j,
Redis, Docker, network, live fleet state or externally supplied fixture is used.
"""

from __future__ import annotations

import pytest

from assistx import feed_registry as fr


def test_unknown_health_status_is_skipped() -> None:
    # Base coerces "bogus" -> "degraded" and admits the connector; the direct
    # /api/feeds POST path rejects that same value with HTTP 400.
    out = fr._parse_registry_env("bad|Bad Feed|general|true|bogus|local://bad")
    assert out == []


def test_blank_endpoint_is_skipped() -> None:
    # A connector with no endpoint cannot be dialed, yet the base parser
    # admits it and GET /api/feeds syncs it into Neo4j.
    out = fr._parse_registry_env("noep|No EP|general|true|healthy|   ")
    assert out == []


def test_mixed_registry_skips_only_malformed_entries() -> None:
    raw = ";".join(
        [
            "good|Good|general|true|healthy|local://good",
            "badhealth|Bad|general|true|bogus|local://badhealth",
            "noep|No EP|general|true|healthy|",
        ]
    )
    assert [c.id for c in fr._parse_registry_env(raw)] == ["good"]


def test_valid_entries_are_unchanged() -> None:
    raw = ";".join(
        [
            "a|A|market|1|healthy|local://a",
            "b|B|macro|yes|degraded|local://b",
            "c|C|events|off|down|local://c",
        ]
    )
    out = fr._parse_registry_env(raw)
    assert [c.id for c in out] == ["a", "b", "c"]
    assert [c.health_status for c in out] == ["healthy", "degraded", "down"]
    assert [c.enabled for c in out] == [True, True, False]
    assert [c.endpoint for c in out] == ["local://a", "local://b", "local://c"]


def test_health_summary_does_not_count_malformed_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ASSISTX_FEED_CONNECTORS", raising=False)
    baseline = fr.feed_health_summary()
    monkeypatch.setenv(
        "ASSISTX_FEED_CONNECTORS",
        "bad|Bad Feed|general|true|bogus|local://bad",
    )
    summary = fr.feed_health_summary()
    assert summary["total"] == baseline["total"]
    assert summary["by_status"] == baseline["by_status"]
    assert "bad" not in [c["id"] for c in summary["connectors"]]


def test_valid_registry_env_is_still_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSISTX_FEED_CONNECTORS", "v|Valid|general|true|healthy|local://v")
    summary = fr.feed_health_summary()
    assert summary["total"] == 1
    assert summary["connectors"][0]["id"] == "v"
