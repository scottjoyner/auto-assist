"""Isolated contract tests; no real Neo4j writes, identities, tokens or serving."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx.owner_identity import (
    TaskOwnerError, authenticated_owner, canonical_identity,
    owner_required, scoped_key, task_owner
)
from assistx.neo4j_client import Neo4jClient
from assistx.executor_security import build_executor_security_router
from assistx.executor_claims import read_claim_state, assert_claim_matches
from assistx.executor_security import ExecutorTokenError

PERSON_A={"owner_issuer":"test-idp","owner_subject":"person-a"}
PERSON_B={"owner_issuer":"test-idp","owner_subject":"person-b"}


def test_strict_mode_off_by_default(monkeypatch):
    monkeypatch.delenv("ASSISTX_REQUIRE_OWNER_BINDING",raising=False)
    assert owner_required() is False
    assert task_owner({}) == {}


def test_typo_does_not_disable_strict_mode(monkeypatch):
    monkeypatch.setenv("ASSISTX_REQUIRE_OWNER_BINDING","tru")
    assert owner_required()


def test_trusted_scope_is_sole_accepted_user_authority():
    request={"state":{"verified_owner":{**PERSON_A,"authenticated":True}}}
    assert authenticated_owner(request,"person-a") == PERSON_A
    with pytest.raises(TaskOwnerError,match="match"):
        authenticated_owner(request,"person-b")


@pytest.mark.parametrize("scope",[
    {"headers":[(b"x-owner-id",b"person-a")]},
    {"state":{}},
    {"state":{"verified_owner":{**PERSON_A,"authenticated":"true"}}},
    {"state":{"verified_owner":{**PERSON_A,"authenticated":False}}},
])
def test_forged_unverified_scope_denied(scope):
    with pytest.raises(TaskOwnerError):
        authenticated_owner(scope,"person-a")


@pytest.mark.parametrize("bad",["", " person-a", "person b", 4, None, "x"*161])
def test_malformed_owner_denied(bad):
    with pytest.raises(TaskOwnerError):
        canonical_identity({"owner_issuer":"test-idp","owner_subject":bad})


def test_owner_scoped_hash_is_stable_but_distinct():
    assert scoped_key(PERSON_A,"retry-1")==scoped_key(PERSON_A,"retry-1")
    assert scoped_key(PERSON_A,"retry-1")!=scoped_key(PERSON_B,"retry-1")
    assert scoped_key(PERSON_A,"retry-1")!=scoped_key(PERSON_A,"retry-2")
    assert "person-a" not in scoped_key(PERSON_A,"retry-1")


class FakeRow(dict):
    pass


class FakeResult:
    def __init__(self,row):self.row=row
    def single(self):return self.row


class MemoryStore:
    def __init__(self,constraint_type="UNIQUENESS"):
        self.rows={}
        self.constraint_type=constraint_type
        self.writes=0
        self.notified=[]
        self.last_query=""
    def session(self):return FakeSession(self)


class FakeTx:
    def __init__(self,store):self.store=store
    def run(self,query,params):
        assert "MERGE (t:Task {owner_idempotency_key:$owner_key})" in query
        assert "ON CREATE SET" in query
        assert "ON MATCH SET" not in query
        self.store.last_query=query
        key=params["owner_key"]
        if key not in self.store.rows:
            self.store.writes+=1
            props=params["props"]
            self.store.rows[key]={
                "id":params["id"],
                "creation_nonce":params["nonce"],
                "owner_issuer":props["owner_issuer"],
                "owner_subject":props["owner_subject"],
            }
        return FakeResult(FakeRow(self.store.rows[key]))


class FakeSession:
    def __init__(self,store):self.store=store
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def run(self,query,params):
        assert "SHOW CONSTRAINTS" in query
        if self.store.constraint_type is None:return FakeResult(None)
        return FakeResult({"name":"task_owner_idempotency_unique",
            "type":self.store.constraint_type,
            "labelsOrTypes":["Task"],"properties":["owner_idempotency_key"]})
    def execute_write(self,callback):return callback(FakeTx(self.store))


def owned_create(store,owner=PERSON_A,key="retry-1"):
    neo=Neo4jClient.__new__(Neo4jClient)
    neo._session=store.session
    return neo.create_owned_task(
        title="test",task_type="swarm_task",status="READY",kind="test",
        required_capabilities=["cpu"],target_agent_id=None,priority=None,
        payload={"command":"echo safe"},idempotency_key=key,
        preemptible=False,max_migrations=2,owner=owner
    )


def test_owned_create_and_retry_cannot_rewrite_task(monkeypatch):
    import assistx.neo4j_client as module
    store=MemoryStore()
    called=[]
    monkeypatch.setattr(module,"notify_task_created",lambda **kw:called.append(kw))
    first=owned_create(store)
    later=owned_create(store)
    assert first["task_id"]==later["task_id"]
    assert first["created"] is True and later["created"] is False
    assert store.writes==1 and len(called)==1


def test_same_client_retry_id_separate_owners_not_alias(monkeypatch):
    import assistx.neo4j_client as module
    monkeypatch.setattr(module,"notify_task_created",lambda **kw:None)
    store=MemoryStore()
    one=owned_create(store,PERSON_A)
    two=owned_create(store,PERSON_B)
    assert one["task_id"]!=two["task_id"]
    assert store.writes==2


@pytest.mark.parametrize("schema",[None,"NODE_KEY","RANGE"])
def test_schema_missing_or_wrong_type_never_writes(monkeypatch,schema):
    import assistx.neo4j_client as module
    monkeypatch.setattr(module,"notify_task_created",lambda **kw:None)
    store=MemoryStore(schema)
    with pytest.raises(ValueError,match="uniqueness constraint"):
        owned_create(store)
    assert store.writes==0


def test_existing_different_authoritative_owner_fails_closed(monkeypatch):
    import assistx.neo4j_client as module
    monkeypatch.setattr(module,"notify_task_created",lambda **kw:None)
    store=MemoryStore()
    owned_create(store)
    key=scoped_key(PERSON_A,"retry-1")
    store.rows[key]["owner_subject"]="hijacked"
    with pytest.raises(ValueError,match="collision"):
        owned_create(store)
    assert store.writes==1


def active_row(owner=None):
    now=int(time.time()*1000)
    task={"id":"task-1","status":"RUNNING","claimed_by":"agent-a",
          "claim_id":"claim-1","agent_session_id":"synthetic-owner-session",
          "execution_attempt":1,"payload_json":"{}",
          "lease_expires_at_ts":now+120000}
    task.update(owner or {})
    return {"task":task,"projection_generation":8,
            "projection_status":"approved","projection_expires_at_ts":now+120000}


class NeoRow:
    def __init__(self,row):self.row=row
    def _session(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def run(self,query,params):return FakeResult(self.row)
    def close(self):pass


def get_claim_status(row):
    app=FastAPI()
    app.include_router(build_executor_security_router(lambda:NeoRow(row)))
    return TestClient(app).get("/api/executor/claims/task-1/status",
        headers={"Authorization":"Bearer status-token"})


def test_strict_claim_status_legacy_ownerless_denied(monkeypatch):
    monkeypatch.setenv("ASSISTX_REQUIRE_OWNER_BINDING","true")
    monkeypatch.setenv("ASSISTX_EXECUTOR_CLAIM_STATUS_TOKEN","status-token")
    result=get_claim_status(active_row())
    assert result.status_code==200
    assert result.json()["active"] is False
    assert result.json()["reason"]=="canonical_task_owner_absent_or_invalid"


def test_strict_claim_status_provides_canonical_owner(monkeypatch):
    monkeypatch.setenv("ASSISTX_REQUIRE_OWNER_BINDING","true")
    monkeypatch.setenv("ASSISTX_EXECUTOR_CLAIM_STATUS_TOKEN","status-token")
    result=get_claim_status(active_row(PERSON_A))
    assert result.status_code==200
    assert result.json()["active"] is True
    assert result.json()["owner_subject"]=="person-a"


def test_strict_claim_match_rejects_owner_switch(monkeypatch):
    monkeypatch.setenv("ASSISTX_REQUIRE_OWNER_BINDING","true")
    row={"active":True,"task_id":"task-1","claim_id":"claim-1",
         "agent_id":"agent-a","projection_generation":8,
         "lease_expires_at_ts":int(time.time()*1000)+120000,**PERSON_A}
    jwt={"task_id":"task-1","claim_id":"claim-1","agent_id":"agent-a",
         "projection_generation":8,"exp":int(time.time())+30,**PERSON_B}
    with pytest.raises(ExecutorTokenError,match="owner"):
        assert_claim_matches(jwt,row)
