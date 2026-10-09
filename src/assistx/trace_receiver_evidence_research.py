"""Research-only Ed25519 envelope for independently observed Neo4j closure.

This is a *receipt integrity* primitive, NOT physical closure truth, distributed
admission, production authority, a replay store, or automatic slot release.
No runtime/API modules import it. Observers must obtain evidence via their own
Neo4j server connection, not from an API worker's exit/timeout callback.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

SCHEMA="assistx-neo4j-independent-receiver-research-v1"
REQUIRED=frozenset({
    "schema","epoch","token","query_ref","receiver_nonce",
    "graph_container_id","server_transaction_id","observed_running_before",
    "terminate_command","terminate_server_message","server_reply_exact_id",
    "post_termination_same_id_visible","observer_source","terminal_verdict",
})
HEX32=re.compile(r"^[a-f0-9]{32}$")
HEX64=re.compile(r"^[a-f0-9]{64}$")
TRANSACTION=re.compile(r"^neo4j-transaction-[0-9]+$")
QREF=re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")


def _uuid4(s:Any)->bool:
    try:
        return type(s) is str and str(uuid.UUID(s))==s and uuid.UUID(s).version==4
    except (ValueError,TypeError,AttributeError):
        return False


def validate_observation(data: Any)->bool:
    if type(data) is not dict or set(data)!=REQUIRED:
        return False
    if data["schema"]!=SCHEMA or not _uuid4(data["epoch"]):
        return False
    if type(data["token"]) is not str or not HEX32.fullmatch(data["token"]):
        return False
    if type(data["query_ref"]) is not str or not QREF.fullmatch(data["query_ref"]):
        return False
    if not _uuid4(data["receiver_nonce"]):
        return False
    if type(data["graph_container_id"]) is not str or not HEX64.fullmatch(data["graph_container_id"]):
        return False
    if type(data["server_transaction_id"]) is not str or not TRANSACTION.fullmatch(data["server_transaction_id"]):
        return False
    if data["observed_running_before"] is not True:
        return False
    if data["terminate_command"]!="TERMINATE TRANSACTIONS":
        return False
    if data["terminate_server_message"]!="Transaction terminated.":
        return False
    if data["server_reply_exact_id"] is not True:
        return False
    samples=data["post_termination_same_id_visible"]
    if type(samples) is not list or len(samples)<4 or len(samples)>16 or any(type(x) is not bool or x for x in samples):
        return False
    if data["observer_source"]!="guarded-disposable-direct-Neo4j":
        return False
    if data["terminal_verdict"]!="receiver-observed-terminated":
        return False
    return True


def canonical(data:dict)->bytes:
    if not validate_observation(data):
        raise ValueError("INVALID_RECEIVER_EVIDENCE")
    return json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("ascii")


def receiver_sign_only(data:dict, secret:Ed25519PrivateKey)->bytes:
    """Only a separate receiver process should own the private key."""
    if not isinstance(secret,Ed25519PrivateKey):
        raise TypeError("RECEIVER_PRIVATE_KEY_REQUIRED")
    return secret.sign(canonical(data))


def verify_research_evidence(
    data: Any,
    signature:bytes,
    public_key:bytes,
    *,
    expected_epoch:str,
    expected_token:str,
    expected_query_ref:str,
    expected_graph_container_id:str,
    expected_transaction_id:str,
    expected_receiver_nonce:str,
)->bool:
    """Bind exact observed transaction, graph identity and request nonce.

    Returns evidence *authenticity*, NOT production authorization. It does not
    call DurableTraceReadLedger. No accepted/used-once durable nonce custody.
    """
    try:
        if not validate_observation(data):
            return False
        required_values={
            "epoch":expected_epoch,"token":expected_token,
            "query_ref":expected_query_ref,
            "graph_container_id":expected_graph_container_id,
            "server_transaction_id":expected_transaction_id,
            "receiver_nonce":expected_receiver_nonce,
        }
        if any(data[k]!=v for k,v in required_values.items()):
            return False
        if type(signature) is not bytes or len(signature)!=64:
            return False
        if type(public_key) is not bytes or len(public_key)!=32:
            return False
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature,canonical(data))
        return True
    except (InvalidSignature,ValueError,TypeError,KeyError):
        return False
