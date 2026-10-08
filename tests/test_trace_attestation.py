"""Isolated, synthetic HMAC trace-claim contract tests. No fleet secrets/data."""
import hashlib
import hmac
import json
import threading
import uuid

import pytest

from assistx.trace_attestation import (
    SCHEMA, DOMAIN, ExpectedEvent, RegisteredSource, SandboxReplayLedger,
    verify_source_claim,
)

CID = "11111111-2222-4333-8444-555555555555"
NOW = 1_780_000_000_000
DIGEST = hashlib.sha256(b"synthetic-test-event-only").hexdigest()
KEY_A = b"research-example-A" + b"A" * 32
KEY_B = b"research-example-B" + b"B" * 32

def registration(**overrides):
    values = dict(
        key_id="fixture-key-a", node_id="fixture-node-a", agent_id="fixture-agent-a",
        source_service="fixture-service", generation="boot-generation-12",
        secret=KEY_A, active=True, not_before_ms=NOW - 100_000,
        not_after_ms=NOW + 200_000
    )
    values.update(overrides)
    return RegisteredSource(**values)

def expected(**overrides):
    values = dict(correlation_id=CID, task_id="fixture-task-1",
                  event_id="event:fixture-1", event_sha256=DIGEST)
    values.update(overrides)
    return ExpectedEvent(**values)

def claim(**overrides):
    fields = dict(schema=SCHEMA, key_id="fixture-key-a",
                  node_id="fixture-node-a", agent_id="fixture-agent-a",
                  source_service="fixture-service", generation="boot-generation-12",
                  correlation_id=CID, task_id="fixture-task-1",
                  event_id="event:fixture-1", event_sha256=DIGEST,
                  issued_at_ms=NOW - 1000, expires_at_ms=NOW + 10_000,
                  nonce="ab" * 16)
    fields.update(overrides)
    return fields

def encode(c, secret=KEY_A, override_sig=None):
    canonical=DOMAIN+json.dumps(c,sort_keys=True,separators=(",",":"),
                                ensure_ascii=True,allow_nan=False).encode()
    sig=hmac.new(secret,canonical,hashlib.sha256).hexdigest()
    return json.dumps({"claim":c,"signature":override_sig or sig},sort_keys=True)

def verdict(tmp_path, c=None, reg=None, exp=None, now=NOW, ledger=True, raw=None):
    if c is None:c=claim()
    if reg is None:reg=registration()
    if exp is None:exp=expected()
    store=SandboxReplayLedger(tmp_path/"replay.sqlite") if ledger else None
    return verify_source_claim(
        raw if raw is not None else encode(c),
        {reg.key_id:reg}, exp, now, store
    )

def test_signed_fixture_accepted_only_as_local_claim_integrity(tmp_path):
    r=verdict(tmp_path)
    assert r["status"]=="LOCAL_CLAIM_INTEGRITY_ACCEPTED"
    assert r["claim_integrity_valid"] and r["local_replay_accepted"]
    assert not r["execution_attested"]
    assert not r["source_hardware_attested"]
    assert not r["custody_independently_witnessed"]
    assert not r["production_authorized"]
    assert not any(x in r for x in ("secret","key","node_id","task_id","event_id"))

def test_no_replay_ledger_is_not_accepted(tmp_path):
    r=verdict(tmp_path,ledger=False)
    assert r["status"]=="REPLAY_LEDGER_REQUIRED"
    assert r["claim_integrity_valid"] is True
    assert not r["local_replay_accepted"]

@pytest.mark.parametrize("field,value",[
    ("node_id","fixture-node-b"),("agent_id","fixture-agent-b"),
    ("source_service","evil-service"),("generation","boot-generation-13"),
    ("correlation_id","11111111-2222-4333-8444-666666666666"),
    ("task_id","other-task"),("event_id","event:other"),
    ("event_sha256","1"*64),("nonce","cd"*16),
])
def test_tampering_signed_fields_fails_signature(tmp_path,field,value):
    signed=encode(claim())
    payload=json.loads(signed)
    payload["claim"][field]=value
    r=verdict(tmp_path, raw=json.dumps(payload))
    assert not r["claim_integrity_valid"] or not r["local_replay_accepted"]

@pytest.mark.parametrize("field,value",[
    ("node_id","fixture-node-b"),("agent_id","fixture-agent-b"),
    ("source_service","other-service"),("generation","boot-generation-13"),
])
def test_valid_signer_cannot_rename_registered_identity(tmp_path,field,value):
    r=verdict(tmp_path,c=claim(**{field:value}))
    assert r["status"]=="REGISTERED_IDENTITY_MISMATCH"

