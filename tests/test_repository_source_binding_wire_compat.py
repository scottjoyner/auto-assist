"""Security regression: schema dialects must not disable repository identity checks."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from assistx.contracts.schemas.repository_source_binding import (
    DirtyStateExpectation, RepositorySourceBinding
)


def _payload():
    return dict(
        repository="auto-assist",
        repository_realpath="/srv/repos/auto-assist",
        worktree_realpath="/srv/wt/authoritative",
        branch="main",
        head_sha="a" * 40,
        dirty_expectation=DirtyStateExpectation.CLEAN_REQUIRED.value,
        task_id="task-bound-001",
        work_id="attempt-bound-001",
    )


def test_round_trip_preserves_legacy_wire_identity_without_widening():
    bound = RepositorySourceBinding.from_contract_payload(_payload())
    assert bound is not None
    assert bound.repository_realpath == bound.repo_realpath == "/srv/repos/auto-assist"
    assert bound.expected_dirty == bound.dirty_expectation == DirtyStateExpectation.CLEAN_REQUIRED
    assert bound.model_config["frozen"] is True
    wire = bound.to_contract_payload()
    assert all(wire[k] == v for k, v in _payload().items())
    assert wire["authority_source"] == "configured_repository_roots"
    assert RepositorySourceBinding.from_contract_payload(wire) == bound


@pytest.mark.parametrize("field,new_value", [
    ("repository_realpath", "/srv/mirror/wrong"),
    ("worktree_realpath", "/srv/wt/stale"),
    ("head_sha", "bad"),
    ("authority_source", "caller-supplied"),
    ("work_id", "attempt\nmalicious"),
    ("source_manifest_sha256", "bad"),
])
def test_identity_and_digest_rejection(field, new_value):
    payload = _payload()
    payload[field] = new_value
    if field in ("repository_realpath", "worktree_realpath"):
        # These are syntactically valid but a differently bound identity;
        # no alias normalization may silently replace the original identity.
        obj = RepositorySourceBinding.from_contract_payload(payload)
        assert obj is not None and obj.to_contract_payload()[field] == new_value
    else:
        with pytest.raises(ValidationError):
            RepositorySourceBinding.from_contract_payload(payload)


@pytest.mark.parametrize("alias", ["repo_realpath", "expected_dirty"])
def test_ambiguous_duplicate_canonical_and_legacy_fields_are_denied(alias):
    payload = _payload()
    payload[alias] = "/srv/wt/malicious" if alias == "repo_realpath" else "any"
    with pytest.raises(ValidationError):
        RepositorySourceBinding.from_contract_payload(payload)


@pytest.mark.parametrize("payload", [None, {}])
def test_absent_optional_binding_only_can_be_unbound(payload):
    assert RepositorySourceBinding.from_contract_payload(payload) is None


@pytest.mark.parametrize("payload", [
    {"repository": "auto-assist"},
    "auto-assist",
    [],
    {"fallback_path": "/srv/wt/stale"},
])
def test_present_but_invalid_binding_never_downgrades_to_unbound(payload):
    with pytest.raises((ValidationError, ValueError)):
        RepositorySourceBinding.from_contract_payload(payload)


def test_newer_internal_spelling_deserializes_to_same_legacy_wire_payload():
    payload = _payload()
    internal = dict(payload)
    internal["repo_realpath"] = internal.pop("repository_realpath")
    internal["expected_dirty"] = internal.pop("dirty_expectation")
    obj = RepositorySourceBinding.from_contract_payload(internal)
    assert obj is not None
    wire = obj.to_contract_payload()
    assert all(wire[k] == v for k, v in payload.items())
    assert "repo_realpath" not in wire and "expected_dirty" not in wire
