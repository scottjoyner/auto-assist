"""Offline node-bound signature gates; no real AssistX claims or SSH."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.trace_execution_adapter import TraceDenied, TraceReceiptStore
from assistx.trace_shadow_grant import new_grant, verify_grant

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def signer():
    return Ed25519PrivateKey.generate()


def test_node_bound_grant_passes_with_strict_30_second_ttl(signer):
    t = 2_000_000_000_000
    doc = new_grant(signer=signer, node_id="xwing", now_ms=t)
    assert verify_grant(doc, node_id="xwing", verifier=signer.public_key(), now_ms=t + 100)
    assert doc["expires_at_ms"] - doc["issued_at_ms"] == 30_000
    assert doc["issuer"] == "offline-shadow-test-fixture"
    assert doc["command_id"] == "probe.noop.v1"


@pytest.mark.parametrize(
    "mutation,error",
    [
        (lambda d: d.update(node_id="scotts-macbook-air"), "wrong_execution_node"),
        (lambda d: d.update(task_id="replaced-task"), "grant_lineage_invalid"),
        (lambda d: d.update(command_id="shell.command"), "unapproved_command_id"),
        (lambda d: d.update(expires_at_ms=d["expires_at_ms"] + 5), "grant_ttl_invalid"),
        (lambda d: d.update(key_id="shadow-grant-scotts-macbook-air-v1"), "wrong_grant_key_id"),
        (lambda d: d.update(issuer="assistx"), "not_a_synthetic_shadow_grant"),
        (lambda d: d.update(added_authority="true"), "invalid_grant_schema"),
    ],
)
def test_mutations_fail_closed(signer, mutation, error):
    t = 2_000_000_000_000
    doc = new_grant(signer=signer, node_id="xwing", now_ms=t)
    mutation(doc)
    with pytest.raises(TraceDenied, match=error):
        verify_grant(doc, node_id="xwing", verifier=signer.public_key(), now_ms=t + 10)


def test_invalid_signature_and_wrong_key_rejected(signer):
    t = 2_000_000_000_000
    doc = new_grant(signer=signer, node_id="xwing", now_ms=t)
    doc["signature"] = new_grant(signer=signer, node_id="xwing", now_ms=t)["signature"]
    with pytest.raises(TraceDenied, match="grant_signature_invalid"):
        verify_grant(doc, node_id="xwing", verifier=signer.public_key(), now_ms=t)
    valid = new_grant(signer=signer, node_id="xwing", now_ms=t)
    with pytest.raises(TraceDenied, match="grant_signature_invalid"):
        verify_grant(valid, node_id="xwing", verifier=Ed25519PrivateKey.generate().public_key(), now_ms=t)


def test_expired_future_and_revoked_denied(signer):
    t = 2_000_000_000_000
    doc = new_grant(signer=signer, node_id="xwing", now_ms=t)
    for at, error in [(t - 2001, "grant_issued_in_future"), (t + 30_001, "grant_expired")]:
        with pytest.raises(TraceDenied, match=error):
            verify_grant(doc, node_id="xwing", verifier=signer.public_key(), now_ms=at)
    with pytest.raises(TraceDenied, match="grant_revoked"):
        verify_grant(
            doc,
            node_id="xwing",
            verifier=signer.public_key(),
            now_ms=t + 10,
            revoked_grant_ids=frozenset({doc["grant_id"]}),
        )


def test_node_runner_journals_digest_and_denies_replay(tmp_path, signer):
    from cryptography.hazmat.primitives import serialization

    public = tmp_path / "grant-authority.pem"
    public.write_bytes(
        signer.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    public.chmod(0o600)
    private = tmp_path / "audit"
    private.mkdir(mode=0o700)
    doc = new_grant(signer=signer, node_id="xwing")
    script = ROOT / "scripts/trace_shadow_grant_node.py"
    command = [
        sys.executable,
        str(script),
        "--node-id",
        "xwing",
        "--audit-root",
        str(private),
        "--public-key",
        str(public),
    ]
    env = {"PYTHONPATH": str(ROOT / "src")}
    import os

    env.update({"PATH": os.getenv("PATH", ""), "HOME": os.getenv("HOME", "")})

    def attempt(data):
        return subprocess.run(
            command,
            input=json.dumps(data).encode(),
            env=env,
            capture_output=True,
            timeout=7,
            check=False,
        )

    first = attempt(doc)
    assert first.returncode == 0, first.stderr
    output = json.loads(first.stdout)
    assert output["ok"] and not output["assistx_claim_verified"]
    assert output["signed_shadow_grant_verified"]
    assert TraceReceiptStore(str(private), node_id="xwing").verify()["records"] == 2
    records = [json.loads(line) for line in (private / "journal.jsonl").read_text().splitlines()]
    assert records[0]["signed_grant_sha256"] == output["grant_sha256"]
    assert records[1]["signed_grant_sha256"] == output["grant_sha256"]
    second = attempt(doc)
    assert second.returncode == 2
    assert json.loads(second.stdout)["reason"] == "attempt_already_recorded"
    assert TraceReceiptStore(str(private), node_id="xwing").verify()["records"] == 2


def test_wrong_node_denied_without_journal(tmp_path, signer):
    from cryptography.hazmat.primitives import serialization

    key = tmp_path / "p.pem"
    key.write_bytes(
        signer.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    key.chmod(0o600)
    journal = tmp_path / "journal"
    journal.mkdir(mode=0o700)
    doc = new_grant(signer=signer, node_id="xwing")
    cmd = [
        sys.executable,
        str(ROOT / "scripts/trace_shadow_grant_node.py"),
        "--node-id",
        "scotts-macbook-air",
        "--audit-root",
        str(journal),
        "--public-key",
        str(key),
    ]
    import os

    env = {"PYTHONPATH": str(ROOT / "src"), "PATH": os.getenv("PATH", "")}
    completed = subprocess.run(
        cmd,
        input=json.dumps(doc).encode(),
        env=env,
        capture_output=True,
        timeout=7,
        check=False,
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["reason"] == "wrong_execution_node"
    assert not (journal / "journal.jsonl").exists()


def test_per_node_key_bootstrap_is_idempotent_and_distinct(tmp_path):
    import sys

    from cryptography.hazmat.primitives import serialization

    sys.path.insert(0, str(ROOT / "scripts"))
    import trace_shadow_grant_bootstrap as bootstrap

    from assistx.trace_shadow_grant import private_key, public_key

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    config = str(ROOT / "config/trace_execution_shadow_nodes.json")
    assert bootstrap.provision(config, private) == 4
    assert bootstrap.provision(config, private) == 0
    public_values = []
    for node in ("xwing", "scotts-macbook-air"):
        signer = private_key(private / node / "grant-signing-key.pem")
        trusted = public_key(private / node / "grant-public-key.pem")
        expected = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        got = trusted.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        assert expected == got
        public_values.append(got)
        assert (private / node / "grant-signing-key.pem").stat().st_mode & 0o077 == 0
    assert public_values[0] != public_values[1]


def test_shadow_controller_reports_signed_grant_denial_without_promoting(monkeypatch):
    import sys
    from types import SimpleNamespace

    sys.path.insert(0, str(ROOT / "scripts"))
    import trace_shadow_grant_control as control
    from trace_execution_shadow_control import load_paths

    node = load_paths(str(ROOT / "config/trace_execution_shadow_nodes.json"))["xwing"]
    grant = new_grant(signer=Ed25519PrivateKey.generate(), node_id="xwing")
    capture = {}

    def fake_run(cmd, **kwargs):
        capture["cmd"] = cmd
        capture["payload"] = json.loads(kwargs["input"])
        return SimpleNamespace(
            returncode=2,
            stdout=json.dumps(
                {
                    "synthetic": True,
                    "node_id": "xwing",
                    "assistx_claim_verified": False,
                    "ok": False,
                    "reason": "grant_expired",
                }
            ).encode(),
            stderr=b"",
        )

    monkeypatch.setattr(control.subprocess, "run", fake_run)
    result = control.dispatch(node, grant)
    assert not result["accepted"]
    assert result["reply"]["reason"] == "grant_expired"
    assert "StrictHostKeyChecking=yes" in capture["cmd"]
    assert "BatchMode=yes" in capture["cmd"]
    assert capture["payload"]["issuer"] == "offline-shadow-test-fixture"
    assert capture["payload"]["command_id"] == "probe.noop.v1"


def test_private_grant_signer_rejects_symbolic_link(tmp_path):
    from cryptography.hazmat.primitives import serialization

    from assistx.trace_shadow_grant import private_key

    raw = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    target = tmp_path / "true.pem"
    target.write_bytes(raw)
    target.chmod(0o600)
    link = tmp_path / "shortcut.pem"
    link.symlink_to(target)
    with pytest.raises(TraceDenied, match="grant_key_unavailable"):
        private_key(link)
