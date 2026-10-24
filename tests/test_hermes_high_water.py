"""Offline synthetic high-water protocol verification.

Threaded UNIX socket is only a transport fixture; test signer and responder
are not independent production authority or durable custody.
"""
from __future__ import annotations

import copy
import json
import socket
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx import hermes_high_water as high
from assistx import hermes_corpus_checkpoint as cpmod
from assistx import knowledge_roots
from test_hermes_corpus_checkpoint import checkpoint
from test_hermes_signed_corpus_custody import signed


@pytest.fixture
def authority(checkpoint, tmp_path, monkeypatch):
    signer = Ed25519PrivateKey.generate()
    pub = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    original = checkpoint[0][2].read_bytes()
    witness = checkpoint[1].read_bytes()
    assert pub != witness != original and pub != original
    key = tmp_path / "authority.pub"
    key.write_bytes(pub)
    sock = tmp_path / "authority.sock"
    monkeypatch.setenv(high.SOCKET_ENV, str(sock))
    monkeypatch.setenv(high.KEY_ENV, str(key))
    monkeypatch.setenv(high.OWNER_ENV, "hermes-synthetic-owner")
    monkeypatch.setenv(high.MODE_ENV, "true")
    return checkpoint, signer, sock


def request_template():
    return {
        "schema": 1, "nonce": "a" * 64, "owner": "hermes-synthetic-owner",
        "generation": 2,
        "manifest_sha256": "b" * 64,
        "checkpoint_sha256": "c" * 64,
    }


def signed_response(req, signer, *, highwater=2, revoked=False, **mutations):
    p = {**req, "high_water_generation": highwater, "revoked": revoked}
    p.update(mutations)
    return high.canonical({
        **p, "signature": signer.sign(high.canonical(p)).hex()})


def invoke(checkpoint_fixture):
    signed, witness_pub, cp, signer, _, raw = checkpoint_fixture
    ro, rw, corpus_key, *_ = signed
    return cpmod.verify_checkpoint(
        read_root=str(ro), write_root=str(rw),
        corpus_public_key=corpus_key.read_bytes(),
        signed_manifest=raw, checkpoint_path=str(cp),
        witness_key_path=str(witness_pub))


def serve_once(sock_path, signer, *, highwater=2, revoked=False, mutation=None):
    """Serve one bounded synthetic nonce request in a distinct thread."""
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(sock_path))
    listener.listen(1)
    observed = {}
    def worker():
        with listener:
            listener.settimeout(4)
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(4)
                    incoming = b""
                    while b"\n" not in incoming and len(incoming) < 2048:
                        chunk = conn.recv(1024)
                        if not chunk:
                            break
                        incoming += chunk
                    received = json.loads(incoming)
                    observed["request"] = received
                    kw = mutation(received) if mutation else {}
                    raw = signed_response(received, signer,
                        highwater=highwater, revoked=revoked, **kw)
                    conn.sendall(raw + b"\n")
            except Exception as e:
                observed["error"] = str(e)
    t=threading.Thread(target=worker, daemon=True)
    t.start()
    return t, observed


@pytest.mark.parametrize("modification",[
    {"nonce":"0"*64},
    {"owner":"other-owner"},
    {"generation":1},
    {"manifest_sha256":"d"*64},
    {"checkpoint_sha256":"e"*64},
    {"high_water_generation":1},
    {"high_water_generation":3},
    {"revoked":True},
    {"revoked":"false"},
    {"signature":"0"*128},
    {"schema":2},
])
def test_reject_conflicting_replayed_or_revoked_response(modification):
    signer=Ed25519PrivateKey.generate()
    req=request_template()
    response=json.loads(signed_response(req,signer))
    response.update(modification)
    raw=high.canonical(response)
    pub=signer.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    with pytest.raises(ValueError,match="high-water"):
        high.verify_receipt(raw,public_key=pub,request=req)


def test_accept_exact_signature_bound_to_fresh_nonce_and_owner():
    signer=Ed25519PrivateKey.generate()
    pub=signer.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    req=request_template()
    high.verify_receipt(signed_response(req,signer),public_key=pub,request=req)


def test_reject_old_signed_response_even_when_valid_signature():
    signer=Ed25519PrivateKey.generate()
    pub=signer.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    previous=request_template()
    old=signed_response(previous,signer)
    current={**previous,"nonce":"f"*64}
    with pytest.raises(ValueError):
        high.verify_receipt(old,public_key=pub,request=current)


@pytest.mark.parametrize("garbage",[b"",b"{}",b"[1]",b"not-json",
                                   b"x"*(high.MAX_RECEIPT+1)])
def test_reject_broken_unbounded_or_unsigned_receipts(garbage):
    signer=Ed25519PrivateKey.generate()
    pub=signer.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    with pytest.raises((ValueError,UnicodeError)):
        high.verify_receipt(garbage,public_key=pub,request=request_template())


