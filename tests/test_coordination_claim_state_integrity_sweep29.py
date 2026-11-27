"""Hermetic claim-fencing tests for the fleet coordination lock store (sweep29).

No network, service, database or Docker fixture is used: every case is a
synthetic JSON file under pytest's tmp_path.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from assistx.coordination_routes import CoordinationStore

# Static, unambiguously-future UTC timestamp keeps these tests deterministic.
_FUTURE = "2099-01-01T00:00:00+00:00"


def _truncated_state_with_live_claim(path) -> None:
    """A truncated write still naming an active claim held by agent-a."""
    path.write_text('{"exclusive_ops": {"router-restart": {"owner": "agent-a", "expires_at": "' + _FUTURE + '"}}')


def test_corrupt_state_never_grants_another_owner_a_claim(tmp_path):
    path = tmp_path / "FLEET-COORDINATION.json"
    _truncated_state_with_live_claim(path)
    store = CoordinationStore(path=str(path))

    with pytest.raises(HTTPException) as exc:
        store.claim("router-restart", "agent-b")

    assert exc.value.status_code == 409
    assert "unreadable" in str(exc.value.detail)
    # The unreadable state must never be silently overwritten by a claim.
    assert "agent-a" in path.read_text()


def test_non_object_state_never_grants_a_claim(tmp_path):
    path = tmp_path / "coord.json"
    path.write_text("[]")
    store = CoordinationStore(path=str(path))

    with pytest.raises(HTTPException) as exc:
        store.claim("benchmark", "agent-b")

    assert exc.value.status_code == 409


def test_non_object_exclusive_ops_never_grants_a_claim(tmp_path):
    path = tmp_path / "coord.json"
    path.write_text(json.dumps({"exclusive_ops": []}))
    store = CoordinationStore(path=str(path))

    with pytest.raises(HTTPException) as exc:
        store.claim("benchmark", "agent-b")

    assert exc.value.status_code == 409


def test_missing_state_file_still_allows_first_claim(tmp_path):
    path = tmp_path / "coord.json"
    store = CoordinationStore(path=str(path))

    result = store.claim("benchmark", "agent-a")

    assert result["claimed"] is True
    assert json.loads(path.read_text())["exclusive_ops"]["benchmark"]["owner"] == "agent-a"


def test_valid_conflicting_claim_still_rejected(tmp_path):
    path = tmp_path / "coord.json"
    path.write_text(
        json.dumps(
            {
                "exclusive_ops": {
                    "benchmark": {"owner": "agent-a", "expires_at": _FUTURE},
                },
            }
        )
    )
    store = CoordinationStore(path=str(path))

    with pytest.raises(HTTPException) as exc:
        store.claim("benchmark", "agent-b")

    assert exc.value.status_code == 409


def test_read_only_snapshot_still_tolerates_unreadable_state(tmp_path):
    path = tmp_path / "coord.json"
    path.write_text("{not json")
    store = CoordinationStore(path=str(path))

    assert store.snapshot()["exclusive_ops"] == {}
    assert path.read_text() == "{not json"


def test_release_on_unreadable_state_is_a_noop(tmp_path):
    path = tmp_path / "coord.json"
    path.write_text("{not json")
    store = CoordinationStore(path=str(path))

    assert store.release("benchmark", "agent-a") == {
        "released": False,
        "reason": "no active claim",
    }
    assert path.read_text() == "{not json"
