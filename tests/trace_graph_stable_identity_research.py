"""Deterministic operation identity from VERIFIED ingress identity + request ID.

This is pure research glue, deliberately NOT a gateway that authenticates
clients. The caller MUST derive verified_subject from a trusted, signed auth
context, never from worker JSON, an X-Forwarded-* header or raw request body.
No production route imports it. SHA-256 is collision-resistant identity
derivation, NOT an authorization signature or per-user encryption mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from collections.abc import Mapping

_SUBJECT = re.compile(r"^[A-Za-z0-9_:/.@-]{1,160}$")
_REQUEST = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
_PLAN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class InvalidStableRequest(ValueError):
    pass


@dataclass(frozen=True)
class StableOperation:
    operation: str
    request_digest: str


def derive_stable_operation(verified_subject: str, client_request_id: str,
                            plan_id: str, parameters: Mapping[str, object]
                            ) -> StableOperation:
    """Use only AFTER auth and strict plan/parameter validation.

    Same verified principal + same request-id returns the same operation
    across retries, regardless of plan. A changed plan/parameters has a
    different request_digest and must be rejected as a payload conflict by
    EtcdQuorumFence, NOT assigned another operation.
    """
    if (not isinstance(verified_subject, str)
            or not _SUBJECT.fullmatch(verified_subject)
            or not isinstance(client_request_id, str)
            or not _REQUEST.fullmatch(client_request_id)
            or not isinstance(plan_id, str)
            or not _PLAN.fullmatch(plan_id)
            or not isinstance(parameters, Mapping)):
        raise InvalidStableRequest("UNVERIFIED_OR_INVALID_REQUEST_IDENTITY")
    try:
        values = dict(parameters)
        if (not all(isinstance(key, str) and _PLAN.fullmatch(key)
                    for key in values) or
                any(type(value) not in (str, int, float, bool, type(None))
                    for value in values.values())):
            raise InvalidStableRequest("INVALID_CANONICAL_REQUEST_PARAMETERS")
        fingerprint = json.dumps(
            {"plan": plan_id, "parameters": values}, sort_keys=True,
            separators=(",", ":"), allow_nan=False
        ).encode()
        if len(fingerprint) > 4096:
            raise InvalidStableRequest("OVERSIZED_REQUEST_FINGERPRINT")
        identity = json.dumps(
            {"subject": verified_subject, "request_id": client_request_id},
            sort_keys=True, separators=(",", ":")
        ).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidStableRequest("INVALID_CANONICAL_REQUEST_PARAMETERS") from exc
    return StableOperation(
        operation="auth:" + sha256(identity).hexdigest(),
        request_digest=sha256(fingerprint).hexdigest()
    )
