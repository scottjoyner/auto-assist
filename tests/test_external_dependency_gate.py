from __future__ import annotations

import importlib.util
import pathlib


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "validate-external-dependencies.py"
SPEC = importlib.util.spec_from_file_location("validate_external_dependencies", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def ready_document() -> dict:
    return {
        "schema_version": 1,
        "status": "ready",
        "policy": {
            "inference_egress": "private_only",
            "public_inference": "forbidden",
            "unknown_required_dependency": "blocks_cutover",
            "missing_required_evidence": "blocks_cutover",
            "image_restore_without_internet": "required",
            "executor_mounts_default": "deny",
        },
        "required_dependency_ids": ["neo4j", "public-inference-policy"],
        "dependencies": [
            {
                "id": "neo4j",
                "required": True,
                "expected_state": "healthy",
                "status": "healthy",
                "owner": "assistx",
                "failure_impact": "state unavailable",
                "probe": "RETURN 1",
                "evidence": ["artifacts/neo4j-probe.txt sha256:abc"],
                "rollback": "restart old database",
            },
            {
                "id": "public-inference-policy",
                "required": True,
                "expected_state": "disabled",
                "status": "disabled",
                "owner": "assistx",
                "failure_impact": "requests leave fleet",
                "probe": "inspect environment",
                "evidence": ["artifacts/offline-verifier.txt sha256:def"],
                "rollback": "stop new router",
            },
        ],
    }


def test_ready_registry_passes() -> None:
    assert MODULE.validate_dependency_registry(ready_document()) == []


def test_unknown_required_dependency_blocks() -> None:
    document = ready_document()
    document["dependencies"][0]["status"] = "unknown"
    errors = MODULE.validate_dependency_registry(document)
    assert any("neo4j must be healthy" in error for error in errors)


def test_placeholder_evidence_blocks() -> None:
    document = ready_document()
    document["dependencies"][0]["evidence"] = ["change-me"]
    errors = MODULE.validate_dependency_registry(document)
    assert any("evidence contains a placeholder" in error for error in errors)


def test_required_dependency_must_be_declared() -> None:
    document = ready_document()
    document["required_dependency_ids"] = ["neo4j"]
    errors = MODULE.validate_dependency_registry(document)
    assert any("missing from required_dependency_ids" in error for error in errors)


def test_registry_status_must_be_ready_after_gates_pass() -> None:
    document = ready_document()
    document["status"] = "blocked"
    errors = MODULE.validate_dependency_registry(document)
    assert errors == ["status must be 'ready' after all dependency gates pass"]
