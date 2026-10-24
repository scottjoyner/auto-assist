"""Strict offline page contract with synthetic keys and floor (NOT custody)."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import importlib.util
from pathlib import Path
import pytest

SRC=Path(__file__).resolve().parents[1]/"src/assistx/hermes_census_page_contract.py"
spec=importlib.util.spec_from_file_location("census_pages_research",SRC)
c=importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

KEY=b"synthetic-local-signature-test-key-not-production"
NOW=1_790_000_000_000


def sign(msg):
    return hmac.new(KEY,msg,hashlib.sha256).hexdigest()


def verify(msg,signature):
    return hmac.compare_digest(sign(msg),signature)


def rec(i):
    return {
        "task_id": f"synthetic-{i:05d}",
        "claimed_by": "hermes-local",
        "target_agent_id": "hermes-local",
        "claim_id": f"claim-{i:05d}",
        "session_id": "synthetic-session",
        "attempt": 1,
        "lease_expiry": NOW+120000,
        "status": "CLAIMED",
        "revocation_state": "unverified",
        "resource_effect_ref": "unavailable",
    }


def reseal(pages):
    prior_sha=None
    for page in pages:
        page["previous_page_sha256"]=prior_sha
        page["signature"]=sign(c.DOMAIN+c.canonical(c.payload(page)))
        prior_sha=c.digest(c.payload(page))
    return pages


def pages(total=621,gen=2):
    records=[rec(i) for i in range(total)]
    count=max(1,(total+499)//500)
    result=[]
    last=None
    for j in range(count):
        chunk=records[j*500:(j+1)*500]
        result.append({
            "schema":1,"owner":"hermes-local","generation":gen,
            "snapshot_id":"synthetic-snapshot",
            "issued_at_ms": NOW-30000,
            "expires_at_ms": NOW+120000,
            "page_number":j,"page_count":count,"total":total,
            "previous_task_id":last,
            "last_task_id":chunk[-1]["task_id"] if chunk else last,
            "previous_page_sha256":None,
            "snapshot_digest_sha256":c.digest(records),
            "records":chunk,
            "signature":"",
        })
        if chunk:
            last=chunk[-1]["task_id"]
    return reseal(result)


def check(items,now=NOW,floor=None,verifier=verify):
    return c.verify_candidate_pages(
        items,now_ms=now,
        external_floor={"owner":"hermes-local","accepted_generation":1}
        if floor is None else floor,
        signature_verifier=verifier,
    )


def denied(items,**kw):
    result=check(items,**kw)
    assert result["candidate_pages_consistent"] is False
    assert result["candidate_count"] is None
    assert result["candidate_digest_sha256"] is None
    assert result["production_dispatch_authorized"] is False
    assert result["decision"]=="HOLD"
    assert "synthetic-" not in str(result)


@pytest.mark.parametrize("count", [0,1,500,501,621,10000])
def test_baseline_page_counts_never_authorize_release(count):
    result=check(pages(count))
    assert result["candidate_pages_consistent"]
    assert result["candidate_count"]==count
    assert result["source_generation_fenced"] is False
    assert result["externally_anchored_floor_verified"] is False
    assert result["independent_key_custody_verified"] is False
    assert result["production_dispatch_authorized"] is False
    assert result["decision"]=="HOLD"


def test_plaintext_cursor_tamper_signature_rejected():
    data=pages()
    data[1]["previous_task_id"]="synthetic-00001"
    denied(data)


@pytest.mark.parametrize("edit",[
    lambda p:p.pop(),
    lambda p:p.append(deepcopy(p[-1])),
    lambda p:p.reverse(),
    lambda p:p[1].update({"page_number":0}),
    lambda p:p[1].update({"generation":999}),
    lambda p:p[1].update({"owner":"hermes-other"}),
    lambda p:p[1].update({"snapshot_id":"different"}),
    lambda p:p[1].update({"previous_task_id":"synthetic-00001"}),
    lambda p:p[0].update({"total":500}),
    lambda p:p[0].update({"page_count":3}),
    lambda p:p[0].update({"expires_at_ms":NOW-1}),
])
def test_partial_reorder_cross_owner_or_metadata_change_denied_even_if_resigned(edit):
    data=pages()
    edit(data)
    reseal(data)
    denied(data)


@pytest.mark.parametrize("change",[
    lambda q:q[1]["records"].pop(),
    lambda q:q[1]["records"].append(deepcopy(q[1]["records"][-1])),
    lambda q:q[1]["records"][0].update({"task_id":q[0]["records"][-1]["task_id"]}),
    lambda q:q[1]["records"][0].update({"claimed_by":"hermes-shadow"}),
    lambda q:q[1]["records"][0].update({"claim_id":None}),
    lambda q:q[1]["records"][0].update({"attempt":0}),
    lambda q:q[1]["records"][0].update({"lease_expiry":True}),
    lambda q:q[1]["records"][0].update({"resource_effect_ref":None}),
    lambda q:q[1]["records"][0].update({"revocation_state":"settled_by_worker"}),
    lambda q:q[1]["records"][0].update({"exposed_payload":"not allowed"}),
])
def test_record_mutation_denied_with_a_valid_synthetic_signature(change):
    data=pages()
    change(data)
    reseal(data)
    denied(data)


def test_bad_chained_page_digest_is_rejected_even_when_locally_resigned():
    data=pages()
    data[1]["previous_page_sha256"]="0"*64
    data[1]["signature"]=sign(c.DOMAIN+c.canonical(c.payload(data[1])))
    denied(data)


def test_digest_recompute_does_not_count_as_authenticated_source_snapshot():
    data=pages()
    data[1]["records"][0]["claim_id"]="altered-claim"
    reseal(data)
    denied(data)
    # Even if a locally controlled synthetic test signer recomputes the
    # digest, no source ownership or external custody proof is established.
    data[0]["snapshot_digest_sha256"]=c.digest(
        data[0]["records"]+data[1]["records"])
    data[1]["snapshot_digest_sha256"]=data[0]["snapshot_digest_sha256"]
    reseal(data)
    result=check(data)
    assert result["candidate_pages_consistent"]
    assert result["source_generation_fenced"] is False
    assert result["independent_key_custody_verified"] is False
    assert result["production_dispatch_authorized"] is False


def test_stale_restore_of_old_snapshot_and_floor_denied():
    data=pages(gen=2)
    denied(data, floor={"owner":"hermes-local","accepted_generation":2})
    denied(data, floor={"owner":"hermes-other","accepted_generation":1})
    denied(data, floor={"owner":"hermes-local","accepted_generation":True})


@pytest.mark.parametrize("time", [NOW+120000,NOW+999999])
def test_expired_page_denied(time):
    denied(pages(),now=time)


def test_bad_verifier_or_missing_custody_denied():
    denied(pages(),verifier=lambda *a:False)
    denied(pages(),verifier=lambda *a:1)
    def crash(*args):
        raise RuntimeError("synthetic-key-secret-no-echo")
    denied(pages(),verifier=crash)
    denied(pages(),floor=None,now="not_time")
    denied(pages(),floor={"owner":"hermes-local"})


def test_external_signature_callback_receives_canonical_full_payload():
    seen=[]
    def observer(data,signature):
        seen.append((data,signature))
        return verify(data,signature)
    report=check(pages(),verifier=observer)
    assert report["candidate_pages_consistent"]
    assert len(seen)==2
    assert b"signature" not in seen[0][0]
    assert b"synthetic-00000" in seen[0][0]


def test_boolean_integer_metadata_not_accepted():
    for field in ("page_number","total","generation","issued_at_ms",
                  "expires_at_ms","page_count"):
        data=pages(total=1)
        data[0][field]=True
        reseal(data)
        denied(data)
