"""Canonical task-payload binding shared by claim issuer and executor.

This is an integrity check against an *observed* mutable source Task, not an
independently administered revocation authority or atomic effect transaction.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

MAX_CLAIMED_PAYLOAD_BYTES = 65_536
_BINDING_DOMAIN = b"assistx-task-payload-binding-v1\x00"


class ClaimBindingError(ValueError):
    """The source payload cannot safely be bound to a signed executor claim."""


def _distinct_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ClaimBindingError("duplicate_source_payload_key")
        result[key] = value
    return result


def _reject_nonfinite(_constant: str) -> Any:
    raise ClaimBindingError("nonfinite_source_payload")


def canonical_claimed_payload(value: Any) -> tuple[dict[str, Any], bytes]:
    """Parse/freeze a JSON object with deterministic, bounded UTF-8 encoding.

    An explicit canonical field never falls back to an alternative field. Use
    this across issuer/worker/resource checking to avoid JSON normalizer drift.
    """
    if isinstance(value, str):
        try:
            encoded = value.encode("utf-8")
            if not value.strip() or len(encoded) > MAX_CLAIMED_PAYLOAD_BYTES:
                raise ClaimBindingError("invalid_source_payload_size")
            parsed = json.loads(
                value,
                object_pairs_hook=_distinct_object_keys,
                parse_constant=_reject_nonfinite,
            )
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ClaimBindingError("invalid_source_payload") from exc
    elif isinstance(value, Mapping):
        parsed = dict(value)
    else:
        raise ClaimBindingError("source_payload_not_object")
    if not isinstance(parsed, dict):
        raise ClaimBindingError("source_payload_not_object")
    try:
        frozen = json.dumps(
            parsed, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ClaimBindingError("invalid_source_payload") from exc
    if len(frozen) > MAX_CLAIMED_PAYLOAD_BYTES:
        raise ClaimBindingError("invalid_source_payload_size")
    return json.loads(frozen), frozen


def source_task_payload(task: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    if "payload_json" in task:
        return canonical_claimed_payload(task["payload_json"])
    if "payload" in task:
        return canonical_claimed_payload(task["payload"])
    raise ClaimBindingError("source_payload_missing")


def payload_binding_digest(payload: Any) -> str:
    _, encoded = canonical_claimed_payload(payload)
    return "sha256:" + hashlib.sha256(_BINDING_DOMAIN + encoded).hexdigest()
