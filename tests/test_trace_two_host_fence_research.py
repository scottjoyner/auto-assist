"""Research-only 2-of-2 x1/xwing fencing and fail-closed handoff tests."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import shutil
import tempfile
import uuid

import pytest
from assistx.trace_two_host_fence_research import (
    IndependentResearchWitness,bootstrap_witness
)
from assistx.trace_two_host_authority_research import (
    SingleAuthorityResearch,bootstrap_research
)
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import probe_trace_two_host_fence_handshake as handshake

GRAPH="a"*64
TERM=7


def fixture():
    owner_dir=tempfile.TemporaryDirectory(prefix="assistx-twohost-authority-test-",dir="/tmp")
    witness_dir=tempfile.TemporaryDirectory(prefix="assistx-twohost-fence-test-",dir="/tmp")
    op=str(Path(owner_dir.name)/"authority-test.sqlite")
    wp=str(Path(witness_dir.name)/"witness-test.sqlite")
    epoch=str(uuid.uuid4())
    bootstrap_research(op,epoch=epoch,graph_id=GRAPH)
    bootstrap_witness(wp,epoch=epoch,graph_id=GRAPH,term=TERM)
    return owner_dir,witness_dir,op,wp,epoch


def witness(path,epoch,term=TERM,checkpoint=0):
    return IndependentResearchWitness(path,expected_epoch=epoch,expected_graph=GRAPH,
        expected_term=term,independent_min_sequence=checkpoint)


def grant(path,epoch,ref):
    return SingleAuthorityResearch(path,pinned_epoch=epoch,pinned_graph_id=GRAPH,
        minimum_sequence=0).admit(ref)


def attest_from_grant(w,decision,epoch,ref,term=TERM):
    return w.attest(epoch=epoch,graph_id=GRAPH,term=term,
        sequence=decision.sequence,token=decision.token,
        receiver_nonce=decision.receiver_nonce,query_ref=ref)


def test_one_owner_grant_requires_distinct_durable_witness():
    a,b,p,q,e=fixture()
    try:
        d=grant(p,e,"synthetic-first")
        assert d.status=="admitted"
        w=witness(q,e)
        r=attest_from_grant(w,d,e,"synthetic-first")
        assert r.status=="witnessed-no-physical-dispatch"
        assert w.observe()["occupied"]==1
        assert SingleAuthorityResearch(p,pinned_epoch=e,pinned_graph_id=GRAPH,
            minimum_sequence=1).admit("synthetic-successor").status=="full"
    finally:a.cleanup();b.cleanup()


@pytest.mark.parametrize("n",[1,3,5,10])
def test_witness_denies_all_successor_waves_while_first_slot_occupied(n):
    a,b,p,q,e=fixture()
    try:
        first=grant(p,e,"synthetic-first")
        w=witness(q,e)
        assert attest_from_grant(w,first,e,"synthetic-first").status=="witnessed-no-physical-dispatch"
        # Synthetically craft subsequent *well-formed* candidate requests.
        # No authority can reissue capacity while the witness holds it.
        def attempt(i):
            return w.attest(epoch=e,graph_id=GRAPH,term=TERM,
                sequence=2,token=uuid.uuid4().hex,receiver_nonce=str(uuid.uuid4()),
                query_ref=f"synthetic-after-{i}").status
        with ThreadPoolExecutor(max_workers=n) as pool:
            decisions=list(pool.map(attempt,range(n)))
        assert decisions==["physical-capacity-held"]*n
        assert w.observe()["occupied"]==1
    finally:a.cleanup();b.cleanup()


def test_witness_rejects_outdated_term_epoch_or_graph():
    a,b,p,q,e=fixture()
    try:
        d=grant(p,e,"synthetic-first")
        w=witness(q,e)
        for epoch,graph,term in [
            (e,GRAPH,TERM-1),(str(uuid.uuid4()),GRAPH,TERM),(e,"b"*64,TERM)
        ]:
            x=w.attest(epoch=epoch,graph_id=graph,term=term,sequence=1,
                token=d.token,receiver_nonce=d.receiver_nonce,query_ref="synthetic-first")
            assert x.status=="identity-or-term-denied"
        assert w.observe()["occupied"]==0
    finally:a.cleanup();b.cleanup()


def test_missing_witness_fails_without_recreating_state():
    a,b,p,q,e=fixture()
    try:
        Path(q).unlink()
        with pytest.raises((ValueError,OSError)):
            witness(q,e)
        assert not Path(q).exists()
    finally:a.cleanup();b.cleanup()


def test_authority_rollback_cannot_bypass_surviving_witness():
    a,b,p,q,e=fixture()
    try:
        earlier=Path(a.name)/"before-admission"
        shutil.copyfile(p,earlier)
        d=grant(p,e,"synthetic-first")
        w=witness(q,e)
        assert attest_from_grant(w,d,e,"synthetic-first").status=="witnessed-no-physical-dispatch"
        # Roll back owner bytes in place; stale owner may issue sequence one
        # again but the separately held witness must still refuse it.
        shutil.copyfile(earlier,p)
        restarted=grant(p,e,"synthetic-after-rollback")
        assert restarted.status=="admitted"
        denied=attest_from_grant(w,restarted,e,"synthetic-after-rollback")
        assert denied.status=="physical-capacity-held"
        assert w.observe()["occupied"]==1
    finally:a.cleanup();b.cleanup()


def test_witness_copy_alone_is_not_global_quorum_expected_counterexample():
    a,b,p,q,e=fixture()
    another=tempfile.TemporaryDirectory(prefix="assistx-twohost-fence-test-",dir="/tmp")
    try:
        q2=str(Path(another.name)/"witness-test.sqlite")
        shutil.copyfile(q,q2)
        Path(q2).chmod(0o600)
        fake1=type("Candidate",(),{
            "sequence":1,"token":uuid.uuid4().hex,
            "receiver_nonce":str(uuid.uuid4())})()
        fake2=type("Candidate",(),{
            "sequence":1,"token":uuid.uuid4().hex,
            "receiver_nonce":str(uuid.uuid4())})()
        assert attest_from_grant(witness(q,e),fake1,e,"synthetic-first").status=="witnessed-no-physical-dispatch"
        assert attest_from_grant(witness(q2,e),fake2,e,"synthetic-second").status=="witnessed-no-physical-dispatch"
        # A copied witness is as dangerous as a copied owner. Not consensus!
    finally:a.cleanup();b.cleanup();another.cleanup()


def test_lost_remote_confirmation_leaves_reserved_slot_no_dispatch(monkeypatch):
    a,b,p,q,e=fixture()
    try:
        monkeypatch.setenv("ASSISTX_TWOHOST_RESEARCH_ONLY","yes-disposable")
        monkeypatch.setattr(handshake.socket,"gethostname",lambda:"x1-370")
        monkeypatch.setattr(handshake.subprocess,"run",
          lambda *args,**kwargs:type("Proc",(),{"returncode":255,"stdout":""})())
        result=handshake.prepare(owner_path=p,witness_path=q,
                                 epoch=e,graph=GRAPH,term=TERM,
                                 query_ref="synthetic-first")
        assert result["status"]=="held-witness-unavailable"
        assert result["physical_dispatch_permitted"] is False
        assert SingleAuthorityResearch(p,pinned_epoch=e,pinned_graph_id=GRAPH,
            minimum_sequence=1).snapshot()["active"]==1
        assert witness(q,e).observe()["occupied"]==0
    finally:a.cleanup();b.cleanup()


def test_spoofed_witness_response_is_not_usable_confirmation(monkeypatch):
    a,b,p,q,e=fixture()
    try:
        monkeypatch.setenv("ASSISTX_TWOHOST_RESEARCH_ONLY","yes-disposable")
        monkeypatch.setattr(handshake.socket,"gethostname",lambda:"x1-370")
        monkeypatch.setattr(handshake.subprocess,"run",
          lambda *args,**kwargs:type("Proc",(),{
              "returncode":0,"stdout":json.dumps({
                 "status":"witnessed-no-physical-dispatch","host":"xwing",
                 "sequence":9,"term":7,"token":"invented",
                 "receiver_nonce":"invented","query_ref":"synthetic-first"})})())
        result=handshake.prepare(owner_path=p,witness_path=q,
                                 epoch=e,graph=GRAPH,term=TERM,
                                 query_ref="synthetic-first")
        assert result["status"]=="held-witness-not-accepted"
        assert result["physical_dispatch_permitted"] is False
    finally:a.cleanup();b.cleanup()
