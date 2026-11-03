"""Source-only research audit contracts. No AssistX app initialization."""
from __future__ import annotations

from pathlib import Path
import runpy

import pytest
from neo4j import READ_ACCESS


module=runpy.run_path(str(
    Path(__file__).resolve().parents[1]/"src/assistx/review_orphan_audit.py"
))
audit=module["audit_review_provenance"]
pointer=module["_pointer"]
AuditIncomplete=module["AuditIncomplete"]


class Session:
    def __init__(self, batches):
        self.batches=list(batches)
        self.calls=[]
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def run(self,query,params):
        self.calls.append((str(query),params))
        if not self.batches:
            raise AssertionError("audit made unexpected graph query")
        return self.batches.pop(0)


class Driver:
    def __init__(self,batches):
        self.session_obj=Session(batches)
        self.kwargs=None
    def session(self,**kwargs):
        self.kwargs=kwargs
        return self.session_obj


@pytest.mark.parametrize("limit",[None,0,-1,301,True,1.5,"10"])
def test_invalid_cap_never_connects(limit):
    driver=Driver([])
    with pytest.raises(ValueError,match="invalid_bounded_audit_limit"):
        audit(driver,limit=limit)
    assert driver.kwargs is None


def test_empty_graph_rejected_as_security_pass():
    d=Driver([[],[],[]])
    result=audit(d,limit=20)
    assert result["decision"]=="HOLD"
    assert result["counts"]=={"reviews":0,"children":0,"outboxes":0}
    assert result["truncated"]=={"reviews":False,"children":False,"outboxes":False}
    assert d.kwargs["default_access_mode"]==READ_ACCESS
    assert [p for _,p in d.session_obj.calls]==[
        {"max":21},{"max":21},{"max":21},
    ]
    for query,_ in d.session_obj.calls:
        for forbidden in ("CREATE (","DELETE ","SET ","MERGE (","REMOVE "):
            assert forbidden not in query


def test_approved_review_with_no_edge_flagged():
    d=Driver([
        [{"id":"review","status":"DONE","decision":"approved"}],
        [],[],
        [{"id":"review","review_rows":1,"child_links":0}],
    ])
    result=audit(d)
    assert result["decision"]=="HOLD"
    assert result["approved_review_missing_unique_child_link"]==1


def test_malformed_and_ambiguous_embedded_pointers():
    examples=[
        ({"payload":"not-json","direct_source":None},"malformed_payload"),
        ({"payload":"[1]","direct_source":None},"malformed_payload"),
        ({"payload":'{"approved_from_review_task": 23}',"direct_source":None},
         "invalid_embedded_pointer"),
        ({"payload":'{"approved_from_review_task":"a"}',"direct_source":"b"},
         "conflicting_source_pointers"),
        ({"payload":None,"direct_source":0},"invalid_direct_pointer"),
        ({"payload":"x"*65537,"direct_source":None},"malformed_payload"),
    ]
    for item,want in examples:
        assert pointer(item)==(None,want)


def test_embedded_and_direct_pointer_agreement():
    item={"payload":'{"approved_from_review_task":"r"}',"direct_source":"r"}
    assert pointer(item)==("r",None)
    assert pointer({"payload":None,"direct_source":"r"})==("r",None)
    assert pointer({"payload":"{}","direct_source":None})==(None,None)


def test_child_result_never_contains_raw_identifiers():
    d=Driver([
        [],
        [{"id":"sensitive-child","status":"READY",
          "payload":'{"approved_from_review_task":"sensitive-review"}',
          "direct_source":None}],
        [],
        [{"id":"sensitive-child","review_rows":0,
          "linked_children":0,"decisions":[],"review_statuses":[]}],
    ])
    result=audit(d)
    assert result["child_link_findings"]=={
        "missing_or_ambiguous_source_review":1,
    }
    assert "sensitive-child" not in str(result)
    assert "sensitive-review" not in str(result)


def test_duplicate_ids_fail_closed():
    d=Driver([
        [{"id":"r","status":"REVIEW","decision":None},
         {"id":"r","status":"REVIEW","decision":None}],
        [],[],
    ])
    with pytest.raises(AuditIncomplete,match="duplicate_id"):
        audit(d)


def test_sample_cap_never_reports_exhaustive_when_overflow():
    d=Driver([
        [{"id":"r1","status":"REVIEW","decision":None},
         {"id":"r2","status":"REVIEW","decision":None}],
        [],[],
        [{"id":"r1","review_rows":1,"child_links":0}],
    ])
    result=audit(d,limit=1)
    assert result["truncated"]["reviews"] is True
    assert result["decision"]=="HOLD"


def test_no_wired_route_no_mutator_or_automatic_reconciler():
    root=Path(__file__).resolve().parents[1]
    for path in ("src/assistx/api.py","src/assistx/routers/review.py",
                 "src/assistx/jobs.py","src/assistx/cli.py",
                 "src/assistx/pipeline_execute.py"):
        assert "audit_review_provenance" not in (root/path).read_text()
    code=(root/"src/assistx/review_orphan_audit.py").read_text()
    assert "default_access_mode=READ_ACCESS" in code
    assert "execute_write(" not in code
    assert "run_task(" not in code
    assert "create_task_with_context(" not in code
