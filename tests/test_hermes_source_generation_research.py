"""HOLD-only source ledger protocol units. No live fleet/Neo4j."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from assistx import hermes_source_generation_research as g

SRC=Path(g.__file__)


def good_constraint():
    return {
        "type":"UNIQUENESS",
        "labelsOrTypes":["HermesSourceGeneration"],
        "properties":["owner"],
    }


class Session:
    def __init__(self, *, constraint=True, current=3, records=None):
        self.sql=[]
        self.schema=good_constraint() if constraint else None
        self.current=current
        self.records=records
        self.calls=0

    def run(self, q, args):
        self.sql.append((q,args))
        result=MagicMock()
        result.single.return_value=self.schema
        return result

    def execute_write(self, fn):
        self.calls+=1
        return fn(Tx(self))

class Tx:
    def __init__(self, session):
        self.parent=session
        self.sql=[]

    def run(self,q,args):
        self.sql.append((q,args))
        result=MagicMock()
        if "RETURN g.generation AS generation, g.owner AS owner" in q:
            result.single.return_value={"generation":self.parent.current,"owner":g.OWNER}
        elif "RETURN g.generation AS generation" in q:
            result.single.return_value={"generation":args["next"]}
        else:
            result.single.return_value={"task_id":"synthetic"}
        return result


def test_missing_constraint_denies_all_activity():
    s=Session(constraint=False)
    with pytest.raises(g.GenerationProtocolError,match="constraint"):
        g.advance_candidate_generation(s,expected_generation=3,mutate=lambda tx:True)
    assert s.calls==0
    outcome=g.pinned_candidate_snapshot(s)
    assert outcome["decision"]=="HOLD"
    assert not outcome["candidate_source_lock_acquired"]
    assert outcome["candidate_count"] is None


@pytest.mark.parametrize("generation",[-1,True,None,"3"])
def test_invalid_generation_no_write(generation):
    s=Session()
    with pytest.raises(g.GenerationProtocolError):
        g.advance_candidate_generation(s,expected_generation=generation,mutate=lambda tx:True)
    assert s.calls==0


def test_atomic_writer_rejects_stale_generation_before_callback():
    s=Session(current=4)
    effects=[]
    with pytest.raises(g.GenerationProtocolError,match="stale"):
        g.advance_candidate_generation(
            s,expected_generation=3,mutate=lambda tx:effects.append(True))
    assert effects==[]
    assert s.calls==1


def test_true_ack_increments_once_but_cannot_authorize_release():
    s=Session(current=3)
    called=[]
    out=g.advance_candidate_generation(
        s,expected_generation=3,
        mutate=lambda tx:called.append(True) or True,
    )
    assert called==[True]
    assert out["generation"]==4
    assert out["candidate_ledger_increment_committed"]
    assert out["mutation_callback_acknowledged"]
    # The fake callback returns True without writing a Task: no proof exists.
    assert out["source_task_write_independently_verified"] is False
    assert "participating_write_committed" not in out
    assert not out["writer_registry_complete"]
    assert not out["production_dispatch_authorized"]
    assert out["decision"]=="HOLD"


@pytest.mark.parametrize("ack",[False,None,1,"true",{}])
def test_unsound_mutation_ack_fails_closed(ack):
    s=Session(current=3)
    with pytest.raises(g.GenerationProtocolError,match="not_acknowledged"):
        g.advance_candidate_generation(
            s,expected_generation=3,
            mutate=lambda tx:ack,
        )


def test_snapshot_remains_non_authoritative_even_after_lock(monkeypatch):
    s=Session()
    monkeypatch.setattr(g,"_pass",lambda tx,bound:([{"task_id":"synthetic"}],"a"*64))
    out=g.pinned_candidate_snapshot(s)
    assert out["candidate_source_lock_acquired"]
    assert out["snapshot_generation"]==3
    assert out["candidate_count"]==1
    assert out["candidate_digest_sha256"]=="a"*64
    for key in ("authoritative_complete_census",
                "all_writers_generation_fenced",
                "external_high_water_verified",
                "independent_custody_verified",
                "resource_effects_verified",
                "production_dispatch_authorized"):
        assert out[key] is False


def test_snapshot_rejects_nonqualified_claim_rows(monkeypatch):
    s=Session()
    def bad(*args):
        raise ValueError("secret-synthetic-task-id")
    monkeypatch.setattr(g,"_pass",bad)
    out=g.pinned_candidate_snapshot(s)
    assert out["candidate_count"] is None
    assert not out["candidate_source_lock_acquired"]
    assert "secret" not in str(out)
    assert out["decision"]=="HOLD"


@pytest.mark.parametrize("size",[-1,0,10001,True,2.0])
def test_snapshot_rejects_invalid_bounds(size):
    s=Session()
    out=g.pinned_candidate_snapshot(s,max_rows=size)
    assert s.calls==0
    assert out["decision"]=="HOLD"
    assert out["reason"]=="invalid_census_bound"


def test_shared_ledger_lock_order_before_snapshot_and_mutation():
    tree=ast.parse(SRC.read_text())
    fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=="_lock")
    queries=[n.args[0].value for n in ast.walk(fn)
             if isinstance(n,ast.Call) and getattr(n.func,"attr","")=="run"
             and n.args and isinstance(n.args[0],ast.Constant)
             and isinstance(n.args[0].value,str)]
    assert len(queries)==1
    sql=queries[0]
    assert "MATCH (g:HermesSourceGeneration {owner:$owner})" in sql
    assert "SET g.lock_epoch=coalesce(g.lock_epoch,0)+1" in sql
    assert sql.index("SET g.lock_epoch")<sql.index("RETURN g.generation")
    s=Session()
    g.advance_candidate_generation(s,expected_generation=3,mutate=lambda tx:True)
    assert s.calls==1


def test_missing_ledger_singleton_denied_without_task_mutation():
    s=Session()
    orig=Tx.run
    def simulate_missing(self,q,args):
        response=orig(self,q,args)
        if "RETURN g.generation AS generation, g.owner AS owner" in q:
            response.single.return_value=None
        return response
    with pytest.MonkeyPatch.context() as m:
        m.setattr(Tx,"run",simulate_missing)
        with pytest.raises(g.GenerationProtocolError,match="singleton_absent"):
            g.advance_candidate_generation(s,expected_generation=3,mutate=lambda tx:True)
