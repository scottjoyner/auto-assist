"""Offline Ed25519 corpus startup integrity: ephemeral signing keys only."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx import hermes_corpus_custody as c
from assistx import knowledge_roots as roots


@pytest.fixture
def signed(tmp_path):
    ro = tmp_path / "source"
    rw = tmp_path / "scratch"
    immutable = tmp_path / "immutable-build"
    for p in (ro, rw, immutable):
        p.mkdir()
    (ro / "decision.md").write_bytes(b"synthetic decision only\n")
    (ro / "nested").mkdir()
    (ro / "nested" / "context.txt").write_bytes(b"synthetic nested only\n")
    signer = Ed25519PrivateKey.generate()
    trusted = immutable / "trust.pub"
    trusted.write_bytes(signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    entries = []
    for rel in ("decision.md", "nested/context.txt"):
        raw = (ro / rel).read_bytes()
        entries.append({"path": rel, "size": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest()})
    payload = {"schema": 1, "root": str(ro), "entries": entries}
    manifest = ro / c.MANIFEST_NAME

    def issue(payload=payload, signer=signer):
        data = {**payload, "signature": signer.sign(c.canonical(payload)).hex()}
        manifest.write_text(json.dumps(data, sort_keys=True))
        return data

    issue()
    return ro, rw, trusted, signer, payload, issue


def verify(bundle):
    ro, rw, key, *_ = bundle
    return c.verify_signed_corpus(str(ro), str(rw), str(key))


def test_accept_signed_exact_bounded_two_file_subset(signed):
    assert verify(signed) == {
        x["path"]: x["sha256"] for x in signed[4]["entries"]}


def test_valid_signature_does_not_authorize_deployment(signed):
    verify(signed)
    assert not hasattr(c, "authorize_production")
    assert c.MAX_ENTRIES == 1024


@pytest.mark.parametrize("operation", [
    "tamper", "truncate", "delete", "append", "extra",
    "nested_extra", "symlink_file", "symlink_root_entry",
])
def test_modified_corpus_content_or_extra_files_are_denied(signed, tmp_path, operation):
    ro, rw, key, signer, payload, issue = signed
    target = ro / "decision.md"
    if operation == "tamper":
        target.write_bytes(b"SYNTHETIC INVALID INPUT\n")
    elif operation == "truncate":
        target.write_bytes(b"")
    elif operation == "delete":
        target.unlink()
    elif operation == "append":
        with target.open("ab") as f: f.write(b"added")
    elif operation == "extra":
        (ro / "unsigned.md").write_text("unknown")
    elif operation == "nested_extra":
        (ro / "nested" / "unsigned.txt").write_text("unknown")
    elif operation == "symlink_file":
        target.unlink()
        target.symlink_to(tmp_path / "untrusted")
    elif operation == "symlink_root_entry":
        (ro / "unexpected").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        verify(signed)


def test_unknown_signing_key_denied(signed):
    ro, rw, key, *_ = signed
    other = Ed25519PrivateKey.generate()
    key.write_bytes(other.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    with pytest.raises(ValueError, match="untrusted"):
        verify(signed)


@pytest.mark.parametrize("mutation", ["digest", "path", "size", "root", "schema",
                                       "signature", "entries", "extra_field"])
def test_manifest_mutation_with_same_signature_denied(signed, mutation):
    ro, rw, key, signer, payload, issue = signed
    manifest = ro / c.MANIFEST_NAME
    doc = json.loads(manifest.read_text())
    if mutation == "digest":
        doc["entries"][0]["sha256"] = "a" * 64
    elif mutation == "path":
        doc["entries"][0]["path"] = "../secret.md"
    elif mutation == "size":
        doc["entries"][0]["size"] += 1
    elif mutation == "root":
        doc["root"] = "/unapproved"
    elif mutation == "schema":
        doc["schema"] = 2
    elif mutation == "signature":
        doc["signature"] = "00" * 64
    elif mutation == "entries":
        doc["entries"] = []
    else:
        doc["unauthorized"] = True
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        verify(signed)


def test_duplicate_signed_path_denied_even_with_new_signature(signed):
    ro, rw, key, signer, payload, issue = signed
    modified = copy.deepcopy(payload)
    modified["entries"].append(modified["entries"][0])
    issue(modified)
    with pytest.raises(ValueError, match="duplicate"):
        verify(signed)


@pytest.mark.parametrize("path", [
    "../traversal.md", "/absolute.md", "nested/../../escape.md",
    "folder/another/deeper.md", ".hidden.md", "name\\.md",
    "file.py", "a//b.md", "a/./c.md", "",
])
def test_signed_paths_cannot_escape_or_expand_scope(signed, path):
    ro, rw, key, signer, payload, issue = signed
    modified = copy.deepcopy(payload)
    modified["entries"][0]["path"] = path
    issue(modified)
    with pytest.raises(ValueError):
        verify(signed)


def test_reject_nested_subdirectory_even_if_not_listed(signed):
    ro, rw, *_ = signed
    (ro / "nested" / "deeper").mkdir()
    with pytest.raises(ValueError, match="nested"):
        verify(signed)


@pytest.mark.parametrize("bad", ["", "/", "/root", "relative"])
def test_untrusted_verifier_location_denied(signed, bad):
    ro, rw, *_ = signed
    with pytest.raises(ValueError):
        c.verify_signed_corpus(str(ro), str(rw), bad)


def test_public_key_from_mutable_source_or_scratch_is_denied(signed):
    ro, rw, key, *_ = signed
    for base in (ro, rw):
        relocated = base / "key.pub"
        relocated.write_bytes(key.read_bytes())
        with pytest.raises(ValueError, match="outside"):
            c.verify_signed_corpus(str(ro), str(rw), str(relocated))


def test_reject_bad_signature_lengths_and_invalid_json(signed):
    ro, rw, *_ = signed
    manifest = ro / c.MANIFEST_NAME
    manifest.write_text('{"schema":1,')
    with pytest.raises(ValueError):
        verify(signed)
    manifest.write_text("x" * (c.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(ValueError):
        verify(signed)


def test_reject_signed_mode_without_mount_guard(monkeypatch, signed):
    ro, rw, key, *_ = signed
    with monkeypatch.context() as mp:
        mp.setenv(roots.READ_ENV, str(ro))
        mp.setenv(roots.WRITE_ENV, str(rw))
        mp.setenv("HERMES_REQUIRE_SIGNED_CORPUS", "true")
        mp.setenv("HERMES_CORPUS_TRUST_KEY_PATH", str(key))
        with pytest.raises(ValueError, match="kernel mount isolation"):
            roots.resolve_knowledge_roots("/unused", str(rw / "eval.json"))


def test_reject_signed_mode_without_split_roots(monkeypatch):
    with monkeypatch.context() as mp:
        mp.delenv(roots.READ_ENV, raising=False)
        mp.delenv(roots.WRITE_ENV, raising=False)
        mp.setenv("HERMES_REQUIRE_SIGNED_CORPUS", "true")
        with pytest.raises(ValueError, match="split roots"):
            roots.resolve_knowledge_roots("/unused")


def test_signed_corpus_does_not_read_files_over_bound(signed):
    ro, rw, key, signer, payload, issue = signed
    (ro / "decision.md").write_bytes(b"x" * (c.MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError):
        verify(signed)


def test_no_private_signing_keys_are_part_of_runtime_source():
    source = Path(c.__file__).read_text()
    assert "Ed25519PrivateKey" not in source
    assert "Ed25519PublicKey" in source
    assert "verify(" in source
    assert "sign(" not in source
