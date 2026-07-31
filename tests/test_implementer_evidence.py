from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-implementer-evidence.py"
EXAMPLE = ROOT / "deploy" / "reconciliation" / "implementer-evidence.example.json"

spec = importlib.util.spec_from_file_location("implementer_evidence", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def load_example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def completed_integration() -> dict:
    document = load_example()
    document["change_id"] = "recovery-rehearsal-20260731T190000Z"
    for key in document["operators"]:
        document["operators"][key] = f"operator-{key}"
    document["source"]["auto_assist_integration_sha"] = "a" * 40
    document["source"]["fleet_resilience_integration_sha"] = "b" * 40
    document["ci"]["auto_assist_run_id"] = "30643920630"
    document["ci"]["fleet_resilience_run_id"] = "30642695246"
    document["evidence_manifest_sha256"] = "c" * 64
    document["review"] = {
        "decision": "GO_TO_REHEARSAL",
        "reviewed_at_utc": "2026-07-31T19:00:00Z",
        "reviewer": "operator-validation_reviewer",
        "exceptions": [],
    }
    return document


def completed_rehearsal() -> dict:
    document = completed_integration()
    document["review"]["decision"] = "REHEARSAL_COMPLETE"
    for gate in document["gates"]:
        gate["status"] = "PASS"
        if not gate["evidence"]:
            gate["evidence"] = [f"evidence/{gate['id']}.json"]
    document["authorization"]["activation_epoch"] = 7
    document["authorization"]["fence_proof_reference"] = (
        "witness:rehearsal-exclusive-lease-7"
    )
    document["authorization"]["activation_envelope_sha256"] = "d" * 64
    return document


def test_checked_in_example_structure_is_valid():
    result = module.validate(load_example(), "integration", allow_template=True)
    assert result["ok"] is True
    assert result["required_gate_count"] == 4


def test_completed_integration_manifest_is_valid():
    result = module.validate(completed_integration(), "integration")
    assert result["stage"] == "integration"
    assert result["production_changed"] is False


def test_production_change_is_rejected():
    document = completed_integration()
    document["production_changed"] = True
    with pytest.raises(module.EvidenceError, match="production_changed"):
        module.validate(document, "integration")


def test_rehearsal_requires_all_gates_and_positive_epoch():
    document = completed_rehearsal()
    document["authorization"]["activation_epoch"] = 0
    with pytest.raises(module.EvidenceError, match="activation_epoch"):
        module.validate(document, "rehearsal")
    document["authorization"]["activation_epoch"] = 7
    result = module.validate(document, "rehearsal")
    assert result["required_gate_count"] == 17


def test_rehearsal_requires_independent_fence_reference():
    document = completed_rehearsal()
    document["authorization"]["fence_proof_reference"] = "assistx-lease:not-independent"
    with pytest.raises(module.EvidenceError, match="fence_proof_reference"):
        module.validate(document, "rehearsal")


def test_rehearsal_requires_signed_activation_checksum():
    document = completed_rehearsal()
    document["authorization"]["activation_envelope_sha256"] = "not-generated"
    with pytest.raises(module.EvidenceError, match="activation_envelope_sha256"):
        module.validate(document, "rehearsal")


def test_pass_gate_without_evidence_is_rejected():
    document = completed_integration()
    document["gates"][0]["evidence"] = []
    with pytest.raises(module.EvidenceError, match="PASS without evidence"):
        module.validate(document, "integration")
