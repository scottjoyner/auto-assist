"""Cross-node *artifact-before-sign* synthetic negatives. No Docker/SSH calls."""
from __future__ import annotations

import base64
import gzip
import hashlib
import importlib
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SRC))
remote = importlib.import_module("disposable_remote_artifact_attestor")
lab = importlib.import_module("run_disposable_signed_build_lab")
verify_mod = importlib.import_module("verify_signed_image_provenance")


def json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_tar(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        for name, data in entries:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
    return buf.getvalue()


def artifact(*, source: bytes | None = None, wrong_diff=False,
             bad_manifest=False, extra_blob=False) -> bytes:
    source = source if source is not None else lab.SOURCE.read_bytes()
    plain_a = make_tar([("probe", b"synthetic-static-probe-data")])
    plain_b = make_tar([("source/verify.py", source)])
    plain = [plain_a, plain_b]
    compressed = [gzip.compress(x, mtime=0) for x in plain]
    diff_ids = ["sha256:" + sha(x) for x in plain]
    if wrong_diff:
        diff_ids[1] = "sha256:" + "e" * 64
    config = json_bytes({
        "rootfs": {"type": "layers", "diff_ids": diff_ids},
        "config": {"User": "65534:65534", "Entrypoint": ["/probe"]},
    })
    blobs = {sha(config): config}
    layers = []
    for comp in compressed:
        blobs[sha(comp)] = comp
        layers.append({
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": "sha256:" + sha(comp), "size": len(comp),
        })
    manifest = json_bytes({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:" + sha(config),
            "size": len(config),
        },
        "layers": layers,
    })
    blobs[sha(manifest)] = manifest
    manifest_id = sha(manifest)
    if bad_manifest:
        manifest_id = "f" * 64
    index = json_bytes({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": "sha256:" + manifest_id,
            "size": len(manifest),
        }],
    })
    entries = [
        ("oci-layout", json_bytes({"imageLayoutVersion": "1.0.0"})),
        ("index.json", index),
        *[("blobs/sha256/" + k, v) for k, v in blobs.items()],
    ]
    if extra_blob:
        entries.append(("blobs/sha256/" + "f" * 64, b"orphan"))
    return make_tar(entries)


def expected_runtime(inspected):
    return {
        "name": lab.SERVICE,
        "container_id": "a" * 64,
        "state": "running",
        "image": {
            "local_image_id": "sha256:" + inspected["manifest_sha256"],
            "manifest_digest_values": ["sha256:" + inspected["manifest_sha256"]],
        },
    }


def test_destroyer_validates_archive_and_reconstructs_statement_itself():
    raw = artifact()
    details = remote.inspect(raw)
    assert details["embedded_source_git_blob"] == lab.EXPECTED_SOURCE_BLOB
    assert details["layer_count"] == 2
    result = remote.attest(raw)
    assert result["remote_observed_manifest_sha256"] == details["manifest_sha256"]
    pub = base64.b64decode(result["ephemeral_public_key"])
    signed_payload = json.loads(base64.b64decode(result["envelope"]["payload"]))
    assert signed_payload["subject"][0]["digest"]["sha256"] == details["manifest_sha256"]
    assert signed_payload["predicate"]["buildDefinition"]["externalParameters"]["sourceRevision"] == lab.EXPECTED_GIT_SHA
    assert signed_payload["predicate"]["runDetails"]["builder"]["id"] == lab.BUILDER
    verified = verify_mod.verify(
        envelope=result["envelope"],
        public_key=pub,
        expected_key_sha256=hashlib.sha256(pub).hexdigest(),
        expected_service=lab.SERVICE,
        expected_git_sha=lab.EXPECTED_GIT_SHA,
        expected_builder_id=lab.BUILDER,
        expected_build_type=lab.BUILD_TYPE,
        expected_manifest_sha256=details["manifest_sha256"],
        runtime_record=expected_runtime(details),
    )
    assert verified["signature_valid_under_supplied_key"] is True
    assert verified["production_decision"] == "HOLD"
    assert verified["promotion_authorized"] is False
    assert verified["externally_authorized_builder_root_proven"] is False


@pytest.mark.parametrize("modify,phrase", [
    ({"source": b"malicious replacement\n"}, "source"),
    ({"wrong_diff": True}, "diff ID"),
    ({"bad_manifest": True}, "missing"),
    ({"extra_blob": True}, "unreferenced"),
])
def test_destroyer_denies_tampered_or_foreign_oci_even_if_repacked(modify, phrase):
    with pytest.raises(remote.Denied, match=phrase):
        remote.attest(artifact(**modify))