def test_local_synthetic_socket_proves_nonce_broker_roundtrip(authority):
    fixture, signer, sock = authority
    thread,seen=serve_once(sock,signer)
    assert invoke(fixture)==2
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert "error" not in seen
    assert seen["request"]["owner"]=="hermes-synthetic-owner"
    assert len(seen["request"]["nonce"])==64


@pytest.mark.parametrize("highwater,revoked",[(3,False),(1,False),(2,True)])
def test_remote_current_highwater_or_revocation_denies(authority,highwater,revoked):
    fixture,signer,sock=authority
    thread,seen=serve_once(sock,signer,highwater=highwater,revoked=revoked)
    with pytest.raises(ValueError,match="checkpoint"):
        invoke(fixture)
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_missing_witness_socket_fails_closed(authority):
    fixture,signer,sock=authority
    with pytest.raises(ValueError):
        invoke(fixture)


def test_trust_key_must_be_distinct_from_corpus_and_checkpoint(authority,monkeypatch):
    fixture,signer,sock=authority
    monkeypatch.setenv(high.KEY_ENV,str(fixture[1]))
    with pytest.raises(ValueError):
        invoke(fixture)


@pytest.mark.parametrize("owner",["","bad owner","../evil","!"*10,"a"*129])
def test_invalid_owner_rejected_without_network(authority,monkeypatch,owner):
    fixture,signer,sock=authority
    monkeypatch.setenv(high.OWNER_ENV,owner)
    with pytest.raises(ValueError):
        invoke(fixture)


def test_witness_socket_cannot_be_inside_mutable_corpus_or_scratch(authority,monkeypatch):
    fixture,signer,sock=authority
    ro,rw,*_=fixture[0]
    for bad in (ro/"witness.sock",rw/"witness.sock"):
        monkeypatch.setenv(high.SOCKET_ENV,str(bad))
        with pytest.raises(ValueError):
            invoke(fixture)


def test_witness_key_path_symlink_denied(authority,monkeypatch,tmp_path):
    fixture,signer,sock=authority
    key=Path(high.os.getenv(high.KEY_ENV))
    alias=tmp_path/"authority-key-alias"
    alias.symlink_to(key)
    monkeypatch.setenv(high.KEY_ENV,str(alias))
    with pytest.raises(ValueError):
        invoke(fixture)


def test_same_rollback_checkpoint_requires_independent_live_witness(authority):
    """Even if attacker restores both signed corpus and checkpoint,
    the uncompromised synthetic responder's newer highwater rejects old."""
    fixture,signer,sock=authority
    signed,pub,cp,_,issue_checkpoint,original=fixture
    ro,rw,corpus_key,corpus_signer,payload,issue=signed
    data=b"OLDER-SYNTHETIC-INPUT\n"
    prior=copy.deepcopy(payload)
    import hashlib
    prior["entries"][0]["size"]=len(data)
    prior["entries"][0]["sha256"]=hashlib.sha256(data).hexdigest()
    (ro/"decision.md").write_bytes(data)
    issue(prior)
    old_signed=(ro/".hermes-corpus-manifest.json").read_bytes()
    issue_checkpoint(generation=1,manifest_raw=old_signed)
    thread,seen=serve_once(sock,signer,highwater=2)
    with pytest.raises(ValueError):
        cpmod.verify_checkpoint(
            read_root=str(ro),write_root=str(rw),
            corpus_public_key=corpus_key.read_bytes(),
            signed_manifest=old_signed,
            checkpoint_path=str(cp),witness_key_path=str(pub))
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_disabled_mode_preserves_normal_offline_checkpoint(checkpoint,monkeypatch):
    monkeypatch.delenv(high.MODE_ENV,raising=False)
    assert invoke(checkpoint)==2


def test_external_mode_without_signed_checkpoint_is_denied(checkpoint,monkeypatch):
    ro,rw,key,*_=checkpoint[0]
    monkeypatch.setenv(high.MODE_ENV,"true")
    monkeypatch.delenv("HERMES_REQUIRE_CORPUS_CHECKPOINT",raising=False)
    monkeypatch.setenv(knowledge_roots.READ_ENV,str(ro))
    monkeypatch.setenv(knowledge_roots.WRITE_ENV,str(rw))
    with pytest.raises(ValueError,match="external high-water"):
        knowledge_roots.resolve_knowledge_roots("/unused",str(rw/"eval.json"))


def test_no_private_signer_or_saved_success_cache():
    source=Path(high.__file__).read_text()
    assert "Ed25519PrivateKey" not in source
    assert "secrets.token_hex(32)" in source
    assert "SOCK_STREAM" in source
    assert "settimeout" in source
    assert "_success_cache" not in source
    assert "lru_cache" not in source
