"""Offline physical negative witness cannot authorize execution or alter receipts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "trace_node_offline_negative.py"
SPEC = importlib.util.spec_from_file_location("trace_node_offline_negative", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
EXECUTOR = ROOT / "src/assistx/trace_claim_live_executor.py"


def make_journal(tmp_path):
    root = tmp_path / "audit"
    root.mkdir(mode=0o700)
    file = root / "journal.jsonl"
    file.write_bytes(b'{"synthetic":1}\n')
    file.chmod(0o600)
    return root, file


def test_actual_offline_denial_with_pinned_source_and_untouched_journal(tmp_path):
    audit, journal = make_journal(tmp_path)
    before = journal.read_bytes()
    expected = hashlib.sha256(EXECUTOR.read_bytes()).hexdigest()
    report = MODULE.collect(str(ROOT), "xwing", str(audit), expected)
    assert report["source_digest_match"]
    assert report["offline_negative_pass"] is True
    assert report["audit_unchanged"]
    assert journal.read_bytes() == before
    assert len(report["cases"]) == 2
    assert all(c["decision"] == "real_trace_execution_disabled" for c in report["cases"])
    assert report["authenticated_claim_tested"] is False
    assert report["network_calls"] == report["claims_issued"] == 0
    assert report["production_dispatch_authorized"] is False
    assert report["promotion_eligible"] is False


def test_wrong_executor_digest_fails_before_loading(tmp_path):
    audit, journal = make_journal(tmp_path)
    report = MODULE.collect(str(ROOT), "xwing", str(audit), "f" * 64)
    assert report["source_digest_match"] is False
    assert report["offline_negative_pass"] is False
    assert report["failure"] == "ValueError"
    assert report["cases"] == []
    assert report["audit_unchanged"]
    assert "synthetic" not in json.dumps(report)


@pytest.mark.parametrize("digest", ["", "bad", "Z" * 64])
def test_invalid_digest_denies_without_write(tmp_path, digest):
    audit, journal = make_journal(tmp_path)
    data = journal.read_bytes()
    report = MODULE.collect(str(ROOT), "xwing", str(audit), digest)
    assert not report["offline_negative_pass"]
    assert journal.read_bytes() == data


def test_symlink_journal_is_never_followed(tmp_path):
    audit = tmp_path / "audit"
    audit.mkdir(mode=0o700)
    private = tmp_path / "secret"
    private.write_text("do not read", encoding="utf-8")
    (audit / "journal.jsonl").symlink_to(private)
    f = MODULE.audit_fingerprint(str(audit))
    assert f["status"] == "unsafe_or_oversized_journal"
    assert "do not read" not in json.dumps(f)
