"""Memory-only audit chain acceptance, no writable journal descriptor."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from assistx.trace_execution_adapter import SCHEMA, _canonical

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/trace_node_readonly_chain.py"
SPEC = importlib.util.spec_from_file_location("trace_node_readonly_chain", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)
ADAPTER = ROOT / "src/assistx/trace_execution_adapter.py"


def fixture(tmp_path, *, node="xwing", corrupted=False):
    root = tmp_path / "audit"
    root.mkdir(mode=0o700)
    event = {
        "schema": SCHEMA, "seq": 1, "node_id": node,
        "prev_hash": "0" * 64, "event": "prepared",
        "task_id": "synthetic-only", "claim_id": "not-a-real-claim",
    }
    digest = hashlib.sha256(_canonical(event)).hexdigest()
    event["entry_hash"] = ("f" * 64) if corrupted else digest
    path = root / "journal.jsonl"
    path.write_bytes((json.dumps(event) + "\n").encode())
    path.chmod(0o600)
    return root, path


def collect(root, node="xwing", sha=None):
    expected = sha or hashlib.sha256(ADAPTER.read_bytes()).hexdigest()
    return PROBE.collect(str(ROOT), str(root), node, expected)


def test_valid_chain_is_readonly_and_has_head_digest(tmp_path):
    folder, journal = fixture(tmp_path)
    old_data, before = journal.read_bytes(), journal.stat()
    r = collect(folder)
    assert r["readonly_chain_pass"]
    assert r["chain_valid"] and r["journal_unchanged"]
    assert r["record_count"] == 1
    assert r["head_sha256"]
    assert r["journal_sha256"] == hashlib.sha256(old_data).hexdigest()
    assert journal.read_bytes() == old_data
    assert journal.stat().st_mtime_ns == before.st_mtime_ns
    assert r["independent_worm_witness_verified"] is False
    assert r["production_dispatch_authorized"] is False
    assert r["promotion_eligible"] is False


def test_corrupt_chain_is_denied_without_repair(tmp_path):
    folder, journal = fixture(tmp_path, corrupted=True)
    old = journal.read_bytes()
    r = collect(folder)
    assert not r["readonly_chain_pass"]
    assert not r["chain_valid"]
    assert journal.read_bytes() == old
    assert "synthetic-only" not in json.dumps(r)


def test_wrong_node_cannot_be_relabelled(tmp_path):
    folder, _ = fixture(tmp_path, node="xwing")
    r = collect(folder, node="scotts-macbook-air")
    assert not r["readonly_chain_pass"]
    assert r["error_kind"] == "ValueError"


def test_wrong_source_digest_refuses_validation(tmp_path):
    folder, _ = fixture(tmp_path)
    r = collect(folder, sha="e" * 64)
    assert r["source_digest_match"] is False
    assert r["chain_valid"] is False
    assert not r["readonly_chain_pass"]


def test_symlink_journal_is_rejected_without_following(tmp_path):
    folder = tmp_path / "audit"
    folder.mkdir(mode=0o700)
    target = tmp_path / "secret"
    target.write_text("sensitive", encoding="utf-8")
    (folder / "journal.jsonl").symlink_to(target)
    r = collect(folder)
    assert not r["readonly_chain_pass"]
    assert r["error_kind"] == "ValueError"
    assert "sensitive" not in json.dumps(r)
