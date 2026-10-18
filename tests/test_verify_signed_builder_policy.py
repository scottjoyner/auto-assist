"""Adversarial unsigned trust-policy delegation controls; no production keys."""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DIR = str(Path(__file__).resolve().parents[1] / "scripts")
if DIR not in sys.path:
    sys.path.insert(0, DIR)
policy = importlib.import_module("verify_signed_builder_policy")
slsa = importlib.import_module("verify_signed_image_provenance")

SHA = "109058ab6c2d8e7b71bfb7146edc5acbe26670db"
MANIFEST = "a" * 64
ROOT_EPOCH = 45
CHECK_TIME = 1792300000
BUILDER = "https://example.test/research-trust-bundle-builder"
BUILD_TYPE = "https://example.test/disposable-nonproduction"
SERVICE = "assistx-api"


def public(key):
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def signed(key, pub, body, kind):
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return {
        "payloadType": kind,
        "payload": base64.b64encode(raw).decode(),
        "signatures": [{
            "keyid": hashlib.sha256(pub).hexdigest(),
            "sig": base64.b64encode(key.sign(slsa.pae(kind, raw))).decode(),
        }],
    }


def fixtures():
    root = Ed25519PrivateKey.generate()
    builder = Ed25519PrivateKey.generate()
    root_pub = public(root)
    builder_pub = public(builder)
    policy_claim = {
        "schema": policy.SCHEMA,
        "epoch": ROOT_EPOCH,
        "not_before": CHECK_TIME - 60,
        "expires_at": CHECK_TIME + 3600,
        "service": SERVICE,
        "source_commit": SHA,
        "manifest_sha256": MANIFEST,
        "builder_id": BUILDER,
        "build_type": BUILD_TYPE,
        "builder_key_sha256": hashlib.sha256(builder_pub).hexdigest(),
    }
    statement = {
        "_type": slsa.STATEMENT_TYPE,
        "subject": [{
            "name": "service:" + SERVICE,
            "digest": {"sha256": MANIFEST},
        }],
        "predicateType": slsa.PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": BUILD_TYPE,
                "externalParameters": {
                    "sourceRepository": slsa.REPOS[SERVICE],
                    "sourceRevision": SHA,
                    "service": SERVICE,
                },
                "resolvedDependencies": [{
                    "uri": "git+" + slsa.REPOS[SERVICE],
                    "digest": {"gitCommit": SHA},
                }],
            },
            "runDetails": {"builder": {"id": BUILDER}},
        },
    }
    runtime = {
        "name": SERVICE, "state": "running", "container_id": "b"*64,
        "image": {
            "local_image_id": "sha256:" + "c"*64,
            "manifest_digest_values": ["sha256:" + MANIFEST],
        },
    }
    return {
        "root": root, "builder": builder,
        "root_pub": root_pub, "builder_pub": builder_pub,
        "claims": policy_claim, "statement": statement,
        "runtime": runtime,
    }


def check(*, change_policy=None, change_statement=None, root=None,
          expected=None, revoked=frozenset(), clock=CHECK_TIME,
          generation=ROOT_EPOCH, same_key=False, policy_mutation=None):
    env = fixtures()
    root = root or env["root"]
    root_pub = public(root)
    builder = root if same_key else env["builder"]
    builder_pub = public(builder)
    claims = env["claims"].copy()
    if change_policy:
        claims.update(change_policy)
    if policy_mutation:
        policy_mutation(claims)
    statement = json.loads(json.dumps(env["statement"]))
    if change_statement:
        change_statement(statement)
    root_policy = signed(root, root_pub, claims, policy.POLICY_TYPE)
    dsse = signed(builder, builder_pub, statement, slsa.PAYLOAD_TYPE)
    args = {
        "policy_envelope": root_policy,
        "root_public_key": root_pub,
        "expected_root_sha256": hashlib.sha256(root_pub).hexdigest(),
        "builder_public_key": builder_pub,
        "attestation_envelope": dsse,
        "runtime_record": env["runtime"],
        "expected_service": SERVICE,
        "expected_commit": SHA,
        "expected_manifest_sha256": MANIFEST,
        "expected_builder_id": BUILDER,
        "expected_build_type": BUILD_TYPE,
        "externally_required_epoch": generation,
        "check_time_epoch": clock,
        "revoked_builder_key_sha256": revoked,
    }
    if expected:
        args.update(expected)
    return policy.verify_policy_and_image(**args)