def test_oci_archive_member_tamper_without_manifest_rehash_denied():
    original = artifact()
    with tarfile.open(fileobj=io.BytesIO(original), mode="r:") as t:
        content = [(m.name, t.extractfile(m).read()) for m in t if m.isfile()]
    corrupted = []
    for name, body in content:
        if name.startswith("blobs/sha256/") and body.startswith(b"\x1f\x8b"):
            body = b"BAD_GZIP_BYTES" + body[14:]
            corrupted.append((name, body))
            corrupted.extend((n, b) for n, b in content if n != name)
            break
    assert corrupted
    with pytest.raises(remote.Denied, match="SHA256"):
        remote.attest(make_tar(corrupted))


def test_duplicate_member_cannot_pass_remote_parser():
    original = artifact()
    with tarfile.open(fileobj=io.BytesIO(original), mode="r:") as t:
        content = [(m.name, t.extractfile(m).read()) for m in t if m.isfile()]
    with pytest.raises(remote.Denied, match="ambiguous"):
        remote.attest(make_tar(content + [content[0]]))


def test_oversized_attestation_input_denied_without_signing():
    with pytest.raises(remote.Denied, match="bounded"):
        remote.attest(b"Z" * (remote.MAX_ARCHIVE + 1))


def test_builder_payload_not_sent_remote_and_source_code_pinned(monkeypatch):
    original = artifact()
    assert lab.git_blob_sha(lab.REMOTE_SOURCE.read_bytes()) == lab.EXPECTED_REMOTE_SOURCE_BLOB
    observed = {}

    def fake_cmd(*args, input=None, timeout=35):
        observed["command"] = args
        observed["input"] = input
        observed["timeout"] = timeout
        return json.dumps(remote.attest(input), sort_keys=True)

    monkeypatch.setattr(lab, "_cmd", fake_cmd)
    env, public, metadata = lab._destroyer_sign(original)
    assert observed["input"] == original
    assert observed["input"] != lab._statement(metadata["manifest_sha256"])
    assert "StrictHostKeyChecking=yes" in observed["command"]
    assert any(str(a) == "destroyer" for a in observed["command"])
    assert metadata["source_git_blob"] == lab.EXPECTED_SOURCE_BLOB
    assert metadata["manifest_sha256"] == remote.inspect(original)["manifest_sha256"]
    assert len(public) == 32
    assert env["payloadType"] == verify_mod.PAYLOAD_TYPE


def test_remote_signer_source_unpinned_aborts_before_any_ssh(monkeypatch, tmp_path):
    other = tmp_path / "untrusted.py"
    other.write_text("print('anything')\n", encoding="utf-8")
    monkeypatch.setattr(lab, "REMOTE_SOURCE", other)
    monkeypatch.setattr(
        lab, "_cmd",
        lambda *_a, **_k: pytest.fail("untrusted signing source invoked SSH"),
    )
    with pytest.raises(lab.LabHold, match="not pinned"):
        lab._destroyer_sign(artifact())


def test_remote_rejects_attestation_output_if_source_digest_wrong(monkeypatch):
    data = artifact()
    doc = remote.attest(data)
    doc["remote_verified_source_git_blob"] = "c" * 40
    monkeypatch.setattr(lab, "_cmd", lambda *_a, **_k: json.dumps(doc))
    with pytest.raises(lab.LabHold, match="digest"):
        lab._destroyer_sign(data)


def test_in_process_remote_signer_never_publishes_private_key():
    signed = remote.attest(artifact())
    assert set(signed) == {
        "envelope",
        "ephemeral_public_key",
        "remote_observed_manifest_sha256",
        "remote_verified_source_git_blob",
    }
    assert not any("private" in key.lower() for key in signed)
    assert "private" not in json.dumps(signed)


def test_signed_subject_cannot_verify_against_substituted_running_image():
    raw = artifact()
    signed = remote.attest(raw)
    pub = base64.b64decode(signed["ephemeral_public_key"])
    observed = remote.inspect(raw)
    runtime = expected_runtime(observed)
    runtime["image"]["manifest_digest_values"] = ["sha256:" + "e" * 64]
    with pytest.raises(verify_mod.ProvenanceHold, match="manifest"):
        verify_mod.verify(
            envelope=signed["envelope"],
            public_key=pub,
            expected_key_sha256=hashlib.sha256(pub).hexdigest(),
            expected_service=lab.SERVICE,
            expected_git_sha=lab.EXPECTED_GIT_SHA,
            expected_builder_id=lab.BUILDER,
            expected_build_type=lab.BUILD_TYPE,
            expected_manifest_sha256=observed["manifest_sha256"],
            runtime_record=runtime,
        )
