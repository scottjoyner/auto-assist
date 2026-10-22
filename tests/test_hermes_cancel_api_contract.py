"""Source-isolated cancellation API tests, with no app/network/DB startup."""
from __future__ import annotations
import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1]/"src/assistx/api.py"
TREE = ast.parse(SOURCE.read_text())

class Conflict(Exception):
    def __init__(self, status_code, detail):
        self.status_code=status_code
        self.detail=detail

def isolated(name, **extras):
    node=next(x for x in TREE.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))
              and x.name==name)
    node=ast.fix_missing_locations(ast.FunctionDef(
        name=node.name,args=node.args,body=node.body,decorator_list=[],
        returns=node.returns,type_comment=None))
    ns={"Depends":lambda *_:None,"auth":None,"HTTPException":Conflict,
        "Neo4jClient":object,**extras}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),
                 str(SOURCE),"exec"),ns)
    return ns[name]

class FakeNeo:
    def __init__(self, accepted):
        self.accepted=accepted
        self.calls=[]
        self.closes=0
    def update_task_status(self,task_id,status):
        self.calls.append((task_id,status))
        return self.accepted
    def close(self):
        self.closes+=1

class FakeSession:
    def __init__(self,row):
        self.row=row
        self.query=None
        self.params=None
    def __enter__(self):return self
    def __exit__(self,*ignored):return None
    def run(self,sql,params):
        self.query=sql
        self.params=params
        return self
    def single(self):return self.row

class CancelEndpointSafetyTests(unittest.TestCase):
    def test_success_requires_source_db_ack(self):
        db=FakeNeo(True)
        fn=isolated("api_cancel_task",_neo=lambda:db)
        result=fn("synthetic-task",user="synthetic-auth")
        self.assertEqual(result["status"],"CANCELLED")
        self.assertEqual(db.calls,[("synthetic-task","CANCELLED")])
        self.assertEqual(db.closes,1)

    def test_rejected_cancellation_is_not_reported_success(self):
        db=FakeNeo(False)
        fn=isolated("api_cancel_task",_neo=lambda:db)
        with self.assertRaises(Conflict) as ctx:
            fn("synthetic-terminal",user="synthetic-auth")
        self.assertEqual(ctx.exception.status_code,409)
        self.assertEqual(db.calls,[("synthetic-terminal","CANCELLED")])
        self.assertEqual(db.closes,1)

    def test_missing_cancellation_result_denied(self):
        db=FakeNeo(None)
        fn=isolated("api_cancel_task",_neo=lambda:db)
        with self.assertRaises(Conflict):
            fn("synthetic-missing",user="synthetic-auth")
        self.assertEqual(db.closes,1)

    def test_intent_cancellation_acquires_lock_before_recheck(self):
        sess=FakeSession({"cancelled":1})
        db=type("FakeDb",(),{"_session":lambda _:sess})()
        fn=isolated("_cancel_tasks_for_intent")
        self.assertEqual(fn(db,"synthetic-intent","test-only"),1)
        sql=sess.query
        self.assertIn("FOREACH",sql)
        self.assertLess(sql.index("hermes_fence_check_epoch"),
                        sql.index("WITH t\n            WHERE t.status"))
        self.assertEqual(sess.params["intent_id"],"synthetic-intent")

    def test_intent_empty_result_does_not_claim_cancellation(self):
        sess=FakeSession({"cancelled":0})
        db=type("FakeDb",(),{"_session":lambda _:sess})()
        self.assertEqual(isolated("_cancel_tasks_for_intent")(
            db,"synthetic-intent","test-only"),0)

if __name__=="__main__":
    unittest.main()
