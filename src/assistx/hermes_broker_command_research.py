"""Deny-by-default, transport-free Hermes broker command prototype.

This is NOT an HTTP service, authentication implementation, token issuer,
deployment entrypoint, or remote dispatch endpoint. The "principal" comes
from an injected upstream context and is NOT cryptographically verified here.
The mutation DB credential must be held only by a future isolated service;
this module does not create/load/return one. Only fixed source-generation-
enrolled heartbeat and completion functions can be invoked.

All replies HOLD. A successful source mutation cannot authorize an external
effect or prove all fleet writers participated.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .hermes_source_generation_research import GenerationProtocolError, OWNER
from .hermes_source_heartbeat_research import renew_candidate_lease
from .hermes_source_completion_research import complete_candidate_claim
from .hermes_source_receipt_research import (
    _normalized, commit_source_receipt, reconcile_source_receipt,
)

_BASE=frozenset(("operation","expected_generation","task_id",
                  "claim_id","session_id","attempt"))
_FIELDS={
    "heartbeat": _BASE | {"ttl_ms"},
    "complete": _BASE,
    "heartbeat_recorded": _BASE | {"ttl_ms","receipt_key"},
    "complete_recorded": _BASE | {"receipt_key"},
    "reconcile_heartbeat": _BASE | {"ttl_ms","receipt_key"},
    "reconcile_complete": _BASE | {"receipt_key"},
}
_RECEIPT_OPERATIONS={
    "heartbeat_recorded":("heartbeat",False),
    "complete_recorded":("complete",False),
    "reconcile_heartbeat":("heartbeat",True),
    "reconcile_complete":("complete",True),
}

class CandidateBrokerCommandError(ValueError):
    """Redacted failed command admission."""


def dispatch_candidate_command(
    command: Mapping[str, Any],
    *,
    trusted_channel_principal: str,
    session_factory: Callable[[], Any],
) -> dict[str, Any]:
    """Run one allowlisted DB mutation behind an injected session.

    A future authenticated transport must establish principal identity
    OUTSIDE the JSON command, carry independent replay protection and keep
    the session factory/credentials inaccessible to callers. Neither is
    implemented here. A forged trusted_channel_principal is not prevented.
    """
    if type(trusted_channel_principal) is not str or trusted_channel_principal != OWNER:
        raise CandidateBrokerCommandError("unqualified_caller")
    if not isinstance(command,Mapping):
        raise CandidateBrokerCommandError("invalid_command")
    operation=command.get("operation")
    if type(operation) is not str or operation not in _FIELDS:
        raise CandidateBrokerCommandError("operation_not_allowed")
    if set(command.keys()) != _FIELDS[operation]:
        raise CandidateBrokerCommandError("command_fields_not_allowed")
    if not callable(session_factory):
        raise CandidateBrokerCommandError("session_unavailable")
    values={name:command[name] for name in _FIELDS[operation] if name!="operation"}
    # Fully type-check before any session factory invocation. The called
    # fixed adapter enforces regex IDs, positive integer generation/attempt,
    # finite TTL and database-clock lease.
    if (type(values["expected_generation"]) is not int
            or values["expected_generation"] < 0
            or type(values["attempt"]) is not int
            or values["attempt"] < 1
            or any(type(values[name]) is not str or
                   not 1 <= len(values[name]) <= 128 or
                   not all(c.isascii() and (c.isalnum() or c in "-_")
                           for c in values[name])
                   for name in ("task_id","claim_id","session_id"))
            or (operation in ("heartbeat","heartbeat_recorded","reconcile_heartbeat")
                and (type(values["ttl_ms"]) is not int or
                     not 1000 <= values["ttl_ms"] <= 60000))):
        raise CandidateBrokerCommandError("invalid_command_identity")
    recorded=operation in _RECEIPT_OPERATIONS
    reconciled=False
    if recorded:
        source_op,reconciled=_RECEIPT_OPERATIONS[operation]
        try:
            # Validate UUIDv4, exact operation/TTL, digest inputs before
            # invoking session_factory; never permit a raw query argument.
            _normalized(operation=source_op,**{**values,"ttl_ms":values.get("ttl_ms")})
        except GenerationProtocolError:
            raise CandidateBrokerCommandError("invalid_command_identity") from None
    try:
        with session_factory() as session:
            if recorded:
                source_op,_=_RECEIPT_OPERATIONS[operation]
                call=(reconcile_source_receipt if reconciled
                      else commit_source_receipt)
                result=call(session,operation=source_op,**values)
            elif operation=="heartbeat":
                result=renew_candidate_lease(session,**values)
            else:
                result=complete_candidate_claim(session,**values)
    except (GenerationProtocolError,ValueError):
        # Keep internal query/state/session details out of responses.
        raise CandidateBrokerCommandError("unqualified_source_claim") from None
    return {
        "schema":1,
        "operation":operation,
        "candidate_generation":result["candidate_generation"],
        "source_transaction_accepted":True,
        "source_receipt_reconciled":reconciled,
        "source_receipt_committed":recorded and not reconciled,
        "authenticated_transport_qualified":False,
        "exclusive_writer_credentials_verified":False,
        "all_writers_generation_fenced":False,
        "external_resource_effect_verified":False,
        "independent_revocation_custody_verified":False,
        "production_dispatch_authorized":False,
        "decision":"HOLD",
    }
