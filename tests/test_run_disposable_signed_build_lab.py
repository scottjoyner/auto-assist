"""Fail-closed Docker lifecycle and two-node signer lab controls (no Docker)."""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

import importlib
import sys
from pathlib import Path

# The full repository's pytest environment may install an unrelated
# top-level "scripts" package. Load exact sibling research files
# instead of importing that ambient package by name.
source_dir = str(Path(__file__).resolve().parents[1] / "scripts")
sys.path.insert(0, source_dir)
lab = importlib.import_module("run_disposable_signed_build_lab")
provenance = importlib.import_module("verify_signed_image_provenance")
ProvenanceHold = provenance.ProvenanceHold
verify = provenance.verify


def config(*, network="none", readonly=True, capdrop=None, opts=None):
    return {
        "Id": "a" * 64,
        "Name": "/" + lab.CONTAINER,
        "Image": "sha256:" + "f" * 64,
        "State": {"Status": "running"},
        "HostConfig": {
            "NetworkMode": network, "ReadonlyRootfs": readonly,
            "CapDrop": capdrop if capdrop is not None else ["ALL"],
            "SecurityOpt": opts if opts is not None else ["no-new-privileges"],
        },
        "Mounts": [],
    }


def test_empty_container_name_and_preexisting_image_refuse_any_delete(monkeypatch):
    assert lab.git_blob_sha(lab.SOURCE.read_bytes()) == lab.EXPECTED_SOURCE_BLOB
    target = "sha256:" + "f" * 64
    calls = []

    monkeypatch.setattr(lab, "_docker_local", lambda: "daemon-local")
    monkeypatch.setattr(
        lab, "_inspect",
        lambda kind, _name: None if kind == "container" else {"Id": target},
    )
    monkeypatch.setattr(
        lab, "inspect_oci_file", lambda *a, **kw: {"manifest_sha256": "f"*64},
    )

    def fake_cmd(*args, **kw):
        calls.append(args)
        if args[0] == "docker" and args[1] in ("load", "run", "rm", "image"):
            pytest.fail("foreign preexisting OCI image must never be touched")
        return ""

    monkeypatch.setattr(lab, "_cmd", fake_cmd)
    with pytest.raises(lab.LabHold, match="preexisted"):
        lab.run()
    assert not any("rm" in c for c in calls)


def test_foreign_container_name_collision_denies_before_build(monkeypatch):
    monkeypatch.setattr(lab, "_docker_local", lambda: "daemon-local")
    monkeypatch.setattr(lab, "_inspect", lambda *_: {"Id": "foreign"})
    monkeypatch.setattr(
        lab, "_cmd",
        lambda *_a, **_kw: pytest.fail("unsafe stage name collision"),
    )
    with pytest.raises(lab.LabHold, match="already occupied"):
        lab.run()


@pytest.mark.parametrize("field,changed", [
    ("NetworkMode", "bridge"),
    ("ReadonlyRootfs", False),
    ("CapDrop", []),
    ("SecurityOpt", ["apparmor=unconfined"]),
])
def test_native_disposable_runtime_rejects_weakened_isolation(
    monkeypatch, field, changed,
):
    container = config()
    container["HostConfig"][field] = changed
    monkeypatch.setattr(
        lab, "_inspect",
        lambda kind, _name: container if kind == "container" else {
            "Id": container["Image"],
        },
    )
    with pytest.raises(lab.LabHold, match="isolation"):
        lab._real_runtime("a" * 64, "sha256:" + "f" * 64)


def test_native_runtime_rejects_any_host_mount(monkeypatch):
    container = config()
    container["Mounts"] = [{"Type": "bind", "Source": "/host-data"}]
    monkeypatch.setattr(
        lab, "_inspect",
        lambda kind, _name: container if kind == "container" else {
            "Id": container["Image"],
        },
    )
    with pytest.raises(lab.LabHold, match="isolation"):
        lab._real_runtime("a"*64, "sha256:"+"f"*64)


def test_pinned_remote_signer_key_is_ephemeral_and_claim_not_trust():
    body = lab._statement("b" * 64)
    obj = json.loads(body)
    assert obj["predicate"]["buildDefinition"]["externalParameters"]["sourceRevision"] == lab.EXPECTED_GIT_SHA
    assert obj["predicate"]["runDetails"]["builder"]["id"] == lab.BUILDER
    assert obj["subject"][0]["digest"]["sha256"] == "b" * 64
    assert b"privkey" not in body
    assert b"SECRET" not in body


def test_attestation_signature_with_forged_builder_denied(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    PAYLOAD_TYPE = provenance.PAYLOAD_TYPE
    pae = provenance.pae

    signer = Ed25519PrivateKey.generate()
    pub = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = lab._statement("a" * 64)
    statement = json.loads(payload)
    statement["predicate"]["runDetails"]["builder"]["id"] = "https://evil.test/builder"
    forged = json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    envelope = {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(forged).decode(),
        "signatures": [{
            "keyid": hashlib.sha256(pub).hexdigest(),
            "sig": base64.b64encode(signer.sign(pae(PAYLOAD_TYPE, forged))).decode(),
        }],
    }
    runtime = {
        "name": lab.SERVICE, "container_id": "c"*64, "state": "running",
        "image": {
            "local_image_id": "sha256:"+"d"*64,
            "manifest_digest_values": ["sha256:"+"a"*64],
        },
    }
    with pytest.raises(ProvenanceHold, match="builder"):
        verify(
            envelope=envelope, public_key=pub,
            expected_key_sha256=hashlib.sha256(pub).hexdigest(),
            expected_service=lab.SERVICE, expected_git_sha=lab.EXPECTED_GIT_SHA,
            expected_builder_id=lab.BUILDER, expected_build_type=lab.BUILD_TYPE,
            expected_manifest_sha256="a"*64, runtime_record=runtime,
        )


def test_no_remote_docker_override_accepted(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "ssh://remote")
    monkeypatch.setattr(
        lab, "_cmd",
        lambda *_a, **_kw: pytest.fail("remote Docker must be rejected preflight"),
    )
    with pytest.raises(lab.LabHold, match="override"):
        lab._docker_local()


def test_missing_remote_signer_causes_explicit_hold(monkeypatch):
    monkeypatch.setattr(
        lab, "_cmd",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            lab.LabHold("remote signer unavailable")
        ),
    )
    with pytest.raises(lab.LabHold, match="remote signer"):
        lab._destroyer_sign(lab._statement("a"*64))


def test_cli_never_converts_failed_native_lab_to_success(monkeypatch, capsys):
    monkeypatch.setattr(
        lab, "run", lambda: (_ for _ in ()).throw(
            lab.LabHold("stage refused")
        ),
    )
    assert lab.main() == 3
    out = json.loads(capsys.readouterr().out)
    assert out["production_decision"] == "HOLD"
    assert out["promotion_authorized"] is False
