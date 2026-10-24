"""Source-only idempotency receipt validation and deny-before-DB tests."""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from assistx import hermes_source_receipt_research as sr
from assistx.hermes_source_generation_research import GenerationProtocolError

KEY="4f36489f-3b2c-4410-a22f-acf5467528a1"

def request(operation="heartbeat"):
    d={
        "operation":operation,"receipt_key":KEY,"expected_generation":2,
        "task_id":"synthetic-task","claim_id":"synthetic-claim",
        "session_id":"synthetic-session","attempt":1,
    }
    if operation=="heartbeat":
        d["ttl_ms"]=15000
    return d

class NoDB:
    def __getattr__(self,name):
        raise AssertionError("database accessed before input validation")

@pytest.mark.parametrize("changes",[
    {"operation":"delete"},{"operation":"cypher"},
    {"receipt_key":"not-uuid"},
    {"receipt_key":"f53c713e-fd9d-13ed-955a-0c651d57f560"},
    {"receipt_key":KEY.upper()},
    {"receipt_key":str(uuid.UUID(int=0))},
    {"expected_generation":True},{"expected_generation":-1},
    {"task_id":""},{"task_id":"../other"},{"claim_id":"bad token"},
    {"session_id":"a"*129},{"attempt":True},{"attempt":0},
    {"ttl_ms":True},{"ttl_ms":999},{"ttl_ms":60001},
])
def test_invalid_heartbeat_refuses_before_db(changes):
    x=request()
    x.update(changes)
    with pytest.raises(GenerationProtocolError,match="invalid_source_receipt_request"):
        sr.commit_source_receipt(NoDB(),**x)
    with pytest.raises(GenerationProtocolError,match="invalid_source_receipt_request"):
        sr.reconcile_source_receipt(NoDB(),**x)

@pytest.mark.parametrize("changes",[
    {"ttl_ms":1000},{"ttl_ms":True},
    {"attempt":0},{"task_id":"invalid/"}
])
def test_invalid_completion_refuses_before_db(changes):
    x=request("complete")
    x.update(changes)
    with pytest.raises(GenerationProtocolError,match="invalid_source_receipt_request"):
        sr.commit_source_receipt(NoDB(),**x)

def test_source_digest_binds_exact_request_without_plaintext():
    x=request()
    fingerprint,canonical=sr._normalized(**x)
    assert len(fingerprint)==64
    assert canonical["task_id"]=="synthetic-task"
    assert canonical["attempt"]==1
    diff={**x,"claim_id":"different-claim"}
    changed,_=sr._normalized(**diff)
    assert fingerprint!=changed
    alt={**x,"receipt_key":str(uuid.uuid4())}
    same,_=sr._normalized(**alt)
    assert fingerprint==same  # key is bound by uniqueness; not credential
    c={**x,"ttl_ms":20000}
    different,_=sr._normalized(**c)
    assert fingerprint!=different

def test_missing_receipt_constraint_denied_with_known_error():
    s=MagicMock()
    s.run.return_value.single.return_value=None
    with pytest.raises(GenerationProtocolError,match="source_receipt_uniqueness_constraint_missing"):
        sr._require_receipt_constraint(s)

def test_matching_constraint_has_expected_label_property():
    s=MagicMock()
    s.run.return_value.single.return_value={
        "type":"UNIQUENESS","labelsOrTypes":["HermesSourceReceipt"],
        "properties":["key"],
    }
    sr._require_receipt_constraint(s)

@pytest.mark.parametrize("row",[
    {"type":"NODE_KEY","labelsOrTypes":["HermesSourceReceipt"],"properties":["key"]},
    {"type":"UNIQUENESS","labelsOrTypes":["HermesSourceReceipt"],"properties":["owner"]},
    {"type":"UNIQUENESS","labelsOrTypes":["Task"],"properties":["key"]},
])
def test_wrong_constraint_denied(row):
    s=MagicMock()
    s.run.return_value.single.return_value=row
    with pytest.raises(GenerationProtocolError,match="source_receipt_uniqueness_constraint_missing"):
        sr._require_receipt_constraint(s)

def test_hold_receipt_never_asserts_external_proof():
    for reconciled in (True,False):
        d=sr._receipt_result(3,reconciled=reconciled)
        assert d["candidate_generation"]==3
        assert d["source_receipt_committed"]
        assert d["source_receipt_reconciled"] is reconciled
        for key in ("socket_delivery_durable",
                    "independent_custody_verified",
                    "all_writers_generation_fenced",
                    "resource_effects_verified",
                    "production_dispatch_authorized"):
            assert d[key] is False
        assert d["decision"]=="HOLD"
        assert "task_id" not in d
        assert "claim_id" not in d

def test_reconcile_never_calls_write_tx(monkeypatch):
    monkeypatch.setattr(sr,"_check_constraint",lambda s:None)
    monkeypatch.setattr(sr,"_require_receipt_constraint",lambda s:None)
    s=MagicMock()
    s.run.return_value.single.return_value={
        "key":KEY,"generation":3,
    }
    out=sr.reconcile_source_receipt(s,**request())
    assert out["candidate_generation"]==3
    assert out["source_receipt_reconciled"] is True
    s.execute_write.assert_not_called()
    assert "MATCH(r:HermesSourceReceipt" in s.run.call_args.args[0]

def test_mismatched_source_receipt_never_infers_noncommit(monkeypatch):
    monkeypatch.setattr(sr,"_check_constraint",lambda s:None)
    monkeypatch.setattr(sr,"_require_receipt_constraint",lambda s:None)
    s=MagicMock()
    s.run.return_value.single.return_value=None
    with pytest.raises(GenerationProtocolError,match="source_receipt_not_verified"):
        sr.reconcile_source_receipt(s,**request())
    s.execute_write.assert_not_called()

def test_production_modules_never_import_candidate_receipt():
    root=Path(__file__).resolve().parents[1]/"src/assistx"
    for file in root.rglob("*.py"):
        if file.name.startswith("hermes_source_") or file.name.startswith("hermes_broker_"):
            continue
        assert "hermes_source_receipt_research" not in file.read_text(),file
