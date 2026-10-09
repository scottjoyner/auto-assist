"""Regression: migration adapters preserve strict source binding provenance.

The optional absence of a source binding is not equivalent to a malformed
supplied binding. A migration must never turn bad source identity into an
unbound task or introduce fallback checkout authority.
"""
import pytest
from pydantic import ValidationError

from assistx.contracts.schemas.repository_source_binding import (
    BINDING_AUTHORITY_SOURCE, RepositorySourceBinding,
)
from assistx.improvement_cycle import build_execution_contract
from assistx.improvement_runtime import contract_source_binding

SHA = "a" * 40


def supplied_binding():
    return {
        "authority_source": BINDING_AUTHORITY_SOURCE,
        "repository": "auto-assist",
        "repo_realpath": "/srv/repos/auto-assist",
        "worktree_realpath": "/srv/worktrees/source-checked",
        "branch": "main",
        "head_sha": SHA,
        "expected_dirty": "clean_required",
        "task_id": "task-1",
        "work_id": "attempt-1",
        "source_manifest_sha256": "d" * 64,
    }


def contract(source_binding=None):
    return build_execution_contract(
        repository="auto-assist",
        objective="Synthetic bounded source review",
        allowed_paths=["tests/test_source_binding_payload_adapter.py"],
        verification_commands=[["pytest", "-q", "tests/test_source_binding_payload_adapter.py"]],
        source_binding=source_binding,
    )


def test_unbound_absence_preserved_but_present_empty_binding_denied():
    assert RepositorySourceBinding.from_contract_payload(None) is None
    item=contract()
    assert "source_binding" not in item
    assert contract_source_binding(item) is None
    for invalid in ({}, [], "", 0, {"repository": "auto-assist"}):
        with pytest.raises((ValidationError, ValueError)):
            RepositorySourceBinding.from_contract_payload(invalid)
        with pytest.raises((ValidationError, ValueError)):
            contract(invalid)


def test_strict_binding_roundtrips_with_no_path_or_authority_loss():
    original=RepositorySourceBinding.from_contract_payload(supplied_binding())
    assert original is not None
    assert original.to_contract_payload()==original.provenance()
    bound=contract(original)
    assert bound["source_binding"]==supplied_binding()
    recovered=contract_source_binding(bound)
    assert recovered==original
    assert recovered.worktree_realpath=="/srv/worktrees/source-checked"
    assert recovered.work_id=="attempt-1"


def test_wrong_repo_or_untrusted_provenance_denied_before_contract_emission():
    valid=supplied_binding()
    with pytest.raises(ValueError, match="repository does not match"):
        build_execution_contract(
            repository="different",
            objective="Synthetic",
            allowed_paths=["tests/example.py"],
            verification_commands=[["pytest", "-q", "tests/example.py"]],
            source_binding=valid,
        )
    for changes in (
        {"authority_source": "unverified_user_path"},
        {"repo_realpath": "/srv/elsewhere/../repos/auto-assist"},
        {"work_id": ""},
        {"extra_payload": "unknown"},
    ):
        forged={**valid,**changes}
        with pytest.raises((ValidationError,ValueError)):
            contract(forged)
