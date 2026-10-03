#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from assistx.system_one_receipt import bind_shadow_receipt  # noqa: E402

ACCEPTANCE_SCHEMA = "assistx-system-one-shadow-receipt-acceptance-v1"
POLICY_CONTRACT = "assistx-agent-policy-v1"
_AUTHORITY_FIELDS = (
    "dispatch_allowed",
    "approval_granted",
    "claim_acquired",
    "mutation_allowed",
    "routing_authority_changed",
)


def _load_mapping(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not readable JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be a JSON object")
    return value


def validate_capture(
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    *,
    expected_provider_id: str | None = None,
    expected_model_id: str | None = None,
    expected_model_artifact_sha256: str | None = None,
    require_model_artifact: bool = False,
) -> dict[str, Any]:
    if response_payload.get("contract") != POLICY_CONTRACT:
        raise ValueError("unexpected policy response contract")

    receipt = response_payload.get("decision_receipt")
    if not isinstance(receipt, dict):
        raise ValueError("decision_receipt must be a JSON object")

    binding = bind_shadow_receipt(
        request_payload=request_payload,
        receipt_value=receipt,
    )
    provider = binding["provider"]
    authority = binding["authority"]

    if binding["evidence_only"] is not True:
        raise ValueError("receipt binding must remain evidence-only")
    if binding["authoritative_behavior_changed"] is not False:
        raise ValueError("receipt binding cannot change authoritative behavior")
    if set(authority) != set(_AUTHORITY_FIELDS):
        raise ValueError("unexpected receipt authority field set")
    if any(authority[name] is not False for name in _AUTHORITY_FIELDS):
        raise ValueError("receipt authority must remain literal false")

    artifact_sha = provider.get("model_artifact_sha256")
    if require_model_artifact and artifact_sha is None:
        raise ValueError("model_artifact_sha256 is required for fleet acceptance")
    if (
        expected_provider_id is not None
        and provider.get("provider_id") != expected_provider_id
    ):
        raise ValueError("provider_id does not match expected identity")
    if (
        expected_model_id is not None
        and provider.get("model_id") != expected_model_id
    ):
        raise ValueError("model_id does not match expected identity")
    if (
        expected_model_artifact_sha256 is not None
        and artifact_sha != expected_model_artifact_sha256
    ):
        raise ValueError("model_artifact_sha256 does not match expected artifact")

    return {
        "schema": ACCEPTANCE_SCHEMA,
        "status": "pass",
        "request_sha256": binding["request_sha256"],
        "decision_receipt_sha256": binding["decision_receipt_sha256"],
        "model_visible_input_sha256": binding["model_visible_input_sha256"],
        "question_schema_sha256": binding["question_schema_sha256"],
        "candidate_set_sha256": binding["candidate_set_sha256"],
        "response_sha256": binding["response_sha256"],
        "provider": provider,
        "resolver_result": binding["resolver_result"],
        "evidence_only": True,
        "authoritative_behavior_changed": False,
        "authority": authority,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Offline validation of one captured System-One shadow receipt. "
            "This command performs no network access and grants no authority."
        )
    )
    parser.add_argument("--request", required=True, type=pathlib.Path)
    parser.add_argument("--response", required=True, type=pathlib.Path)
    parser.add_argument("--expected-provider-id")
    parser.add_argument("--expected-model-id")
    parser.add_argument("--expected-model-artifact-sha256")
    parser.add_argument("--require-model-artifact", action="store_true")
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()

    try:
        request_payload = _load_mapping(args.request, "request")
        response_payload = _load_mapping(args.response, "response")
        result = validate_capture(
            request_payload,
            response_payload,
            expected_provider_id=args.expected_provider_id,
            expected_model_id=args.expected_model_id,
            expected_model_artifact_sha256=(
                args.expected_model_artifact_sha256
            ),
            require_model_artifact=args.require_model_artifact,
        )
    except (TypeError, ValueError) as exc:
        print(f"SYSTEM_ONE_RECEIPT_ACCEPTANCE: BLOCKED {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    else:
        print(rendered, end="")
    print("SYSTEM_ONE_RECEIPT_ACCEPTANCE: PASS", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
