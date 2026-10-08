"""Audit receipts are append-only and fail closed on modification."""
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fleet_custody_chain import append_receipt, verify_records
from render_fleet_burn_custody import render


def test_chain_continuity_and_tamper_detection(tmp_path):
    report=tmp_path/"report.json"
    panel=tmp_path/"panel.html"
    ledger=tmp_path/"ledger.jsonl"
    report.write_text(json.dumps({"generated_epoch": 1234, "complete": True}))
    panel.write_text("first panel")
    a=append_receipt(report,panel,ledger)
    b=append_receipt(report,panel,ledger)
    assert a["sequence"] == 1 and b["sequence"] == 2
    assert b["previous_sha256"] == a["chain_sha256"]
    assert verify_records(ledger.read_bytes())[0] == 2
    raw=ledger.read_text()
    ledger.write_text(raw.replace('"sequence":1', '"sequence":9'))
    with pytest.raises(ValueError, match="digest_mismatch"):
        append_receipt(report,panel,ledger)
    assert ledger.read_text().count("\n") == 2


def test_chain_does_not_overwrite_symlink(tmp_path):
    r=tmp_path/"report.json"
    p=tmp_path/"panel.html"
    target=tmp_path/"destination.txt"
    link=tmp_path/"ledger.jsonl"
    r.write_text('{"generated_epoch":123}')
    p.write_text("html")
    target.write_text("sensitive")
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        append_receipt(r,p,link)
    assert target.read_text() == "sensitive"


def test_static_panel_does_not_promote_token_claims():
    document={"generated_epoch":1791450000,"expected_nodes":2,
              "observed_manifests":1,"quarantined_nodes":1,
              "stale_nodes":0,"content_hash_verified_nodes":0,
              "nodes":[{"node":"<invalid>","status":"QUARANTINED","reason":"missing_latest",
                        "age_hours":None,"archive_hash_verified":False}]}
    page=render(document)
    assert "&lt;invalid&gt;" in page and "<invalid>" not in page
    assert "Cross-node token aggregation: NOT QUALIFIED" in page
    assert "archive hashes verified 0" in page
