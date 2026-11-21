"""Bounded image-pinned checkpoint tests; all keys/data synthetic and disposable."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx import hermes_corpus_checkpoint as witness
from assistx import hermes_corpus_custody as custody
from assistx import knowledge_roots as roots
from test_hermes_signed_corpus_custody import signed


@pytest.fixture
def checkpoint(signed, tmp_path):
    ro, rw, corpus_key, corpus_signer, payload, issue = signed
    approved = (ro / custody.MANIFEST_NAME).read_bytes()
    loc = tmp_path / "immutable-witness"
    loc.mkdir()
    witness_signer = Ed25519PrivateKey.generate()
    witness_pub = loc / "witness.pub"
    witness_pub.write_bytes(witness_signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    assert witness_pub.read_bytes() != corpus_key.read_bytes()
    anchor = loc / "checkpoint.json"

    def issue_checkpoint(generation=2, manifest_raw=approved,
                         witness_private=witness_signer, **overrides):
        body = {
            "schema": 1,
            "generation": generation,
            "root": str(ro),
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "corpus_key_sha256": hashlib.sha256(corpus_key.read_bytes()).hexdigest(),
        }
        body.update(overrides)
        doc = {**body,
               "signature": witness_private.sign(custody.canonical(body)).hex()}
        anchor.write_text(json.dumps(doc, sort_keys=True))
        return doc
    issue_checkpoint()
    return signed, witness_pub, anchor, witness_signer, issue_checkpoint, approved


def verify(bundle):
    signed, witness_key, checkpoint_file, _, _, raw = bundle
    ro, rw, key, *_ = signed
    return witness.verify_checkpoint(
        read_root=str(ro), write_root=str(rw),
        corpus_public_key=key.read_bytes(), signed_manifest=raw,
        checkpoint_path=str(checkpoint_file), witness_key_path=str(witness_key))


def set_opts(monkeypatch, bundle):
    signed, pub, check, *_ = bundle
    monkeypatch.setenv("HERMES_REQUIRE_CORPUS_CHECKPOINT", "true")
    monkeypatch.setenv("HERMES_CORPUS_CHECKPOINT_PATH", str(check))
    monkeypatch.setenv("HERMES_CORPUS_WITNESS_KEY_PATH", str(pub))


def test_exact_distinct_signer_checkpoint_validates(checkpoint, monkeypatch):
    assert verify(checkpoint) == 2
    set_opts(monkeypatch, checkpoint)
    assert len(custody.verify_signed_corpus(
        str(checkpoint[0][0]), str(checkpoint[0][1]),
        str(checkpoint[0][2]))) == 2
    assert len(custody.verified_context_texts(
        str(checkpoint[0][0]), str(checkpoint[0][1]),
        str(checkpoint[0][2]))) == 2


def test_no_checkpoint_flag_preserves_signed_v1_behavior(checkpoint, monkeypatch):
    monkeypatch.delenv("HERMES_REQUIRE_CORPUS_CHECKPOINT", raising=False)
    ro,rw,key,*_ = checkpoint[0]
    assert len(custody.verify_signed_corpus(str(ro),str(rw),str(key))) == 2


@pytest.mark.parametrize("field,value", [
    ("generation",0),("generation",-1),("generation",True),
    ("generation",False),("generation",1.2),
    ("generation",witness.MAX_GENERATION+1),
    ("root","/different/source"),
    ("schema",0),("schema",True),
    ("manifest_sha256","f"*64),
    ("corpus_key_sha256","f"*64),
])
def test_signed_wrong_checkpoint_payload_rejected(checkpoint, field, value):
    checkpoint[4](**{field:value})
    with pytest.raises(ValueError):
        verify(checkpoint)


def test_unsigned_checkpoint_mutation_rejected(checkpoint):
    anchor = checkpoint[2]
    data = json.loads(anchor.read_text())
    data["generation"] = 999
    anchor.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        verify(checkpoint)


def test_wrong_witness_verification_key_rejected(checkpoint):
    checkpoint[1].write_bytes(Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    with pytest.raises(ValueError):
        verify(checkpoint)


def test_corpus_signer_key_cannot_also_be_witness(checkpoint):
    checkpoint[1].write_bytes(checkpoint[0][2].read_bytes())
    with pytest.raises(ValueError, match="checkpoint"):
        verify(checkpoint)


@pytest.mark.parametrize("part", ["checkpoint", "witness"])
def test_missing_or_symlink_witness_object_denied(checkpoint,tmp_path,part):
    target = checkpoint[2] if part=="checkpoint" else checkpoint[1]
    other = tmp_path / ("redirect-" + part)
    other.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(other)
    with pytest.raises(ValueError):
        verify(checkpoint)


def test_checkpoint_and_key_cannot_reside_under_mutable_ro_rw(checkpoint):
    signed, key, cp, signer, issue, raw = checkpoint
    ro, rw, *_ = signed
    for location in (ro, rw):
        local = location / "anchor.json"
        local.write_bytes(cp.read_bytes())
        with pytest.raises(ValueError, match="overlaps"):
            witness.verify_checkpoint(read_root=str(ro),write_root=str(rw),
                corpus_public_key=signed[2].read_bytes(),signed_manifest=raw,
                checkpoint_path=str(local),witness_key_path=str(key))


@pytest.mark.parametrize("bad", ["", "/", "relative/key.pub", "/foo/../foo.pub"])
def test_invalid_checkpoint_path_denied(checkpoint,bad):
    signed, key, cp, *_ = checkpoint
    ro,rw,*_ = signed
    with pytest.raises(ValueError):
        witness.verify_checkpoint(read_root=str(ro),write_root=str(rw),
            corpus_public_key=signed[2].read_bytes(),
            signed_manifest=checkpoint[-1],checkpoint_path=bad,
            witness_key_path=str(key))


def test_original_signed_manifest_replay_denied_by_newer_witness(checkpoint,monkeypatch):
    signed, witness_key, cp, signer, issue_anchor, approved = checkpoint
    ro,rw,corpus_key,corpus_signer,payload,issue = signed
    older = b"synthetic older snapshot\n"
    prior = copy.deepcopy(payload)
    prior["entries"][0]["size"] = len(older)
    prior["entries"][0]["sha256"] = hashlib.sha256(older).hexdigest()
    (ro/"decision.md").write_bytes(older)
    old_doc = issue(prior)  # signed by the SAME approved corpus signer
    old_raw = (ro/custody.MANIFEST_NAME).read_bytes()
    assert old_raw != approved
    set_opts(monkeypatch,checkpoint)
    with pytest.raises(ValueError,match="checkpoint"):
        custody.verify_signed_corpus(str(ro),str(rw),str(corpus_key))


def test_rollback_of_manifest_and_checkpoint_remains_possible(checkpoint,monkeypatch):
    """This is the explicit security LIMIT until independent high-water custody."""
    signed, pub, cp, signer, new_checkpoint, approved = checkpoint
    ro,rw,key,corpus_signer,payload,issue = signed
    old = b"synthetic once-approved previous corpus\n"
    old_payload = copy.deepcopy(payload)
    old_payload["entries"][0]["size"] = len(old)
    old_payload["entries"][0]["sha256"] = hashlib.sha256(old).hexdigest()
    (ro/"decision.md").write_bytes(old)
    issue(old_payload)
    old_signed_raw = (ro/custody.MANIFEST_NAME).read_bytes()
    # Same operator can issue a valid OLD witness and restore both objects.
    new_checkpoint(generation=1, manifest_raw=old_signed_raw)
    set_opts(monkeypatch, checkpoint)
    assert len(custody.verify_signed_corpus(str(ro),str(rw),str(key))) == 2
    # This accepted co-rollback MUST NOT be represented as tamper resistance.


@pytest.mark.parametrize("mode", ["off", "TRUE", "1"])
def test_unsupported_checkpoint_env_mode_denied(checkpoint,monkeypatch,mode):
    ro,rw,key,*_ = checkpoint[0]
    monkeypatch.setenv("HERMES_REQUIRE_CORPUS_CHECKPOINT",mode)
    with pytest.raises(ValueError,match="checkpoint"):
        custody.verify_signed_corpus(str(ro),str(rw),str(key))


def test_checkpoint_requires_signed_corpus_mode(monkeypatch,checkpoint):
    ro,rw,key,*_ = checkpoint[0]
    monkeypatch.setenv("HERMES_REQUIRE_CORPUS_CHECKPOINT","true")
    monkeypatch.delenv("HERMES_REQUIRE_SIGNED_CORPUS",raising=False)
    monkeypatch.setenv(roots.READ_ENV,str(ro))
    monkeypatch.setenv(roots.WRITE_ENV,str(rw))
    with pytest.raises(ValueError,match="checkpoint requires signed"):
        roots.resolve_knowledge_roots("/legacy",str(rw/"eval.json"))


def test_checkpoint_rejected_with_unset_split_roots(monkeypatch):
    monkeypatch.delenv(roots.READ_ENV,raising=False)
    monkeypatch.delenv(roots.WRITE_ENV,raising=False)
    monkeypatch.setenv("HERMES_REQUIRE_CORPUS_CHECKPOINT","true")
    with pytest.raises(ValueError,match="checkpoint"):
        roots.resolve_knowledge_roots("/legacy")


def test_checkpoint_has_no_mutating_side_effect_or_signing_private_key():
    source=Path(witness.__file__).read_text()
    assert "Ed25519PrivateKey" not in source
    assert "Ed25519PublicKey" in source
    assert "verify(" in source
    assert "os.replace" not in source
    assert "os.remove" not in source
    assert "open(\"/proc" not in source
