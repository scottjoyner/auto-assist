"""Strict source-binding codec compatibility; never grants source authority."""
from __future__ import annotations

import copy

import pytest

from assistx.contracts.schemas.repository_source_binding import (
    BINDING_AUTHORITY_SOURCE,
    RepositorySourceBinding,
)
from assistx.improvement_cycle import build_execution_contract
from assistx.improvement_runtime import contract_source_binding


def good():
    return {
        "authority_source": BINDING_AUTHORITY_SOURCE,
        "repository": "auto-assist",
        "repo_realpath": "/srv/repos/auto-assist",
        "worktree_realpath": "/srv/worktrees/bound-worktree",
        "branch": "main",
        "head_sha": "a" * 40,
        "expected_dirty": "clean_required",
        "task_id": "task-unit",
        "work_id": "attempt-1",
        "source_manifest_sha256": "b" * 64,
    }


def test_absent_legacy_binding_does_not_imply_authority():
    assert RepositorySourceBinding.from_contract_payload(None) is None
    assert RepositorySourceBinding.from_contract_payload({}) is None
    assert contract_source_binding({"kind":"legacy"}) is None


def test_explicit_valid_source_binding_round_trip():
    binding = RepositorySourceBinding.from_contract_payload(good())
    assert isinstance(binding, RepositorySourceBinding)
    assert binding.to_contract_payload() == binding.provenance()
    assert binding.to_contract_payload()["repo_realpath"] == good()["repo_realpath"]
    assert RepositorySourceBinding.from_contract_payload(
        binding.to_contract_payload()) == binding


def test_canonical_binding_object_keeps_identity():
    binding = RepositorySourceBinding.model_validate(good())
    assert RepositorySourceBinding.from_contract_payload(binding) is binding


def test_contract_builder_uses_exact_payload_not_home_fallback():
    binding = RepositorySourceBinding.model_validate(good())
    built = build_execution_contract(
        repository="auto-assist",
        objective="Investigate source only",
        allowed_paths=["tests/sample.py"],
        verification_commands=[["pytest", "-q", "tests/sample.py"]],
        source_binding=binding,
    )
    assert built["source_binding"] == binding.to_contract_payload()
    assert contract_source_binding(built) == binding


@pytest.mark.parametrize("field,value",[
    ("repository", "../../etc"),
    ("repo_realpath", "/srv/repos/../stale"),
    ("worktree_realpath", "/tmp/../home"),
    ("head_sha", "bad"),
    ("authority_source", "caller_provided"),
    ("source_manifest_sha256", "bad"),
    ("task_id", "bad\x00id"),
])
def test_tampered_or_noncanonical_payload_denied(field,value):
    supplied=copy.deepcopy(good())
    supplied[field]=value
    with pytest.raises(ValueError):
        RepositorySourceBinding.from_contract_payload(supplied)


def test_invalid_present_payload_must_not_silently_be_unbound():
    for supplied in ({"repository":"auto-assist"}, {"repository_realpath":"/src"}, [], "missing"):
        with pytest.raises(ValueError):
            RepositorySourceBinding.from_contract_payload(supplied)


def test_unrecognized_legacy_aliases_are_not_implicitly_authoritative():
    supplied=good()
    supplied["repository_realpath"]=supplied.pop("repo_realpath")
    with pytest.raises(ValueError):
        RepositorySourceBinding.from_contract_payload(supplied)


def test_no_extra_authority_field():
    supplied=good()
    supplied["mutation_allowed"]=True
    with pytest.raises(ValueError):
        RepositorySourceBinding.from_contract_payload(supplied)
