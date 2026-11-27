"""Hermetic passive-claim safety regressions (sweep24).

All fixtures below are synthetic in-process dicts.  No socket, Neo4j, Redis,
SQLite, DB/WAL, docker, credential file or network mount is touched.
"""

from __future__ import annotations

from typing import Any

import assistx.passive_claims as pc


class _Result:
    def single(self) -> None:
        return None

    def consume(self) -> None:
        return None


class _Session:
    def __init__(self, task: dict[str, Any]) -> None:
        self._task = task

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute_write(self, fn: Any, *args: Any) -> dict[str, Any]:
        # Emulate the claim transaction: SET status='CLAIMED_PASSIVE' while
        # keeping the original status in previous_status, then RETURN t.
        return {"task": dict(self._task)}

    def run(self, query: str, params: Any = None) -> _Result:
        return _Result()


class _Neo:
    def __init__(self, task: dict[str, Any]) -> None:
        self._task = task

    @property
    def driver(self) -> _Neo:
        return self

    def session(self) -> _Session:
        return _Session(self._task)

    def close(self) -> None:
        return None


def _claimed_task(previous_status: str, **extra: Any) -> dict[str, Any]:
    """A Task node as returned *after* the passive-claim write."""
    return {
        "id": "task-1",
        "status": "CLAIMED_PASSIVE",
        "previous_status": previous_status,
        "title": "Review docs",
        "privacy": "unspecified",
        "queue_class": "background",
        "priority": "LOW",
        "required_capabilities": [],
        **extra,
    }


def _claim(
    monkeypatch: Any,
    task: dict[str, Any],
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    monkeypatch.setattr(pc, "record_passive_event", lambda *a, **k: "evt-1")
    body = pc.PassiveClaimIn(
        agent_id="agent-1",
        task_id="task-1",
        mode="review_only",
        capabilities=capabilities or [],
    )
    return pc.claim_passive_task(lambda: _Neo(task), body)


def test_ready_task_claim_is_accepted(monkeypatch: Any) -> None:
    result = _claim(monkeypatch, _claimed_task("READY"))
    assert result.get("ok") is True
    assert result.get("status") == "CLAIMED_PASSIVE"


def test_review_task_claim_is_accepted(monkeypatch: Any) -> None:
    result = _claim(monkeypatch, _claimed_task("REVIEW"))
    assert result.get("ok") is True
    assert result.get("status") == "CLAIMED_PASSIVE"


def test_sensitive_task_stays_blocked(monkeypatch: Any) -> None:
    result = _claim(monkeypatch, _claimed_task("READY", sensitive=True))
    assert result.get("ok") is False
    assert result.get("reason") == "safety_rejected"


def test_non_passive_previous_status_stays_blocked(monkeypatch: Any) -> None:
    result = _claim(monkeypatch, _claimed_task("RUNNING"))
    assert result.get("ok") is False
    assert result.get("reason") == "safety_rejected"


def test_capability_mismatch_stays_blocked(monkeypatch: Any) -> None:
    result = _claim(
        monkeypatch,
        _claimed_task("READY", required_capabilities=["repo-write"]),
        capabilities=[],
    )
    assert result.get("ok") is False
    assert result.get("reason") == "capability_mismatch"
