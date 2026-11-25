"""Test source-isolated Task pause/resume API denial and verified receipts.

No API process, production Neo4j or fleet workload is invoked here.
"""
from __future__ import annotations

import ast
from pathlib import Path
import pytest

FILE = Path(__file__).resolve().parents[1] / "src/assistx/api.py"
TREE = ast.parse(FILE.read_text(encoding="utf-8"))

class Conflict(Exception):
    def __init__(self,status_code,detail):
        self.status_code=status_code
        self.detail=detail

class FakeSession:
    def __init__(self,row):
        self.row=row
        self.sql=None
        self.params=None
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return None
    def run(self,sql,params):
        self.sql=sql
        self.params=params
        return self
    def single(self):
        return self.row

class FakeNeo:
    def __init__(self,row):
        self.session=FakeSession(row)
        self.closed=0
    def _session(self):
        return self.session
    def close(self):
        self.closed+=1

def endpoint(name,neo):
    original=next(x for x in TREE.body if isinstance(x,ast.FunctionDef)
                  and x.name==name)
    fn=ast.FunctionDef(name=original.name,args=original.args,body=original.body,
                      decorator_list=[],returns=original.returns,type_comment=None)
    mod=ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[]))
    ns={"Depends":lambda *args:None,"auth":None,
        "HTTPException":Conflict,"_neo":lambda:neo}
    exec(compile(mod,str(FILE),"exec"),ns)
    return ns[name]

@pytest.mark.parametrize("name,paused",[
    ("api_pause_task",True),("api_resume_task",False),
])
@pytest.mark.parametrize("db_ack",[True,False])
def test_task_pause_resume_is_rejected_without_db_ack(name,paused,db_ack):
    neo=FakeNeo({"updated_id":"synthetic-task"} if db_ack else None)
    fn=endpoint(name,neo)
    if not db_ack:
        with pytest.raises(Conflict) as exc:
            fn("synthetic-task",user="synthetic-user")
        assert exc.value.status_code==409
    else:
        result=fn("synthetic-task",user="synthetic-user")
        assert result=={"task_id":"synthetic-task","paused":paused}
    assert neo.closed==1
    assert neo.session.params=={"id":"synthetic-task"}
    sql=neo.session.sql
    assert "SET t.hermes_fence_check_epoch =" in sql
    assert "WITH t" in sql
    assert "WHERE NOT (" in sql
    assert sql.index("SET t.hermes_fence_check_epoch") < sql.index("WHERE NOT (")
    assert sql.index("WHERE NOT (") < sql.index("SET t.paused=")
    for field in ("claimed_by","target_agent_id","completed_by",
                  "heartbeat_by","failed_by","paused_from_agent_id",
                  "migration_target_agent_id"):
        assert f"coalesce(t.{field}, '') STARTS WITH 'hermes-'" in sql

@pytest.mark.parametrize("name",["api_pause_task","api_resume_task"])
def test_each_hermes_identity_guard_is_mandatory(name):
    neo=FakeNeo(None)
    fn=endpoint(name,neo)
    with pytest.raises(Conflict):
        fn("synthetic-task",user="synthetic-user")
    sql=neo.session.sql
    expected=[f"coalesce(t.{field}, '') STARTS WITH 'hermes-'"
              for field in ("claimed_by","target_agent_id","completed_by",
                            "heartbeat_by","failed_by","paused_from_agent_id",
                            "migration_target_agent_id")]
    assert all(field in sql for field in expected)
    for field in expected:
        tampered=sql.replace(field,"false",1)
        assert not all(required in tampered for required in expected)
