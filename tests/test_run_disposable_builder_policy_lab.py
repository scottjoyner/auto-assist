"""No SSH calls: negative source contracts of ephemeral multi-node test."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT_DIR = str(Path(__file__).resolve().parents[1] / "scripts")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
lab = importlib.import_module("run_disposable_builder_policy_lab")


@pytest.mark.parametrize("host,typ,size", [
    ("root", lab.POLICY_TYPE, 18),
    ("localhost", lab.POLICY_TYPE, 18),
    ("destroyer", "application/x-malicious", 18),
    ("xwing", lab.POLICY_TYPE, 0),
    ("xwing", lab.POLICY_TYPE, 9000),
])
def test_disposable_signer_refuses_foreign_requests_before_ssh(
    monkeypatch, host, typ, size,
):
    monkeypatch.setattr(
        lab.subprocess, "run",
        lambda *_a, **_kw: pytest.fail("unsafe request must not SSH"),
    )
    with pytest.raises(lab.ResearchHold, match="unapproved"):
        lab._remote_sign(host, typ, b"a" * size)


def test_signer_requests_host_key_check_and_single_sealed_input(monkeypatch):
    calls = []

    def respond(argv, input=None, **kwargs):
        calls.append((argv, input, kwargs))
        return SimpleNamespace(returncode=3, stdout=b"",
                               stderr=b"token=SECRET-DO-NOT-LEAK")

    monkeypatch.setattr(lab.subprocess, "run", respond)
    with pytest.raises(lab.ResearchHold) as exc:
        lab._remote_sign("destroyer", lab.PAYLOAD_TYPE, b"{}")
    assert "SECRET" not in str(exc.value)
    assert len(calls) == 1
    argv, payload, kwargs = calls[0]
    assert argv[0:2] == ["ssh", "-T"]
    assert "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "destroyer" in argv
    assert "python3 -c" in argv[-1]
    assert payload == b"{}"
    assert kwargs["timeout"] <= 20


def test_malformed_signer_json_is_denied_without_private_key_logging(monkeypatch):
    monkeypatch.setattr(lab.subprocess, "run", lambda *_a, **_kw:
                        SimpleNamespace(returncode=0, stdout=b'{"weird":"credential"}'))
    with pytest.raises(lab.ResearchHold, match="response"):
        lab._remote_sign("xwing", lab.POLICY_TYPE, b"{}")


def test_policy_and_statement_tie_same_commit_and_manifest():
    import hashlib
    now = 1800000000
    stmt = lab._statement()
    pol = lab._policy(b"x" * 32, now)
    assert stmt["predicate"]["buildDefinition"]["externalParameters"][
        "sourceRevision"
    ] == pol["source_commit"] == lab.COMMIT
    assert stmt["subject"][0]["digest"]["sha256"] == pol["manifest_sha256"]
    assert pol["builder_key_sha256"] == hashlib.sha256(b"x" * 32).hexdigest()
    assert pol["expires_at"] - pol["not_before"] <= 900


def test_stage_is_not_allowed_to_claim_live_runtime_evidence():
    record = lab._fixture_runtime()
    assert record["container_id"] == "b" * 64
    assert record["image"]["local_image_id"] == "sha256:" + "c"*64
    assert record["image"]["manifest_digest_values"] == [
        "sha256:" + lab.MANIFEST
    ]


def test_cli_fails_hold_if_no_signer_contact(monkeypatch, capsys):
    monkeypatch.setattr(
        lab, "_remote_sign",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            lab.ResearchHold("no ephemeral signer")
        ),
    )
    assert lab.main() == 3
    result = json.loads(capsys.readouterr().out)
    assert result["production_decision"] == "HOLD"
    assert result["promotion_authorized"] is False
