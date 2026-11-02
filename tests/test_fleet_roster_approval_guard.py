"""An independent fleet roster must gate EVERY projection mutation path."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAME = "projection_roster_guard_under_test"
SPEC = importlib.util.spec_from_file_location(
    NAME, ROOT / "scripts" / "approve-runtime-projection.py"
)
assert SPEC and SPEC.loader
approval = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = approval
SPEC.loader.exec_module(approval)


def manifest(nodes=("x1-370", "xwing", "joyner", "deathstar",
                    "destroyer", "beelink", "lenovo", "optiplex")):
    # Only the coverage guard is under test; physical/identity verification
    # is deliberately the responsibility of the normal admission validators.
    return {
        "schema_version": 1, "generation": 897,
        "expected_current_generation": 896,
        "approval_id": "operator-reviewed-generation-897",
        "runtimes": [
            {
                "node_id": node,
                "runtime_instance_id": f"test-runtime-{i}",
                "models": [{"model_instance_id": f"model-{i}"}],
            }
            for i, node in enumerate(nodes)
        ],
    }


def roster(doc=None):
    m = manifest() if doc is None else doc
    return {
        "schema_version": 1,
        "generation": m["generation"],
        "expected_current_generation": m["expected_current_generation"],
        "approval_id": m["approval_id"],
        "node_ids": [r["node_id"] for r in manifest()["runtimes"]],
    }


def roster_path(tmp_path, document=None):
    path = tmp_path / "independent-required-fleet.json"
    path.write_text(json.dumps(roster() if document is None else document))
    path.chmod(0o600)
    return path


def test_eight_node_coverage_requires_exact_same_membership(tmp_path):
    required = approval.load_required_node_roster(roster_path(tmp_path))
    approval.validate_required_node_coverage(manifest(), required)
    for subset in (
        ("x1-370",), ("x1-370", "xwing", "joyner"),
        ("x1-370", "xwing", "joyner", "deathstar",
         "destroyer", "beelink", "lenovo"),
    ):
        with pytest.raises(ValueError, match="does not exactly cover"):
            approval.validate_required_node_coverage(manifest(subset), required)
    extra = manifest()
    extra["runtimes"].append({"node_id": "rogue", "runtime_instance_id": "extra"})
    with pytest.raises(ValueError, match="does not exactly cover"):
        approval.validate_required_node_coverage(extra, required)


def test_duplicate_runtime_node_fails_even_if_roster_ids_are_unique():
    p = manifest()
    p["runtimes"][1]["node_id"] = "x1-370"
    with pytest.raises(ValueError, match="does not exactly cover"):
        approval.validate_required_node_coverage(p, roster())


def test_expired_generation_or_different_approval_id_fails():
    p = manifest()
    for field, changed in (
        ("generation", 898),
        ("expected_current_generation", 895),
        ("approval_id", "another-operator-change"),
    ):
        old = p[field]
        p[field] = changed
        with pytest.raises(ValueError, match="identity differ"):
            approval.validate_required_node_coverage(p, roster())
        p[field] = old


def test_missing_or_unsafe_roster_fails(tmp_path):
    with pytest.raises(ValueError, match="mandatory"):
        approval.load_required_node_roster(None)
    path = roster_path(tmp_path)
    path.chmod(0o666)
    with pytest.raises(ValueError, match="writable"):
        approval.load_required_node_roster(path)
    path.chmod(0o600)
    symlink = tmp_path / "link.json"
    symlink.symlink_to(path)
    with pytest.raises(ValueError, match="non-symlinked"):
        approval.load_required_node_roster(symlink)
    bad = roster()
    bad["node_ids"] = ["x1-370"]
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="2-64"):
        approval.load_required_node_roster(path)


def test_transaction_refuses_incomplete_manifest_before_any_db_command():
    class NoDb:
        def run(self, *_args, **_kwargs):
            pytest.fail("mutation attempted before full-roster coverage check")

    doc = manifest(("x1-370",))
    validated = approval.ValidatedManifest(payload=doc, checksum="0" * 64)
    with pytest.raises(ValueError, match="does not exactly cover"):
        approval._apply_transaction(NoDb(), validated, 1000, roster())
    with pytest.raises(ValueError, match="missing independent"):
        approval._apply_transaction(NoDb(), validated, 1000, None)


def test_apply_cli_without_external_roster_never_opens_database(
    tmp_path, monkeypatch
):
    path = tmp_path / "single-runtime.yaml"
    # A valid schema is necessary to reach the roster check.
    import yaml
    validation_path = ROOT / "tests" / "test_runtime_projection_approval.py"
    spec = importlib.util.spec_from_file_location("roster_valid_manifest", validation_path)
    assert spec and spec.loader
    test_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = test_module
    spec.loader.exec_module(test_module)
    path.write_text(yaml.safe_dump(test_module.valid_manifest()))

    def forbidden_driver(*_a, **_kw):
        pytest.fail("database opened without mandatory independent roster")

    monkeypatch.setattr(approval.GraphDatabase, "driver", forbidden_driver)
    monkeypatch.setattr(sys, "argv", ["approve-runtime-projection.py",
                                       str(path), "--apply"])
    assert approval.main() == 2


def test_candidate_applicator_uses_same_transaction_guard():
    src = (ROOT / "scripts" / "apply-runtime-admission-candidate.py").read_text()
    assert "approver.load_required_node_roster(args.required_nodes_file)" in src
    assert "approver.validate_required_node_coverage(" in src
    assert "approver._apply_transaction(tx, validated_manifest, now_ms, required_roster)" in src
    assert 'parser.add_argument("--required-nodes-file", type=pathlib.Path)' in src
