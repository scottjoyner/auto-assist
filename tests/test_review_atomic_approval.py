"""Source-only tests: no AssistX package imports, services or database."""
from pathlib import Path
import runpy

import pytest

module = runpy.run_path(str(
    Path(__file__).resolve().parents[1] / "src/assistx/review_atomic_approval.py"
))
Denied=module["ReviewAtomicApprovalDenied"]
approve=module["propose_held_review_approval"]
tx_approve=module["_tx_approve"]
source_title=module["_safe_source_title"]


class Rec:
    def __init__(self,data):
        self.data=data
    def single(self):
        return self.data


class Tx:
    def __init__(self,locks=1,eligible=True,created=True,payload='{"source_text":"research Task"}'):
        self.locks=locks
        self.eligible=eligible
        self.created=created
        self.payload=payload
        self.calls=[]
    def run(self,query,params=None):
        self.calls.append((query,params))
        n=len(self.calls)
        if n==1:
            return Rec({"matching_rows":self.locks})
        if n==2:
            return Rec({"review_element_id":"synthetic-element","title":"Fallback",
                        "payload_json":self.payload,"priority":"HIGH"} if self.eligible else None)
        return Rec({"candidate_status":"PENDING_ADMISSION",
                    "outbox_status":"HELD_UNTIL_ADMISSION"} if self.created else None)


class Session:
    def __init__(self,tx):self.tx=tx
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def execute_write(self,fn,*args):return fn(self.tx,*args)


class Neo:
    def __init__(self,tx):
        self.tx=tx
        self.opens=0
    def _session(self):
        self.opens+=1
        return Session(self.tx)


def run(neo,**kwargs):
    a=dict(review_id="review-synthetic-1",actor="reviewer-local",
           expected_revision=1700000000123,note="research only")
    a.update(kwargs)
    return approve(neo,**a)


def test_separate_post_lock_read_then_atomic_create_inert():
    tx=Tx()
    result=run(Neo(tx))
    assert result["candidate_status"]=="PENDING_ADMISSION"
    assert result["outbox_status"]=="HELD_UNTIL_ADMISSION"
    assert len(tx.calls)==3
    assert "WorkSupplyState {name:'canonical'}" in tx.calls[0][0]
    assert "review_outbox_serial" in tx.calls[0][0]
    query=tx.calls[1][0]
    for term in ("collect(r0) AS matches","size(matches)=1",
                 "r.kind='intent_review'","r.status='REVIEW'",
                 "r.updated_at_ts=$expected_revision",
                 "r.review_decision IS NULL","r.machine_token_epoch IS NULL",
                 "r.machine_authorization_approval_id IS NULL",
                 "r.agent_session_id IS NULL","r.target_agent_id IS NULL",
                 "MATCH (r)-[:APPROVED_AS]->(:Task)",
                 "MATCH (:Task {approved_from_review_task:$review_id})",
                 "MATCH (:ReviewApprovalOutbox {id:$outbox_id})"):
        assert term in query
    creation=tx.calls[2][0]
    for term in ("elementId(r)=$review_element_id",
                 "status:'PENDING_ADMISSION'",
                 "status:'HELD_UNTIL_ADMISSION'",
                 "verified_operator:false","signed_admission:false",
                 "CREATE (r)-[:APPROVED_AS]->(t)",
                 "CREATE (r)-[:HAS_APPROVAL_OUTBOX]->(o)",
                 "r.review_decision='approved_held'"):
        assert term in creation
    assert "READY" not in creation
    assert tx.calls[2][1]["title"]=="research Task"


@pytest.mark.parametrize("locks",[0,2,3])
def test_missing_duplicate_shared_lock_denied_before_read(locks):
    tx=Tx(locks=locks)
    with pytest.raises(Denied,match="lock_not_unique"):
        run(Neo(tx))
    assert len(tx.calls)==1


def test_stale_protected_or_duplicate_review_denied_before_create():
    tx=Tx(eligible=False)
    with pytest.raises(Denied,match="stale_protected_or_duplicate"):
        run(Neo(tx))
    assert len(tx.calls)==2


def test_failed_create_raises_for_atomic_transaction_rollback():
    tx=Tx(created=False)
    with pytest.raises(Denied,match="review_changed"):
        run(Neo(tx))
    assert len(tx.calls)==3


@pytest.mark.parametrize("params",[
    {"review_id":None},{"review_id":""},{"review_id":"z"*257},
    {"actor":None},{"actor":""},{"actor":"z"*129},
    {"expected_revision":None},{"expected_revision":0},
    {"expected_revision":True},{"expected_revision":1.5},
    {"note":None},{"note":"a"*1001},
    {"auto_dispatch":True},{"auto_dispatch":None},
])
def test_bad_or_dispatching_input_no_db(params):
    neo=Neo(Tx())
    with pytest.raises(Denied,match="invalid_or_dispatching"):
        run(neo,**params)
    assert neo.opens==0


@pytest.mark.parametrize("bad_payload",[
    "[1,2]","not json","null","42",'{"source_text": []}',
    '{"source_text":{}}',"x"*65537,
])
def test_malformed_payload_rollback_prevents_create(bad_payload):
    tx=Tx(payload=bad_payload)
    with pytest.raises(Denied):
        run(Neo(tx))
    assert len(tx.calls)==2


def test_deterministic_candidate_outbox_for_same_review():
    x=Tx()
    y=Tx()
    run(Neo(x))
    run(Neo(y))
    assert x.calls[1][1]["approved_id"]==y.calls[1][1]["approved_id"]
    assert x.calls[1][1]["outbox_id"]==y.calls[1][1]["outbox_id"]
    assert x.calls[1][1]["approved_id"]!=x.calls[1][1]["outbox_id"]


def test_never_routed_no_external_effects():
    root=Path(__file__).resolve().parents[1]
    for name in ("src/assistx/api.py","src/assistx/routers/review.py",
                 "src/assistx/cli.py","src/assistx/jobs.py",
                 "src/assistx/pipeline_execute.py"):
        assert "propose_held_review_approval" not in (root/name).read_text()
    code=(root/"src/assistx/review_atomic_approval.py").read_text()
    for unsafe in ("notify_task_created(", "create_dispatch_with_paperclip(",
                   "run_task(", "get_paperclip_client("):
        assert unsafe not in code
