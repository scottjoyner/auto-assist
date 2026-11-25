#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
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


MAX_CAPTURE_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 64


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key")
        obj[key] = value
    return obj


def _reject_constant(value):
    raise ValueError("nonfinite JSON constant")


def _check_json_depth(raw: bytes) -> None:
    # Count structural delimiters outside quoted strings before parsing.
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("JSON depth limit")
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                raise ValueError("invalid JSON structure")


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _load_mapping(path: pathlib.Path, label: str) -> dict[str, Any]:
    # No final-component symlinks, blocking FIFO opens, or unbounded reads.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_CAPTURE_BYTES:
            raise ValueError(f"{label} invalid input file")
        raw = stream.read(MAX_CAPTURE_BYTES + 1)
        after = os.fstat(stream.fileno())
        if (len(raw) > MAX_CAPTURE_BYTES or len(raw) != before.st_size
                or _file_identity(before) != _file_identity(after)):
            raise ValueError(f"{label} unstable or oversized input")
    _check_json_depth(raw)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                       parse_constant=_reject_constant)
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be a JSON object")
    return value


def _publish_new_report(path: pathlib.Path, rendered: str) -> None:
    """Publish a complete private report without replacing existing evidence.

    Parent must already exist. Hard-link publication is atomic and exclusive;
    an unsupported filesystem fails closed. Not a custody or signature proof.
    """
    fd, temp_name = tempfile.mkstemp(prefix=".shadow-receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp_name, path, follow_symlinks=False)
    finally:
        os.unlink(temp_name)


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
        rendered = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output is not None:
            _publish_new_report(args.output, rendered)
            print(args.output)
        else:
            print(rendered, end="")
    except (OSError, TypeError, ValueError, RecursionError, OverflowError) as exc:
        # Validation errors may include untrusted input values; never echo them.
        print(f"SYSTEM_ONE_RECEIPT_ACCEPTANCE: BLOCKED {type(exc).__name__}", file=sys.stderr)
        return 1
    print("SYSTEM_ONE_RECEIPT_ACCEPTANCE: PASS", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
