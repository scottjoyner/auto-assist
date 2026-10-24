"""Fixed broker recorded writes and read-only source receipt reconciliation."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from assistx import hermes_broker_command_research as broker
from assistx.hermes_source_generation_research import GenerationProtocolError
from assistx.hermes_broker_unix_boundary_research import send_holding_reply
import socket
import json

KEY="4f36489f-3b2c-4410-a22f-acf5467528a1"

def command(op):
    d={"operation":op,"receipt_key":KEY,"expected_generation":2,
       "task_id":"synthetic-task","claim_id":"synthetic-claim",
       "session_id":"synthetic-session","attempt":1}
    if op in ("heartbeat_recorded","reconcile_heartbeat"):
        d["ttl_ms"]=15000
    return d

class Session:
    def __init__(self):
        self.opened=0
    def __call__(self):
        self.opened+=1
        return self
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False

@pytest.mark.parametrize("op,source,should_reconcile",[
    ("heartbeat_recorded","heartbeat",False),
    ("complete_recorded","complete",False),
    ("reconcile_heartbeat","heartbeat",True),
    ("reconcile_complete","complete",True),
])
def test_allowlisted_route_calls_only_correct_receipt_adapter(monkeypatch,op,source,should_reconcile):
    s=Session()
    observed=[]
    def commit(session,**kw):
        observed.append(("commit",session,kw))
        return {"candidate_generation":3}
    def reconcile(session,**kw):
        observed.append(("reconcile",session,kw))
        return {"candidate_generation":3}
    monkeypatch.setattr(broker,"commit_source_receipt",commit)
    monkeypatch.setattr(broker,"reconcile_source_receipt",reconcile)
    out=broker.dispatch_candidate_command(command(op),
        trusted_channel_principal="hermes-local",session_factory=s)
    assert s.opened==1
    assert len(observed)==1
    mode,session,kw=observed[0]
    assert session is s
    assert mode==("reconcile" if should_reconcile else "commit")
    assert kw["operation"]==source
    assert kw["receipt_key"]==KEY
    assert kw["attempt"]==1
    assert ("ttl_ms" in kw)==(source=="heartbeat")
    assert out["candidate_generation"]==3
    assert out["source_receipt_reconciled"] is should_reconcile
    assert out["source_receipt_committed"] is (not should_reconcile)
    assert out["decision"]=="HOLD"
    for k in ("authenticated_transport_qualified","exclusive_writer_credentials_verified",
              "independent_revocation_custody_verified","external_resource_effect_verified",
              "all_writers_generation_fenced","production_dispatch_authorized"):
        assert out[k] is False
    assert "receipt_key" not in out
    assert "task_id" not in out

@pytest.mark.parametrize("op",[
    "heartbeat_recorded","complete_recorded","reconcile_heartbeat",
    "reconcile_complete",
])
@pytest.mark.parametrize("field,value",[
    ("receipt_key","other"),("receipt_key",KEY.upper()),
    ("receipt_key","f53c713e-fd9d-13ed-955a-0c651d57f560"),
    ("attempt",True),("expected_generation",False),
    ("claim_id","../other"),("task_id",""),
])
def test_bad_identity_fails_before_db_session(op,field,value):
    s=Session()
    d=command(op);d[field]=value
    with pytest.raises(broker.CandidateBrokerCommandError,match="invalid_command_identity"):
        broker.dispatch_candidate_command(d,
            trusted_channel_principal="hermes-local",session_factory=s)
    assert s.opened==0

@pytest.mark.parametrize("op",[
    "heartbeat_recorded","complete_recorded","reconcile_heartbeat","reconcile_complete",
])
@pytest.mark.parametrize("field",["query","cypher","password","owner","source_operation","raw"])
def test_extra_fields_fails_before_session(op,field):
    s=Session()
    d=command(op);d[field]="synthetic"
    with pytest.raises(broker.CandidateBrokerCommandError,match="command_fields_not_allowed"):
        broker.dispatch_candidate_command(d,
            trusted_channel_principal="hermes-local",session_factory=s)
    assert s.opened==0

def test_mismatched_receipt_lookup_is_redacted(monkeypatch):
    s=Session()
    def reject(*args,**kwargs):
        raise GenerationProtocolError("private-Task-sensitive-details")
    monkeypatch.setattr(broker,"reconcile_source_receipt",reject)
    with pytest.raises(broker.CandidateBrokerCommandError) as err:
        broker.dispatch_candidate_command(command("reconcile_complete"),
            trusted_channel_principal="hermes-local",session_factory=s)
    assert s.opened==1
    assert str(err.value)=="unqualified_source_claim"

def test_explicit_reconciled_response_is_short_and_no_credentials():
    a,b=socket.socketpair()
    try:
        send_holding_reply(a,admitted=True,candidate_generation=3,reconciled=True)
        raw=b.recv(512)
        assert len(raw)<=192
        assert raw.endswith(b"\n")
        payload=json.loads(raw)
        assert payload["reconciled"] is True
        assert payload["accepted_source_transaction"] is True
        assert payload["durable_delivery_verified"] is False
        assert payload["production_dispatch_authorized"] is False
        assert payload["decision"]=="HOLD"
        for s in ("claim","receipt_key","password","task_id","session"):
            assert s not in raw.decode()
    finally:
        a.close();b.close()

def test_rejected_command_cannot_be_marked_reconciled():
    a,b=socket.socketpair()
    try:
        from assistx.hermes_broker_unix_boundary_research import CandidateLocalTransportError
        with pytest.raises(CandidateLocalTransportError,match="invalid_receipt"):
            send_holding_reply(a,admitted=False,reconciled=True)
    finally:
        a.close();b.close()