def test_two_separate_test_signatures_do_not_authorize_release():
    result = check()
    assert result["operator_root_signature_under_supplied_key_valid"] is True
    assert result["builder_statement_signature_valid"] is True
    assert result["separate_builder_and_policy_keys"] is True
    assert result["production_decision"] == "HOLD"
    assert result["promotion_authorized"] is False
    assert result["independent_operator_root_custody_proven"] is False
    assert result["non_restorable_epoch_witness_proven"] is False
    assert result["clock_authenticity_proven"] is False
    assert result["revocation_freshness_proven"] is False


@pytest.mark.parametrize("field,value", [
    ("epoch", 44), ("epoch", 46), ("epoch", True),
    ("not_before", CHECK_TIME + 1000),
    ("expires_at", CHECK_TIME - 1),
    ("expires_at", CHECK_TIME + 86402),
    ("service", "assistx-worker"),
    ("source_commit", "d"*40),
    ("manifest_sha256", "d"*64),
    ("builder_id", "https://evil.example/attacker"),
    ("build_type", "https://evil.example/malicious"),
    ("builder_key_sha256", "e"*64),
    ("schema", "assistx.builder-authorization/v0"),
])
def test_re_signed_overbroad_policy_is_denied(field, value):
    with pytest.raises(slsa.ProvenanceHold):
        check(change_policy={field: value})


def test_unknown_extra_policy_field_denied():
    with pytest.raises(slsa.ProvenanceHold, match="unknown"):
        check(change_policy={"allowAll": True})


@pytest.mark.parametrize("epoch", [-1, 0, 44, 46, True, 1.5, 2**60])
def test_external_epoch_mismatch_or_invalid_is_denied(epoch):
    with pytest.raises(slsa.ProvenanceHold):
        check(generation=epoch)


@pytest.mark.parametrize("clock", [0, -1, CHECK_TIME-1000, CHECK_TIME+3600, True])
def test_expired_or_premature_trust_policy_denied(clock):
    with pytest.raises(slsa.ProvenanceHold):
        check(clock=clock)


def test_builder_and_root_keys_must_be_distinct():
    with pytest.raises(slsa.ProvenanceHold, match="identical"):
        check(same_key=True)


def test_revoked_builder_key_refuses_even_valid_signature():
    env = fixtures()
    builder_fingerprint = hashlib.sha256(env["builder_pub"]).hexdigest()
    # The fixture helper makes a new signer on each call: use patching to
    # record/revoke the signer in a single verification call instead.
    root = env["root"]
    builder = env["builder"]
    claim = env["claims"]
    p = signed(root, public(root), claim, policy.POLICY_TYPE)
    att = signed(builder, public(builder), env["statement"], slsa.PAYLOAD_TYPE)
    with pytest.raises(slsa.ProvenanceHold, match="revoked"):
        policy.verify_policy_and_image(
            policy_envelope=p, root_public_key=public(root),
            expected_root_sha256=hashlib.sha256(public(root)).hexdigest(),
            builder_public_key=public(builder),
            attestation_envelope=att, runtime_record=env["runtime"],
            expected_service=SERVICE, expected_commit=SHA,
            expected_manifest_sha256=MANIFEST,
            expected_builder_id=BUILDER, expected_build_type=BUILD_TYPE,
            externally_required_epoch=ROOT_EPOCH,
            check_time_epoch=CHECK_TIME,
            revoked_builder_key_sha256=frozenset({builder_fingerprint}),
        )


@pytest.mark.parametrize("bad", [["a"*64], frozenset({"bad"}), frozenset({45}), None])
def test_revocation_snapshot_must_be_explicit_well_formed(bad):
    with pytest.raises(slsa.ProvenanceHold, match="revocation"):
        check(revoked=bad)


