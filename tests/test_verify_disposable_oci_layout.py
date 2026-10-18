"""OCI layout content-addressed artifact negative tests; no Docker required."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

import importlib.util

SPEC = importlib.util.spec_from_file_location(
    "disposable_oci_layout_under_test",
    Path(__file__).resolve().parents[1] / "scripts/verify_disposable_oci_layout.py",
)
assert SPEC is not None and SPEC.loader is not None
oci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oci)

SOURCE = b"print('verified source fixture')\n"


def j(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def assemble(*, source=SOURCE, config_edit=None, manifest_edit=None,
             index_edit=None, extra=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        item = tarfile.TarInfo("source/verify.py")
        item.size = len(source)
        tar.addfile(item, io.BytesIO(source))
    plain = stream.getvalue()
    compressed = gzip.compress(plain, mtime=0)
    conf = {
        "rootfs": {"type": "layers", "diff_ids": ["sha256:" + digest(plain)]},
        "config": {"User": "65534:65534", "Entrypoint": ["/probe"]},
    }
    if config_edit:
        config_edit(conf)
    config_blob = j(conf)
    manifest = {
        "schemaVersion": 2,
        "mediaType": oci.OCI_MANIFEST,
        "config": {
            "mediaType": oci.OCI_CONFIG,
            "digest": "sha256:" + digest(config_blob),
            "size": len(config_blob),
        },
        "layers": [{
            "mediaType": oci.OCI_LAYER,
            "digest": "sha256:" + digest(compressed),
            "size": len(compressed),
        }],
    }
    if manifest_edit:
        manifest_edit(manifest)
    manifest_blob = j(manifest)
    index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{
            "mediaType": oci.OCI_MANIFEST,
            "digest": "sha256:" + digest(manifest_blob),
            "size": len(manifest_blob),
        }],
    }
    if index_edit:
        index_edit(index)
    content = {
        "oci-layout": j({"imageLayoutVersion": "1.0.0"}),
        "index.json": j(index),
        "blobs/sha256/" + digest(config_blob): config_blob,
        "blobs/sha256/" + digest(manifest_blob): manifest_blob,
        "blobs/sha256/" + digest(compressed): compressed,
    }
    if extra:
        content.update(extra)
    return make_tar(content), oci.git_blob_sha(source)


def make_tar(content):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, data in content.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    return stream.getvalue()


def test_valid_synthetic_oci_link_and_all_blobs():
    blob, expected = assemble()
    report = oci.inspect_oci(blob, expected_source_blob=expected)
    assert report["source_file_verified_inside_oci_layer"]
    assert report["layer_count"] == 1
    assert report["production_decision"] == "HOLD"
    assert report["promotion_authorized"] is False
    assert len(report["manifest_sha256"]) == 64


@pytest.mark.parametrize("edit", [
    lambda cfg: cfg["rootfs"]["diff_ids"].__setitem__(0, "sha256:" + "f"*64),
    lambda cfg: cfg["config"].update({"User": "root"}),
    lambda cfg: cfg["config"].update({"Entrypoint": ["/bin/sh"]}),
])
def test_unsafe_config_or_layer_hash_denied(edit):
    blob, expected = assemble(config_edit=edit)
    with pytest.raises(oci.ArtifactHold):
        oci.inspect_oci(blob, expected_source_blob=expected)


@pytest.mark.parametrize("edit", [
    lambda man: man["layers"][0].update({"digest": "sha256:" + "f"*64}),
    lambda man: man["config"].update({"digest": "sha256:" + "f"*64}),
    lambda man: man["layers"][0].update({"size": 9999999}),
    lambda man: man.update({"extra_untrusted": "fixture"}),
    lambda man: man["layers"][0].update({"urls": ["https://evil.test"]}),
])
def test_modified_manifest_or_external_blob_denied(edit):
    blob, expected = assemble(manifest_edit=edit)
    with pytest.raises(oci.ArtifactHold):
        oci.inspect_oci(blob, expected_source_blob=expected)


def test_wrong_expected_source_git_blob_denied():
    blob, _expected = assemble()
    with pytest.raises(oci.ArtifactHold, match="Git blob"):
        oci.inspect_oci(blob, expected_source_blob="f" * 40)


def test_source_tamper_with_all_updated_digests_not_the_expected_blob():
    blob, expected = assemble()
    tampered, _ = assemble(source=b"attacker replacement\n")
    assert oci.inspect_oci(blob, expected_source_blob=expected)
    with pytest.raises(oci.ArtifactHold, match="Git blob"):
        oci.inspect_oci(tampered, expected_source_blob=expected)


@pytest.mark.parametrize("path", [
    "../secret",
    "/etc/passwd",
    "blobs/sha256/not-a-hash",
])
def test_unexpected_tar_path_denied(path):
    blob, expected = assemble(extra={path: b"unexpected"})
    with pytest.raises(oci.ArtifactHold):
        oci.inspect_oci(blob, expected_source_blob=expected)


def test_unreferenced_extra_blob_rejected():
    blob, expected = assemble(extra={"blobs/sha256/" + "e"*64: b"orphan"})
    with pytest.raises(oci.ArtifactHold, match="orphan"):
        oci.inspect_oci(blob, expected_source_blob=expected)


def test_duplicate_tar_member_rejected():
    blob, expected = assemble()
    entries = oci._tar_entries(blob)
    duplicate = io.BytesIO()
    with tarfile.open(fileobj=duplicate, mode="w") as tar:
        for key, data in list(entries.items()) + [("index.json", b"{}")]:
            member = tarfile.TarInfo(key)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    with pytest.raises(oci.ArtifactHold, match="duplicate"):
        oci.inspect_oci(duplicate.getvalue(), expected_source_blob=expected)


@pytest.mark.parametrize("mutation", [
    lambda index: index["manifests"].append(index["manifests"][0]),
    lambda index: index.update({"schemaVersion": 1}),
    lambda index: index["manifests"][0].update({"mediaType": "wrong"}),
])
def test_multi_manifest_or_index_malformed_denied(mutation):
    blob, expected = assemble(index_edit=mutation)
    with pytest.raises(oci.ArtifactHold):
        oci.inspect_oci(blob, expected_source_blob=expected)


def test_oci_file_requires_local_regular_nonsymlink(tmp_path: Path):
    raw, expected = assemble()
    regular = tmp_path / "test.oci"
    regular.write_bytes(raw)
    assert oci.inspect_oci_file(str(regular), expected_source_blob=expected)
    linked = tmp_path / "alias.oci"
    linked.symlink_to(regular)
    with pytest.raises(oci.ArtifactHold, match="unavailable"):
        oci.inspect_oci_file(str(linked), expected_source_blob=expected)


def test_oversized_compressed_layer_rejected_before_unbounded_expansion():
    # Hostile gzip payload grows >16MB from a few kilobytes.
    raw = gzip.compress(b"x" * (oci.MAX_BLOB + 2), compresslevel=9)
    with pytest.raises(oci.ArtifactHold, match="too large"):
        oci._find_source_in_layers([raw], oci.git_blob_sha(SOURCE))