@pytest.mark.parametrize("field,value",[
    ("correlation_id","aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
    ("task_id","other-task"),("event_id","event:other"),
    ("event_sha256","0"*64)
])
def test_valid_signer_cannot_substitute_independently_expected_event(tmp_path,field,value):
    r=verdict(tmp_path,c=claim(**{field:value}))
    assert r["status"]=="EVENT_BINDING_MISMATCH"

def test_wrong_key_or_revocation_or_rotation(tmp_path):
    wrong=verdict(tmp_path,raw=encode(claim(),secret=KEY_B))
    assert wrong["status"]=="INVALID_CLAIM_SIGNATURE"
    revoked=verdict(tmp_path,reg=registration(active=False))
    assert revoked["status"]=="KEY_UNAVAILABLE_OR_REVOKED"
    rotated=verdict(tmp_path,reg=registration(generation="boot-generation-13"))
    assert rotated["status"]=="REGISTERED_IDENTITY_MISMATCH"

def test_missing_or_unregistered_signer(tmp_path):
    r=verify_source_claim(encode(claim()),{},expected(),NOW,
                          SandboxReplayLedger(tmp_path/"missing.sqlite"))
    assert r["status"]=="KEY_UNAVAILABLE_OR_REVOKED"

@pytest.mark.parametrize("mut",[
    dict(expires_at_ms=NOW-1),
    dict(issued_at_ms=NOW+10001),
    dict(issued_at_ms=NOW-200000),
    dict(expires_at_ms=NOW+200000),
    dict(issued_at_ms=NOW+20000),
    dict(expires_at_ms=NOW-1000),
])
def test_bad_time_windows_rejected(tmp_path,mut):
    r=verdict(tmp_path,c=claim(**mut))
    assert r["status"]=="CLAIM_OUTSIDE_TIME_WINDOW"

def test_registered_key_validity_is_independent(tmp_path):
    r=verdict(tmp_path,reg=registration(not_after_ms=NOW+100))
    assert r["status"]=="CLAIM_OUTSIDE_TIME_WINDOW"
    r=verdict(tmp_path,reg=registration(not_before_ms=NOW))
    assert r["status"]=="CLAIM_OUTSIDE_TIME_WINDOW"

def test_nonce_replay_rejected_after_ledger_reopen(tmp_path):
    assert verdict(tmp_path)["local_replay_accepted"]
    r=verdict(tmp_path)
    assert r["status"]=="DUPLICATE_NONCE_OR_EVENT"
    assert r["claim_integrity_valid"] and not r["local_replay_accepted"]

def test_different_nonce_same_event_rejected_as_duplicate(tmp_path):
    assert verdict(tmp_path)["local_replay_accepted"]
    r=verdict(tmp_path,c=claim(nonce="cd"*16))
    assert r["status"]=="DUPLICATE_NONCE_OR_EVENT"

def test_same_nonce_for_different_event_rejected(tmp_path):
    assert verdict(tmp_path)["local_replay_accepted"]
    c=claim(event_id="event:fixture-2",event_sha256="c"*64)
    r=verdict(tmp_path,c=c,exp=expected(event_id=c["event_id"],event_sha256=c["event_sha256"]))
    assert r["status"]=="DUPLICATE_NONCE_OR_EVENT"

def test_distinct_claim_events_admitted_separately(tmp_path):
    assert verdict(tmp_path)["local_replay_accepted"]
    c=claim(nonce="cd"*16,event_id="event:fixture-2",event_sha256="c"*64)
    r=verdict(tmp_path,c=c,exp=expected(event_id=c["event_id"],event_sha256=c["event_sha256"]))
    assert r["local_replay_accepted"]

def test_concurrent_duplicate_admission_at_most_once(tmp_path):
    ledger=SandboxReplayLedger(tmp_path/"concurrent.sqlite")
    raw=encode(claim())
    barrier=threading.Barrier(5)
    output=[]
    def worker():
        barrier.wait()
        output.append(verify_source_claim(raw,{"fixture-key-a":registration()},
                                          expected(),NOW,ledger))
    ts=[threading.Thread(target=worker) for _ in range(4)]
    for t in ts:t.start()
    barrier.wait()
    for t in ts:t.join(timeout=5)
    assert all(not t.is_alive() for t in ts)
    assert sum(x["local_replay_accepted"] for x in output)==1
    assert sorted(x["status"] for x in output).count("DUPLICATE_NONCE_OR_EVENT")==3

@pytest.mark.parametrize("invalid",[
    {"generation":["boot-generation-12"]}, {"agent_id":"a\nb"},
    {"event_sha256":"AA"*32},{"nonce":"ab" * 5},
    {"issued_at_ms":True},{"expires_at_ms":1.0},
    {"task_id":"x"*129},{"correlation_id":"not-a-uuid"},
    {"schema":"unexpected-schema"},{"extra_field":"wat"},
])
def test_shape_rejects_bad_claims(tmp_path,invalid):
    r=verdict(tmp_path,c=claim(**invalid))
    assert r["status"] in {"INVALID_CLAIM_SCHEMA","INVALID_CLAIM"}

def test_missing_key_rejected(tmp_path):
    c=claim()
    del c["node_id"]
    assert verdict(tmp_path,c=c)["status"]=="INVALID_CLAIM_SCHEMA"

def test_duplicate_keys_in_json_envelope_or_claim_rejected(tmp_path):
    raw=encode(claim())
    assert verdict(tmp_path,raw=raw.replace('"signature":','"signature":"0", "signature":'))["status"]=="INVALID_CLAIM"
    text=raw.replace('"agent_id":', '"agent_id":"malicious", "agent_id":')
    assert verdict(tmp_path,raw=text)["status"]=="INVALID_CLAIM"

def test_rejects_malformed_json_or_extra_envelope_fields(tmp_path):
    for raw in ('{', '{}','[]',
        json.dumps({"claim":claim(),"signature":"0"*64,"untrusted":True})):
        assert not verdict(tmp_path,raw=raw)["local_replay_accepted"]

def test_weak_test_keys_fail_closed(tmp_path):
    r=verdict(tmp_path,reg=registration(secret=b"short"))
    assert r["status"]=="UNSAFE_TEST_KEY"

def test_raw_envelope_size_limit(tmp_path):
    r=verdict(tmp_path,raw=" "*5000)
    assert r["status"]=="INVALID_CLAIM"

def test_invalid_verifier_context_cannot_admit(tmp_path):
    r=verify_source_claim(encode(claim()),{"fixture-key-a":registration()},
                          expected(),True,SandboxReplayLedger(tmp_path/"clock.sqlite"))
    assert r["status"]=="INVALID_VERIFIER_INPUT"


def test_replacing_live_ledger_file_fails_closed(tmp_path):
    store=SandboxReplayLedger(tmp_path/"tamper.sqlite")
    initial=verify_source_claim(encode(claim()),{"fixture-key-a":registration()},
                                expected(),NOW,store)
    assert initial["local_replay_accepted"]
    path=tmp_path/"tamper.sqlite"
    path.rename(tmp_path/"moved.sqlite")
    # Attempt a fresh database without prior replay history, under the same name.
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE dummy (x)")
    result=verify_source_claim(encode(claim()),{"fixture-key-a":registration()},
                               expected(),NOW,store)
    assert result["status"]=="REPLAY_LEDGER_UNAVAILABLE"
    assert not result["local_replay_accepted"]


def test_ledger_symlink_swap_fails_closed(tmp_path):
    store=SandboxReplayLedger(tmp_path/"link.sqlite")
    p=tmp_path/"link.sqlite"
    p.rename(tmp_path/"original.sqlite")
    p.symlink_to(tmp_path/"original.sqlite")
    result=verify_source_claim(encode(claim()),{"fixture-key-a":registration()},
                               expected(),NOW,store)
    assert result["status"]=="REPLAY_LEDGER_UNAVAILABLE"
    assert not result["local_replay_accepted"]


def test_new_ledger_rejects_existing_symlink(tmp_path):
    original=SandboxReplayLedger(tmp_path/"original.sqlite")
    (tmp_path/"alias.sqlite").symlink_to(tmp_path/"original.sqlite")
    with pytest.raises(ValueError,match="UNSAFE_LEDGER_PARENT"):
        SandboxReplayLedger(tmp_path/"alias.sqlite")


def test_legacy_static_node_token_is_not_signed_event_evidence(tmp_path):
    from assistx.node_identity import verify_node_token
    assert verify_node_token({"fixture-node-a":"fixture-bearer-token"},
                             "fixture-node-a","fixture-bearer-token") is None
    # A successfully checked bearer token cannot substitute for signed
    # binding to generation, event digest, task and correlation ID.
    result=verdict(tmp_path,raw=json.dumps({
        "claim":claim(),"signature":"0"*64
    }))
    assert result["status"]=="INVALID_CLAIM_SIGNATURE"
    assert not result["execution_attested"]


def test_registered_source_repr_does_not_echo_provisioned_key():
    value=registration()
    assert "research-example" not in repr(value)
    assert "secret=" not in repr(value)