def test_wrong_external_operator_root_fingerprint_denied():
    with pytest.raises(slsa.ProvenanceHold, match="fingerprint"):
        check(expected={"expected_root_sha256": "a"*64})


def test_signed_builder_attestation_wrong_commit_denied():
    with pytest.raises(slsa.ProvenanceHold, match="parameters"):
        check(change_statement=lambda x: x["predicate"]["buildDefinition"][
            "externalParameters"].update({"sourceRevision": "e"*40}))


def test_signed_builder_attestation_wrong_subject_denied():
    with pytest.raises(slsa.ProvenanceHold, match="subject"):
        check(change_statement=lambda x: x["subject"][0][
            "digest"].update({"sha256": "f"*64}))


def test_runtime_manifest_substitution_denied():
    e = fixtures()
    root, builder = e["root"], e["builder"]
    with pytest.raises(slsa.ProvenanceHold, match="manifest"):
        policy.verify_policy_and_image(
            policy_envelope=signed(root, public(root), e["claims"], policy.POLICY_TYPE),
            root_public_key=public(root),
            expected_root_sha256=hashlib.sha256(public(root)).hexdigest(),
            builder_public_key=public(builder),
            attestation_envelope=signed(
                builder, public(builder), e["statement"], slsa.PAYLOAD_TYPE
            ),
            runtime_record={
                **e["runtime"],
                "image": {"local_image_id": "sha256:"+"c"*64,
                          "manifest_digest_values": ["sha256:"+"f"*64]},
            },
            expected_service=SERVICE, expected_commit=SHA,
            expected_manifest_sha256=MANIFEST,
            expected_builder_id=BUILDER, expected_build_type=BUILD_TYPE,
            externally_required_epoch=ROOT_EPOCH, check_time_epoch=CHECK_TIME,
            revoked_builder_key_sha256=frozenset(),
        )


def test_signed_policy_payload_tamper_denied():
    e = fixtures()
    original = signed(e["root"], public(e["root"]), e["claims"], policy.POLICY_TYPE)
    original["payload"] = base64.b64encode(b'{"schema":"evil"}').decode()
    with pytest.raises(slsa.ProvenanceHold, match="signature"):
        policy._signed_policy(
            original, public(e["root"]),
            hashlib.sha256(public(e["root"])).hexdigest(),
        )


def test_root_policy_signature_domain_separation():
    e = fixtures()
    signed_body = signed(e["root"], public(e["root"]), e["claims"], policy.POLICY_TYPE)
    signed_body["payloadType"] = slsa.PAYLOAD_TYPE
    with pytest.raises(slsa.ProvenanceHold, match="domain"):
        policy._signed_policy(
            signed_body, public(e["root"]),
            hashlib.sha256(public(e["root"])).hexdigest(),
        )


def test_valid_signature_with_replayed_old_policy_generation_denied():
    with pytest.raises(slsa.ProvenanceHold, match="epoch"):
        check(generation=ROOT_EPOCH+1)


def test_cli_does_not_convert_missing_runtime_to_release(monkeypatch, capsys):
    monkeypatch.setattr(policy, "_runtime_record", lambda _service: (
        (_ for _ in ()).throw(slsa.ProvenanceHold("no docker data"))
    ))
    assert policy.main([
        "--policy", "/tmp/no-untrusted-policy",
        "--attestation", "/tmp/no-untrusted-statement",
        "--root-public-key", "/tmp/no-untrusted-root",
        "--builder-public-key", "/tmp/no-untrusted-signer",
        "--root-sha256", "a"*64,
        "--service", SERVICE,
        "--commit", SHA, "--manifest-sha256", MANIFEST,
        "--builder-id", BUILDER, "--build-type", BUILD_TYPE,
        "--required-epoch", "45",
    ]) == 3
    obj = json.loads(capsys.readouterr().out)
    assert obj["production_decision"] == "HOLD"
    assert obj["promotion_authorized"] is False
