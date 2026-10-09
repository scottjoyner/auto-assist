"""No network: exact-identity Ed25519 receiver evidence integrity tests.

The tests DO NOT equate a correctly signed claim with physical truth,
distributed authority, or admission release.
"""
from copy import deepcopy
import uuid
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.trace_receiver_evidence_research import (
    SCHEMA, canonical, receiver_sign_only, verify_research_evidence,
    validate_observation,
)


def fixture():
    observation={
        "schema":SCHEMA,
        "epoch":str(uuid.uuid4()),
        "token":"a"*32,
        "query_ref":"synthetic-query-1",
        "receiver_nonce":str(uuid.uuid4()),
        "graph_container_id":"b"*64,
        "server_transaction_id":"neo4j-transaction-17",
        "observed_running_before":True,
        "terminate_command":"TERMINATE TRANSACTIONS",
        "terminate_server_message":"Transaction terminated.",
        "server_reply_exact_id":True,
        "post_termination_same_id_visible":[False]*6,
        "observer_source":"guarded-disposable-direct-Neo4j",
        "terminal_verdict":"receiver-observed-terminated",
    }
    key=Ed25519PrivateKey.generate()
    pub=key.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    expected={
        "expected_epoch":observation["epoch"],
        "expected_token":observation["token"],
        "expected_query_ref":observation["query_ref"],
        "expected_graph_container_id":observation["graph_container_id"],
        "expected_transaction_id":observation["server_transaction_id"],
        "expected_receiver_nonce":observation["receiver_nonce"],
    }
    return observation,key,pub,expected


def test_exact_receiver_envelope_roundtrip_but_not_release():
    evidence,key,pub,expected=fixture()
    assert validate_observation(evidence)
    signature=receiver_sign_only(evidence,key)
    assert len(signature)==64
    assert verify_research_evidence(evidence,signature,pub,**expected)
    assert "admission_authorized" not in evidence
    assert "automatic_release" not in evidence


@pytest.mark.parametrize("mutation",[
    {"schema":"v0"},
    {"epoch":"wrong"},
    {"token":"f"*31},
    {"query_ref":"../etc/passwd"},
    {"receiver_nonce":"wrong"},
    {"graph_container_id":"c"*64},
    {"server_transaction_id":"old-worker-id"},
    {"observed_running_before":False},
    {"terminate_command":"CLIENT_TIMEOUT"},
    {"terminate_server_message":"Transaction not found."},
    {"server_reply_exact_id":False},
    {"post_termination_same_id_visible":[True,False,False,False]},
    {"post_termination_same_id_visible":[False]},
    {"observer_source":"worker-assertion"},
    {"terminal_verdict":"unknown"},
    {"extra":"unexpected"},
])
def test_invalid_observations_cannot_be_signed(mutation):
    ev,key,pub,expected=fixture()
    ev.update(mutation)
    assert not validate_observation(ev)
    with pytest.raises(ValueError):
        receiver_sign_only(ev,key)


@pytest.mark.parametrize("binding",[
    ("expected_epoch",lambda d:str(uuid.uuid4())),
    ("expected_token",lambda d:"c"*32),
    ("expected_query_ref",lambda d:"other-query"),
    ("expected_graph_container_id",lambda d:"d"*64),
    ("expected_transaction_id",lambda d:"neo4j-transaction-18"),
    ("expected_receiver_nonce",lambda d:str(uuid.uuid4())),
])
def test_valid_signature_not_transferable_to_another_identity(binding):
    ev,key,pub,expected=fixture()
    signed=receiver_sign_only(ev,key)
    arg,fn=binding
    expected[arg]=fn(ev)
    assert not verify_research_evidence(ev,signed,pub,**expected)


def test_wrong_key_or_tampered_evidence_rejected():
    ev,key,pub,expected=fixture()
    signature=receiver_sign_only(ev,key)
    other=Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    assert not verify_research_evidence(ev,signature,other,**expected)
    assert not verify_research_evidence(ev,b"",pub,**expected)
    changed=deepcopy(ev)
    changed["server_transaction_id"]="neo4j-transaction-999"
    assert not verify_research_evidence(changed,signature,pub,**expected)


def test_receiver_nonce_prevents_cross_request_replay_only_when_externally_pinned():
    ev,key,pub,expected=fixture()
    sig=receiver_sign_only(ev,key)
    assert verify_research_evidence(ev,sig,pub,**expected)
    future={**expected,"expected_receiver_nonce":str(uuid.uuid4())}
    assert not verify_research_evidence(ev,sig,pub,**future)
    # Rechecking the SAME tuple still verifies: durable consumed-nonce
    # tracking and distributed rollback protection are NOT implemented.
    assert verify_research_evidence(ev,sig,pub,**expected)


def test_proof_data_requires_every_observation_and_canonical_json():
    ev,key,pub,expected=fixture()
    assert canonical(ev)==canonical(dict(reversed(list(ev.items()))))
    for removed in ("receiver_nonce","server_transaction_id","post_termination_same_id_visible"):
        changed=deepcopy(ev)
        del changed[removed]
        assert not validate_observation(changed)


def test_private_key_cannot_be_replaced_by_untrusted_worker_claim():
    ev,key,pub,expected=fixture()
    with pytest.raises(TypeError,match="RECEIVER_PRIVATE_KEY_REQUIRED"):
        receiver_sign_only(ev,pub)
