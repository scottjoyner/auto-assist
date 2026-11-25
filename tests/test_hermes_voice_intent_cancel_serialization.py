"""Voice/intent cancellation is available but must serialize with Task writers.

These query-contract and mock checks do not prove resource-side effect
revocation or qualify production Hermes cutover.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT=Path(__file__).resolve().parents[1]/"src/assistx"


def _fn(source, name):
    tree=ast.parse((ROOT/source).read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                and n.name==name)


def _cancel_query(source,name):
    fn=_fn(source,name)
    runs=[n for n in ast.walk(fn)
          if isinstance(n,ast.Call) and getattr(n.func,"attr","")=="run"
          and n.args and isinstance(n.args[0],ast.Constant)
          and isinstance(n.args[0].value,str)]
    assert len(runs)==1
    return runs[0].args[0].value


@pytest.mark.parametrize("file,fn",[
    ("voice_routes.py","_cancel_target_task"),
    ("intent_orchestrator.py","_handle_cancel"),
])
def test_cancel_uses_lock_then_current_status_before_mutation(file,fn):
    sql=_cancel_query(file,fn)
    assert sql.count("SET t.hermes_fence_check_epoch =")==1
    assert sql.count("WITH t")==1
    assert "WHERE t.status IN ['READY','CLAIMED','RUNNING']" in sql
    assert sql.index("SET t.hermes_fence_check_epoch =") < sql.index("WITH t")
    assert sql.index("WITH t") < sql.index("WHERE t.status IN")
    assert sql.index("WHERE t.status IN") < sql.index("SET t.status='CANCELLED'")
    # Cancellation intentionally stays available for Hermes; no blanket
    # Hermes exclusion can block a safety cancellation.
    assert "STARTS WITH 'hermes-'" not in sql


class Session:
    def __init__(self, ack):
        self.ack=ack
        self.sql=None
        self.params=None
    def __enter__(self):return self
    def __exit__(self,*args):return None
    def run(self,sql,params):
        self.sql=sql
        self.params=params
        return self
    def single(self):
        return self.ack


class Neo:
    def __init__(self,ack):
        self.session=Session(ack)
    def _session(self):
        return self.session


def test_voice_cancel_uses_database_receipt():
    source=ROOT/"voice_routes.py"
    fn=_fn("voice_routes.py","_cancel_target_task")
    body=ast.FunctionDef(name=fn.name,args=fn.args,body=fn.body,
                         decorator_list=[],returns=fn.returns,type_comment=None)
    ns={"Any":object}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[body],type_ignores=[])),
                 str(source),"exec"),ns)
    cancel=ns["_cancel_target_task"]
    event={"event_id":"synthetic-voice","metadata":{"task_id":"synthetic-task"}}
    denied=Neo({"cancelled":0})
    assert cancel(denied,event)==0
    assert denied.session.params["task_id"]=="synthetic-task"
    accepted=Neo({"cancelled":1})
    assert cancel(accepted,event)==1
    assert accepted.session.sql==_cancel_query("voice_routes.py","_cancel_target_task")


def test_intent_cancel_query_keeps_task_receipt():
    sql=_cancel_query("intent_orchestrator.py","_handle_cancel")
    assert "RETURN t.id AS task_id, t.status AS old_status" in sql
    assert "MATCH (i:Intent {id:$id})-[:CREATED_TASK]->(t:Task)" in sql
