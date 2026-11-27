"""Fail-closed Redis liveness verdict (round 60).

The runtime health snapshot gates readiness on ``_check_redis()["status"] ==
"ok"``.  A Redis liveness probe only *confirms* liveness when it returns the
boolean ``True`` (redis-py's contract, or it raises).  Before this fix the code
only treated the exact value ``False`` as down, so a wrapper/stub returning
``None``, ``0`` or ``""`` -- no affirmative confirmation at all -- was reported
as ``ok`` and kept runtime health green with zero liveness evidence.

Purely offline: no Redis, sockets, services, credentials or subprocesses.  The
redis module factory is replaced with in-memory stubs and only ``tmp_path`` is
used for any incidental bytecode.
"""

from __future__ import annotations

from typing import Any

from assistx import deps, runtime as runtime_mod

_REDIS_URL = "redis://127.0.0.1:6379/0"


class _FakeClient:
    def __init__(self, ping_value: Any) -> None:
        self._ping_value = ping_value
        self.closed = False

    def ping(self) -> Any:
        return self._ping_value

    def close(self) -> None:
        self.closed = True


class _FakeRedisModule:
    def __init__(self, ping_value: Any) -> None:
        self._ping_value = ping_value
        self.clients: list[_FakeClient] = []

    def from_url(self, url: str, **kwargs: Any) -> _FakeClient:
        client = _FakeClient(self._ping_value)
        self.clients.append(client)
        return client


def _install(monkeypatch: Any, ping_value: Any) -> _FakeRedisModule:
    module = _FakeRedisModule(ping_value)
    monkeypatch.setenv("REDIS_URL", _REDIS_URL)
    monkeypatch.setattr(deps, "load_redis_module", lambda: module)
    return module


def test_unconfirmed_ping_none_is_down(monkeypatch: Any) -> None:
    module = _install(monkeypatch, None)
    verdict = runtime_mod._check_redis()
    assert verdict["status"] == "down", "None is not an affirmative liveness confirmation"
    assert verdict["url"] == _REDIS_URL
    assert verdict["reason"]
    assert module.clients and module.clients[0].closed, "client must still be closed"


def test_unconfirmed_ping_falsy_values_are_down(monkeypatch: Any) -> None:
    for value in (0, ""):
        module = _install(monkeypatch, value)
        verdict = runtime_mod._check_redis()
        assert verdict["status"] == "down", f"ping={value!r} must not read as healthy"
        assert module.clients[0].closed


def test_non_boolean_truthy_ping_is_down(monkeypatch: Any) -> None:
    # "PONG"/1 are not redis-py's bool True; an authority verdict should not
    # infer health from an unexpected probe return shape.
    for value in ("PONG", 1):
        _install(monkeypatch, value)
        assert runtime_mod._check_redis()["status"] == "down"


def test_affirmative_true_ping_still_ok(monkeypatch: Any) -> None:
    module = _install(monkeypatch, True)
    verdict = runtime_mod._check_redis()
    assert verdict["status"] == "ok"
    assert verdict["url"] == _REDIS_URL
    assert module.clients[0].closed


def test_explicit_false_ping_still_down(monkeypatch: Any) -> None:
    _install(monkeypatch, False)
    verdict = runtime_mod._check_redis()
    assert verdict["status"] == "down"
    assert "reason" in verdict


def test_runtime_health_degrades_when_redis_ping_unconfirmed(monkeypatch: Any) -> None:
    """The unconfirmed verdict must propagate all the way to readiness."""
    _install(monkeypatch, None)
    monkeypatch.setenv("ASSISTX_RUNTIME_PROFILE", "production")
    monkeypatch.setattr(
        runtime_mod, "_check_neo4j",
        lambda: {"status": "ok", "uri": "bolt://stub", "database": "assistx"},
    )
    monkeypatch.setattr(
        runtime_mod, "_check_llm",
        lambda: {"status": "ok", "backend": "openai", "endpoint": "http://stub"},
    )
    monkeypatch.setattr(
        runtime_mod, "_check_outbox",
        lambda: {"status": "ok", "ok": True, "pending": 0, "dead": 0,
                 "oldest_pending_age_s": None, "readiness_enforced": True},
    )

    health = runtime_mod.build_runtime_health()

    assert health["dependencies"]["redis"]["status"] == "down"
    assert health["ok"] is False, "runtime must not be green with unconfirmed Redis"
