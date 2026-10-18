"""Negative controls for offline DSSE Ed25519 source/image provenance gate.

All signatures in these tests use EPHEMERAL IN-PROCESS TEST KEYS and assert
that syntactically valid signed evidence NEVER authorizes production use.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_signed_image_provenance.py"
SPEC = importlib.util.spec_from_file_location("signed_provenance_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
SHA = "87a00b15cb641cd9177b5e950113593e9dc95bce"
MANIFEST = "a" * 64
BUILDER = "https://example.test/synthetic-disposable-builder"
BUILD_TYPE = "https://example.test/offline-disposable-build-type"
SERVICE = "assistx-api"


def synthetic():
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    statement = {
        "_type": verifier.STATEMENT_TYPE,
        "subject": [{"name": "service:" + SERVICE, "digest": {"sha256": MANIFEST}}],
        "predicateType": verifier.PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": BUILD_TYPE,
                "externalParameters": {
                    "sourceRepository": verifier.REPOS[SERVICE],
                    "sourceRevision": SHA,
                    "service": SERVICE,
                },
                "resolvedDependencies": [{
                    "uri": "git+" + verifier.REPOS[SERVICE],
                    "digest": {"gitCommit": SHA},
                }],
            },
            "runDetails": {"builder": {"id": BUILDER}},
        },
    }
    runtime = {
        "name": SERVICE,
        "container_id": "b" * 64,
        "state": "running",
        "image": {
            "local_image_id": "sha256:" + "c" * 64,
            "manifest_digest_values": ["sha256:" + MANIFEST],
        },
    }
    return key, pub, statement, runtime


def envelope_for(key, pub, statement, *, payload_override=None, payload_type=None):
    payload = (
        payload_override
        if payload_override is not None
        else json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    )
    kind = payload_type or verifier.PAYLOAD_TYPE
    return {
        "payloadType": kind,
        "payload": base64.b64encode(payload).decode(),
        "signatures": [{
            "keyid": hashlib.sha256(pub).hexdigest(),
            "sig": base64.b64encode(key.sign(verifier.pae(kind, payload))).decode(),
        }],
    }


def check(statement=None, runtime=None, *, key=None, pub=None, env=None, **kw):
    key0, pub0, statement0, runtime0 = synthetic()
    key = key or key0
    pub = pub or pub0
    statement = statement or statement0
    runtime = runtime or runtime0
    env = env or envelope_for(key, pub, statement)
    args = {
        "envelope": env,
        "public_key": pub,
        "expected_key_sha256": hashlib.sha256(pub).hexdigest(),
        "expected_service": SERVICE,
        "expected_git_sha": SHA,
        "expected_builder_id": BUILDER,
        "expected_build_type": BUILD_TYPE,
        "expected_manifest_sha256": MANIFEST,
        "runtime_record": runtime,
    }
    args.update(kw)
    return verifier.verify(**args)


def test_valid_ephemeral_dsse_signature_does_not_authorize_release():
    result = check()
    assert result["signature_valid_under_supplied_key"]
    assert result["signed_subject_matches_observed_manifest"]
    assert result["production_decision"] == "HOLD"
    assert result["promotion_authorized"] is False
    assert result["externally_authorized_builder_root_proven"] is False
    assert result["signed_source_to_runtime_release_authority"] is False


@pytest.mark.parametrize("change,pattern", [
    (("signed", "externalParameters", "sourceRevision", "d" * 40), "parameters"),
    (("signed", "externalParameters", "sourceRepository", "https://evil.test/repo"), "parameters"),
    (("signed", "externalParameters", "service", "assistx-worker"), "parameters"),
    (("signed", "resolvedDependencies", "digest", "d" * 40), "dependency"),
    (("signed", "subject", "digest", "d" * 64), "subject"),
    (("signed", "subject", "name", "service:git-router-1"), "subject"),
    (("signed", "builder", "id", "https://example.test/evil-builder"), "builder"),
    (("signed", "buildType", None, "https://example.test/evil-build"), "build type"),
])
def test_re_signed_forged_claims_denied_even_when_signature_valid(change, pattern):
    key, pub, statement, runtime = synthetic()
    s = copy.deepcopy(statement)
    _kind, what, prop, value = change
    if what == "externalParameters":
        s["predicate"]["buildDefinition"][what][prop] = value
    elif what == "resolvedDependencies":
        s["predicate"]["buildDefinition"][what][0]["digest"][prop.replace("digest", "gitCommit")] = value
    elif what == "subject":
        if prop == "name":
            s["subject"][0][prop] = value
        else:
            s["subject"][0]["digest"]["sha256"] = value
    elif what == "builder":
        s["predicate"]["runDetails"]["builder"][prop] = value
    else:
        s["predicate"]["buildDefinition"]["buildType"] = value
    with pytest.raises(verifier.ProvenanceHold, match=pattern):
        check(s, runtime, key=key, pub=pub)


def test_payload_tampering_without_re_signing_rejected():
    key, pub, statement, runtime = synthetic()
    env = envelope_for(key, pub, statement)
    payload = json.loads(base64.b64decode(env["payload"]))
    payload["predicate"]["runDetails"]["builder"]["id"] = "https://example.test/attacker"
    env["payload"] = base64.b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(verifier.ProvenanceHold, match="signature"):
        check(statement, runtime, key=key, pub=pub, env=env)


def test_dsse_domain_separates_payload_type():
    key, pub, statement, runtime = synthetic()
    env = envelope_for(key, pub, statement)
    env["payloadType"] = "application/json"
    with pytest.raises(verifier.ProvenanceHold, match="payload type"):
        check(statement, runtime, key=key, pub=pub, env=env)


def test_wrong_pinned_key_rejected_even_if_attacker_controls_envelope():
    key, pub, statement, runtime = synthetic()
    env = envelope_for(key, pub, statement)
    other = Ed25519PrivateKey.generate()
    other_pub = other.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    with pytest.raises(verifier.ProvenanceHold, match="pinned fingerprint"):
        check(statement, runtime, key=key, pub=other_pub, env=env,
              expected_key_sha256=hashlib.sha256(pub).hexdigest())


@pytest.mark.parametrize("mutation", [
    lambda e: e["signatures"][0].update({"keyid": "e" * 64}),
    lambda e: e.update({"extra": 1}),
    lambda e: e.update({"signatures": []}),
    lambda e: e["signatures"].append(copy.deepcopy(e["signatures"][0])),
    lambda e: e["signatures"][0].update({"sig": "not_base64"}),
    lambda e: e.update({"payload": "!badpayload"}),
    lambda e: e["signatures"][0].update({"extra": "unsigned"}),
])
def test_wrong_envelope_or_signature_shape_fail_closed(mutation):
    key, pub, statement, runtime = synthetic()
    env = envelope_for(key, pub, statement)
    mutation(env)
    with pytest.raises(verifier.ProvenanceHold):
        check(statement, runtime, key=key, pub=pub, env=env)


@pytest.mark.parametrize("replacement", [
    lambda r: r["image"].update({"manifest_digest_values": []}),
    lambda r: r["image"].update({"manifest_digest_values": ["sha256:" + "e"*64]}),
    lambda r: r.update({"state": "exited"}),
    lambda r: r.update({"name": "assistx-worker"}),
    lambda r: r.update({"container_id": "invalid"}),
    lambda r: r["image"].update({"local_image_id": "latest"}),
])
def test_runtime_not_exact_signed_manifest_or_live_identity_denied(replacement):
    key, pub, statement, runtime = synthetic()
    replacement(runtime)
    with pytest.raises(verifier.ProvenanceHold):
        check(statement, runtime, key=key, pub=pub)


def test_signed_duplicate_json_object_key_is_rejected():
    key, pub, _s, runtime = synthetic()
    payload = b'{"_type":"x","_type":"y"}'
    env = envelope_for(key, pub, {}, payload_override=payload)
    with pytest.raises(verifier.ProvenanceHold, match="malformed signed JSON"):
        check({}, runtime, key=key, pub=pub, env=env)


def test_nonstandard_signed_number_rejected():
    with pytest.raises(verifier.ProvenanceHold, match="malformed signed JSON"):
        verifier._json_strict(b'{"value":NaN}')


def test_synthetic_source_repository_cannot_be_arbitrary(monkeypatch):
    key, pub, statement, runtime = synthetic()
    monkeypatch.setitem(verifier.REPOS, "evil-service", "https://evil.test")
    try:
        with pytest.raises(verifier.ProvenanceHold):
            check(statement, runtime, key=key, pub=pub, expected_service="missing-service")
    finally:
        verifier.REPOS.pop("evil-service", None)


@pytest.mark.parametrize("text", [
    "not_hex",
    "a" * 39,
    "g" * 40,
])
def test_expected_commit_is_canonical_hex(text):
    with pytest.raises(verifier.ProvenanceHold, match="commit"):
        check(expected_git_sha=text)


def test_real_runtime_probe_failure_is_hold(monkeypatch, capsys):
    monkeypatch.setattr(verifier, "_runtime_record", lambda _s: (
        (_ for _ in ()).throw(verifier.ProvenanceHold("no permitted runtime scan"))
    ))
    assert verifier.main([
        "--service", SERVICE, "--commit", SHA,
        "--builder-id", BUILDER, "--build-type", BUILD_TYPE,
        "--manifest-sha256", MANIFEST, "--key-sha256", "a" * 64,
        "--envelope", "/tmp/nonexistent.dsse", "--public-key", "/tmp/nonexistent.pub",
    ]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["production_decision"] == "HOLD"
    assert not result["promotion_authorized"]


def test_symlink_public_root_refused(tmp_path):
    real = tmp_path / "real.pub"
    real.write_bytes(b"a" * 32)
    fake = tmp_path / "alias.pub"
    fake.symlink_to(real)
    with pytest.raises(verifier.ProvenanceHold, match="unavailable"):
        verifier._read_public_file(str(fake), max_length=32)


def test_oversized_envelope_or_short_public_key_denied(tmp_path):
    real = tmp_path / "big.dsse"
    real.write_bytes(b"a" * 256_001)
    with pytest.raises(verifier.ProvenanceHold, match="too large"):
        verifier._read_public_file(str(real), max_length=256_000)
    key = tmp_path / "key.pub"
    key.write_bytes(b"xyz")
    with pytest.raises(verifier.ProvenanceHold):
        check(public_key=b"xyz")


def test_slas_stated_builder_is_not_independently_trusted():
    result = check()
    assert "independently" in result["limitations"] or "independent" in result["limitations"]
    assert result["externally_authorized_builder_root_proven"] is False


def test_cli_full_offline_signature_path_remains_hold(tmp_path, monkeypatch, capsys):
    key, pub, statement, runtime = synthetic()
    envelope = envelope_for(key, pub, statement)
    env_file = tmp_path / "ephemeral-test.dsse"
    pub_file = tmp_path / "ephemeral-test.pub"
    env_file.write_text(json.dumps(envelope), encoding="utf-8")
    pub_file.write_bytes(pub)
    monkeypatch.setattr(verifier, "_runtime_record", lambda _service: runtime)
    assert verifier.main([
        "--envelope", str(env_file),
        "--public-key", str(pub_file),
        "--key-sha256", hashlib.sha256(pub).hexdigest(),
        "--service", SERVICE, "--commit", SHA,
        "--builder-id", BUILDER, "--build-type", BUILD_TYPE,
        "--manifest-sha256", MANIFEST,
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["signature_valid_under_supplied_key"] is True
    assert result["signed_subject_matches_observed_manifest"] is True
    assert result["promotion_authorized"] is False
    assert result["production_decision"] == "HOLD"
    assert result["externally_authorized_builder_root_proven"] is False


def test_cli_wrong_key_even_with_matching_payload_is_denied(tmp_path, monkeypatch, capsys):
    key, pub, statement, runtime = synthetic()
    envelope = envelope_for(key, pub, statement)
    env_file = tmp_path / "ephemeral-test.dsse"
    pub_file = tmp_path / "wrong-test.pub"
    wrong = Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    env_file.write_text(json.dumps(envelope), encoding="utf-8")
    pub_file.write_bytes(wrong)
    monkeypatch.setattr(verifier, "_runtime_record", lambda _service: runtime)
    assert verifier.main([
        "--envelope", str(env_file),
        "--public-key", str(pub_file),
        "--key-sha256", hashlib.sha256(pub).hexdigest(),
        "--service", SERVICE, "--commit", SHA,
        "--builder-id", BUILDER, "--build-type", BUILD_TYPE,
        "--manifest-sha256", MANIFEST,
    ]) == 3
    result = json.loads(capsys.readouterr().out)
    assert not result["promotion_authorized"]
    assert result["production_decision"] == "HOLD"
