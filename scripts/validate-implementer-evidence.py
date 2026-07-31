#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

AUTO_ASSIST_ATTESTED_SHA = "74a9f9c386de93e99e7c3f8488db868a31be6db6"
FLEET_RESILIENCE_ATTESTED_SHA = "f59002a5d91f89e670fe4cd0fe08f08703b08fe2"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"replace-with|<[^>]+>", re.IGNORECASE)
VALID_STATUS = {"PASS", "FAIL", "NOT_RUN", "BLOCKED"}

INTEGRATION_GATES = {
    "source-integrity",
    "conflict-review",
    "auto-assist-validation",
    "appliance-validation",
}
REHEARSAL_GATES = INTEGRATION_GATES | {
    "image-bundle",
    "beelink-preflight",
    "warm-standby-fence",
    "signed-snapshot-replication",
    "neo4j-backup-verification",
    "degraded-activation",
    "heartbeat-delegation",
    "pending-durable-finalization",
    "memory-shedding",
    "isolated-neo4j-restore",
    "journal-replay",
    "primary-return",
    "rollback",
}


class EvidenceError(ValueError):
    pass


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise EvidenceError(f"{field} is required")
    return result


def _validate_sha(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    result = _text(value, field)
    if not pattern.fullmatch(result):
        raise EvidenceError(f"{field} is not a valid lowercase hexadecimal digest")
    return result


def _validate_evidence_path(value: Any, gate_id: str) -> str:
    path = _text(value, f"gate {gate_id} evidence path")
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise EvidenceError(f"gate {gate_id} evidence path must be relative and contained")
    return path


def validate(document: dict[str, Any], stage: str, allow_template: bool = False) -> dict[str, Any]:
    if document.get("schema_version") != 1:
        raise EvidenceError("schema_version must equal 1")
    if document.get("production_changed") is not False:
        raise EvidenceError("production_changed must remain false for this handoff")

    source = document.get("source")
    if not isinstance(source, dict):
        raise EvidenceError("source must be an object")
    if source.get("auto_assist_attested_sha") != AUTO_ASSIST_ATTESTED_SHA:
        raise EvidenceError("auto_assist_attested_sha does not match the frozen handoff")
    if source.get("fleet_resilience_attested_sha") != FLEET_RESILIENCE_ATTESTED_SHA:
        raise EvidenceError("fleet_resilience_attested_sha does not match the frozen handoff")
    _validate_sha(source.get("auto_assist_integration_sha"), "auto_assist_integration_sha", SHA40)
    _validate_sha(source.get("fleet_resilience_integration_sha"), "fleet_resilience_integration_sha", SHA40)

    operators = document.get("operators")
    if not isinstance(operators, dict):
        raise EvidenceError("operators must be an object")
    for role in (
        "integration_implementer",
        "appliance_implementer",
        "primary_operator",
        "witness_or_break_glass_approver",
        "validation_reviewer",
    ):
        _text(operators.get(role), f"operators.{role}")

    raw_gates = document.get("gates")
    if not isinstance(raw_gates, list):
        raise EvidenceError("gates must be an array")
    gates: dict[str, dict[str, Any]] = {}
    for raw in raw_gates:
        if not isinstance(raw, dict):
            raise EvidenceError("every gate must be an object")
        gate_id = _text(raw.get("id"), "gate.id")
        if gate_id in gates:
            raise EvidenceError(f"duplicate gate id: {gate_id}")
        status = _text(raw.get("status"), f"gate {gate_id} status").upper()
        if status not in VALID_STATUS:
            raise EvidenceError(f"gate {gate_id} has invalid status {status}")
        evidence = raw.get("evidence")
        if not isinstance(evidence, list):
            raise EvidenceError(f"gate {gate_id} evidence must be an array")
        normalized = [_validate_evidence_path(item, gate_id) for item in evidence]
        if status == "PASS" and not normalized:
            raise EvidenceError(f"gate {gate_id} is PASS without evidence")
        gates[gate_id] = {"status": status, "evidence": normalized}

    required = INTEGRATION_GATES if stage == "integration" else REHEARSAL_GATES
    missing = sorted(required - gates.keys())
    if missing:
        raise EvidenceError("missing required gates: " + ", ".join(missing))
    incomplete = sorted(
        gate_id for gate_id in required if gates[gate_id]["status"] != "PASS"
    )
    if incomplete:
        raise EvidenceError("required gates are not PASS: " + ", ".join(incomplete))

    authorization = document.get("authorization")
    if not isinstance(authorization, dict):
        raise EvidenceError("authorization must be an object")
    if authorization.get("production_deployment_approved") is not False:
        raise EvidenceError("production deployment may not be approved by this handoff")
    epoch = authorization.get("activation_epoch")
    if stage == "rehearsal" and (not isinstance(epoch, int) or epoch <= 0):
        raise EvidenceError("rehearsal activation_epoch must be a positive integer")

    manifest_sha = _text(document.get("evidence_manifest_sha256"), "evidence_manifest_sha256")
    if not allow_template:
        if PLACEHOLDER.search(json.dumps(document, sort_keys=True)):
            raise EvidenceError("manifest still contains placeholder text")
        if not SHA256.fullmatch(manifest_sha):
            raise EvidenceError("evidence_manifest_sha256 must be a 64-character digest")

    review = document.get("review")
    if not isinstance(review, dict):
        raise EvidenceError("review must be an object")
    expected_decision = "GO_TO_REHEARSAL" if stage == "integration" else "REHEARSAL_COMPLETE"
    if not allow_template and review.get("decision") != expected_decision:
        raise EvidenceError(
            f"review.decision must equal {expected_decision} for stage {stage}"
        )

    return {
        "ok": True,
        "stage": stage,
        "required_gate_count": len(required),
        "validated_gate_count": len(gates),
        "production_changed": False,
        "attested_inputs": {
            "auto_assist": AUTO_ASSIST_ATTESTED_SHA,
            "fleet_resilience": FLEET_RESILIENCE_ATTESTED_SHA,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the July 31 degraded recovery implementer evidence manifest."
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--stage",
        choices=("integration", "rehearsal"),
        default="integration",
    )
    parser.add_argument(
        "--allow-template",
        action="store_true",
        help="Allow placeholders while validating the checked-in example structure.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        document = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise EvidenceError("manifest root must be an object")
        result = validate(document, args.stage, allow_template=args.allow_template)
    except (OSError, json.JSONDecodeError, EvidenceError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
