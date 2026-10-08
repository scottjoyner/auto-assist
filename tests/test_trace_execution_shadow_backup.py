"""Offline custody/rollback tests; no remote SSH or real NAS needed."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import trace_execution_shadow_backup as custody
import trace_execution_shadow_bootstrap as setup

from assistx.trace_execution_adapter import TraceDenied, TraceReceiptStore, run_trace_probe

REGISTRY = Path(__file__).resolve().parents[1] / "config/trace_execution_shadow_nodes.json"


def generate_journal(tmp_path, node_id="xwing", task="task-one"):
    root = tmp_path / "source"
    root.mkdir(mode=0o700, exist_ok=True)
    run_trace_probe(
        {
            "id": task,
            "target_agent_id": node_id,
            "payload": {"command_id": "probe.noop.v1"},
        },
        node_id=node_id,
        claim_id="claim-" + task,
        audit_root=str(root),
        enabled=True,
    )
    return (root / "journal.jsonl").read_bytes()


def setup_secrets(tmp_path):
    private = tmp_path / "private"
    anchors = tmp_path / "anchors"
    setup.private_dir(private)
    setup.private_dir(anchors)
    for node in ["xwing", "scotts-macbook-air"]:
        setup.private_dir(private / node)
        setup.private_dir(anchors / node)
        assert setup.new_key(private / node / "encryption.passphrase")
        assert setup.new_key(private / node / "anchor.key")
    return private, anchors


def test_distinct_per_node_keys_and_nonoverwrite(tmp_path):
    private, _ = setup_secrets(tmp_path)
    x = private / "xwing" / "anchor.key"
    m = private / "scotts-macbook-air" / "anchor.key"
    assert x.read_bytes() != m.read_bytes()
    assert x.stat().st_mode & 0o077 == 0
    assert setup.new_key(x) is False
    assert len(custody.secret_file(x).strip()) >= 64


def test_private_directory_and_unsafe_key_denied(tmp_path):
    private, _ = setup_secrets(tmp_path)
    file = private / "xwing" / "anchor.key"
    file.chmod(0o644)
    with pytest.raises(TraceDenied, match="unsafe_secret_file"):
        custody.secret_file(file)
    file.chmod(0o600)
    (private / "xwing").chmod(0o755)
    with pytest.raises(TraceDenied, match="unsafe_private_directory"):
        custody.ensure_private_dir(private / "xwing")


def test_fetch_remote_verifies_identity_digest_and_full_chain(tmp_path, monkeypatch):
    raw = generate_journal(tmp_path)
    records = TraceReceiptStore._verify_data(raw)
    envelope = {
        "schema": "assistx.trace-shadow-export.v1",
        "node_id": "xwing",
        "records": len(records),
        "last_hash": records[-1]["entry_hash"],
        "journal_sha256": hashlib.sha256(raw).hexdigest(),
        "journal_base64": base64.b64encode(raw).decode("ascii"),
    }

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, json.dumps(envelope).encode(), b"")

    monkeypatch.setattr(custody.subprocess, "run", fake_run)
    node = custody.load_paths(str(REGISTRY))["xwing"]
    result, meta = custody.fetch_remote(node)
    assert result == raw and meta["records"] == 2
    envelope["node_id"] = "scotts-macbook-air"
    with pytest.raises(TraceDenied, match="remote_export_identity_invalid"):
        custody.fetch_remote(node)
    envelope["node_id"] = "xwing"
    envelope["journal_sha256"] = "0" * 64
    with pytest.raises(TraceDenied, match="remote_export_hash_invalid"):
        custody.fetch_remote(node)


@pytest.mark.skipif(not Path("/usr/bin/gpg").exists(), reason="gpg unavailable")
def test_encrypted_archive_restore_and_rollback_gate(tmp_path, monkeypatch):
    raw = generate_journal(tmp_path)
    private, anchors = setup_secrets(tmp_path)
    destination = tmp_path / "fake_nas"
    destination.mkdir()
    node = custody.load_paths(str(REGISTRY))["xwing"]
    monkeypatch.setattr(custody, "assert_cifs", lambda path: None)
    monkeypatch.setattr(
        custody,
        "fetch_remote",
        lambda _: (
            raw,
            {
                "node_id": "xwing",
                "records": 2,
                "last_hash": TraceReceiptStore._verify_data(raw)[-1]["entry_hash"],
                "journal_sha256": custody.sha(raw),
            },
        ),
    )
    first = custody.backup(node, private=private, anchors=anchors, destination=destination)
    landed = destination / first["archive"]
    assert landed.is_file()
    assert landed.read_bytes() != raw
    assert custody.sha(landed.read_bytes()) == first["encrypted_sha256"]
    assert custody.decrypt(landed, private / "xwing" / "encryption.passphrase") == raw
    assert (landed.with_name(landed.name + ".manifest.json")).is_file()
    # A later valid journal extension is accepted and anchored.
    source = tmp_path / "source"
    run_trace_probe(
        {
            "id": "task-two",
            "target_agent_id": "xwing",
            "payload": {"command_id": "probe.noop.v1"},
        },
        node_id="xwing",
        claim_id="claim-task-two",
        audit_root=str(source),
        enabled=True,
    )
    newer = (source / "journal.jsonl").read_bytes()
    custody.verify_continuation(
        newer,
        node_id="xwing",
        anchors=anchors / "xwing",
        key=custody.secret_file(private / "xwing" / "anchor.key"),
    )
    monkeypatch.setattr(
        custody,
        "fetch_remote",
        lambda _: (
            newer,
            {
                "node_id": "xwing",
                "records": 4,
                "last_hash": TraceReceiptStore._verify_data(newer)[-1]["entry_hash"],
                "journal_sha256": custody.sha(newer),
            },
        ),
    )
    second = custody.backup(node, private=private, anchors=anchors, destination=destination)
    assert second["records"] == 4
    assert second["signed_head"] != first["signed_head"]
    with pytest.raises(TraceDenied, match="remote_journal_rollback_or_rewrite"):
        custody.verify_continuation(
            raw,
            node_id="xwing",
            anchors=anchors / "xwing",
            key=custody.secret_file(private / "xwing" / "anchor.key"),
        )
    assert len((anchors / "xwing" / "signed-heads.jsonl").read_text().splitlines()) == 2
    assert (
        custody.verify_archives(node, private=private, anchors=anchors, destination=destination)["archives_verified"]
        == 2
    )
    # Mutating ciphertext never passes the independent sealed restore gate.
    landed.write_bytes(b"tampered ciphertext")
    with pytest.raises(TraceDenied, match="archive_ciphertext_hash_mismatch"):
        custody.verify_archives(node, private=private, anchors=anchors, destination=destination)
    (anchors / "xwing" / "signed-heads.jsonl").unlink()
    with pytest.raises(TraceDenied, match="anchor_ledger_missing_with_archives_present"):
        custody.backup(node, private=private, anchors=anchors, destination=destination)


def test_cifs_refusal_without_verified_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(custody.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "ext4\n", ""))
    with pytest.raises(TraceDenied, match="not_verified_cifs_mount"):
        custody.assert_cifs(tmp_path)
