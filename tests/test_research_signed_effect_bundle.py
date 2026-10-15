"""Cross-sign synthetic effect bytes to witness certificate; separate keys only.

The resource-attestor and witness private keys are owned by ONE pytest
administrator. PASSING hostile-admin tests deliberately document NO-GO.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts/research-verify-signed-effect-bundle.py"
SPEC = importlib.util.spec_from_file_location("signed_effect_verifier", MODULE)
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)
OLDER = ROOT / "tests/test_research_effect_artifact_gate.py"
SPEC2 = importlib.util.spec_from_file_location("effect_artifact_research_cases", OLDER)
assert SPEC2 and SPEC2.loader
prior = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(prior)
NOW = prior.prior.old.NOW
WID = prior.WID
CHALLENGE = prior.prior.old.CHALLENGE


@pytest.fixture
def lab(tmp_path):
    state = prior.lab.__wrapped__(tmp_path)
    state["crash"]("no_crash")
    prior.gate.create_disposable_effect(state["artifact"], prior.PAYLOAD)
    ok, reason, cert = state["issue_effect"]()
    assert (ok, reason) == (True, "RESEARCH_WITNESS_LOCALLY_ISSUED_HOLD")
    effect_key = Ed25519PrivateKey.generate()
    effect_root = effect_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    root = state["roots"][WID]["public"]
    receipt = state["expected"]

    def assemble(
        *, attestor=effect_key, witness=None,
        effect_override=None, cert_override=None, join_override=None,
    ):
        member_key = state["keys"][WID] if witness is None else witness
        member_cert = copy.deepcopy(cert if cert_override is None else cert_override)
        effect = {
            "schema_version": 1,
            "audience": "synthetic-committed-resource-effect",
            "resource_id": receipt["resource_id"], "term": receipt["term"],
            "commit_seq": receipt["commit_seq"], "generation": receipt["generation"],
            "checksum": receipt["checksum"],
            "revision_sha256": receipt["revision_sha256"],
            "receipt_digest": receipt["receipt_digest"],
            "artifact_sha256": prior.EXPECTED_DIGEST,
            "nonce": receipt["nonce"], "challenge": CHALLENGE,
            "key_epoch": 4,
            "issued_at_ms": NOW - 100,
            "expires_at_ms": NOW + 10_000,
        }
        if effect_override:
            effect.update(effect_override)
        encoded_effect = verify.canonical(verify._EFFECT_DOMAIN, effect)
        resource_sig = base64.b64encode(attestor.sign(encoded_effect)).decode("ascii")
        unsigned_member = {
            k: v for k, v in member_cert.items() if k != "signature"
        }
        bind = {
            "schema_version": 1,
            "audience": "synthetic-witness-effect-binding",
            "witness_id": WID, "resource_id": receipt["resource_id"],
            "term": receipt["term"], "commit_seq": receipt["commit_seq"],
            "nonce": receipt["nonce"], "challenge": CHALLENGE,
            "artifact_sha256": effect["artifact_sha256"],
            "effect_attestation_sha256": hashlib.sha256(
                encoded_effect
            ).hexdigest(),
            "witness_certificate_sha256": hashlib.sha256(
                verify._quorum().canonical(unsigned_member)
            ).hexdigest(),
            "key_epoch": 3, "issued_at_ms": NOW - 100,
            "expires_at_ms": NOW + 10_000,
        }
        if join_override:
            bind.update(join_override)
        member_sig = base64.b64encode(member_key.sign(
            verify.canonical(verify._BIND_DOMAIN, bind)
        )).decode("ascii")
        return {
            "effect_attestation": {
                "key_id": verify._KEY_ID,
                "claim": effect, "signature": resource_sig,
            },
            "witness_certificate": member_cert,
            "witness_binding": {"claim": bind, "signature": member_sig},
        }

    def check(bundle=None, *, eff_root=effect_root, wit_root=root,
              clock=NOW, challenge=CHALLENGE, source=None, anchor=None,
              artifact=None):
        return verify.verify(
            state["resource"] if source is None else source,
            state["anchor"] if anchor is None else anchor,
            state["artifact"] if artifact is None else artifact,
            expected=receipt,
            bundle=assemble() if bundle is None else bundle,
            pinned_effect_root=eff_root,
            pinned_witness_root=wit_root,
            expected_witness_id=WID, expected_challenge=challenge, now_ms=clock,
        )

    return {
        **state, "effect_key": effect_key, "effect_root": effect_root,
        "witness_root": root, "assemble": assemble, "check": check,
    }


def test_two_signatures_bind_same_witness_receipt_and_fsynced_effect(lab):
    assert lab["check"]() == (
        True, "RESEARCH_CRYPTOGRAPHIC_EFFECT_BUNDLE_ONLY_HOLD"
    )


@pytest.mark.parametrize(("field", "replacement", "reason"), [
    ("artifact_sha256", "0" * 64, "SIGNED_EFFECT_ARTIFACT_MISMATCH_NO_GO"),
    ("term", 5, "EFFECT_CLAIM_RESOURCE_MISMATCH_NO_GO"),
    ("commit_seq", 13, "EFFECT_CLAIM_RESOURCE_MISMATCH_NO_GO"),
    ("generation", 99, "EFFECT_CLAIM_RESOURCE_MISMATCH_NO_GO"),
    ("nonce", "f" * 32, "EFFECT_CLAIM_RESOURCE_MISMATCH_NO_GO"),
    ("receipt_digest", "0" * 64, "EFFECT_CLAIM_RESOURCE_MISMATCH_NO_GO"),
    ("key_epoch", 99, "EFFECT_BUNDLE_CONTEXT_OR_FRESHNESS_DENIED_NO_GO"),
    ("expires_at_ms", NOW - 1, "EFFECT_BUNDLE_CONTEXT_OR_FRESHNESS_DENIED_NO_GO"),
    ("challenge", "1" * 64, "EFFECT_BUNDLE_CONTEXT_OR_FRESHNESS_DENIED_NO_GO"),
])
def test_resigned_effect_does_not_misbind_artifact_or_resource(lab, field, replacement, reason):
    bundle = lab["assemble"](effect_override={field: replacement})
    assert lab["check"](bundle) == (False, reason)


def test_unsigned_tamper_of_effect_digest_denied(lab):
    bundle = lab["assemble"]()
    bundle["effect_attestation"]["claim"]["artifact_sha256"] = "f" * 64
    assert lab["check"](bundle) == (
        False, "RESOURCE_EFFECT_SIGNATURE_INVALID_NO_GO"
    )


def test_unknown_effect_attestor_key_denied(lab):
    attacker = Ed25519PrivateKey.generate()
    bundle = lab["assemble"](attestor=attacker)
    assert lab["check"](bundle) == (
        False, "RESOURCE_EFFECT_SIGNATURE_INVALID_NO_GO"
    )


def test_unknown_witness_binding_key_denied(lab):
    attacker = Ed25519PrivateKey.generate()
    bundle = lab["assemble"](witness=attacker)
    assert lab["check"](bundle) == (
        False, "WITNESS_EFFECT_BINDING_SIGNATURE_INVALID_NO_GO"
    )


def test_witness_receipt_signed_bytes_tampered_denied(lab):
    bundle = lab["assemble"]()
    bundle["witness_certificate"]["tip"]["commit_seq"] = 13
    assert lab["check"](bundle) == (
        False, "WITNESS_CERTIFICATE_RECEIPT_MISMATCH_NO_GO"
    )


def test_no_attestation_component_denied(lab):
    bundle = lab["assemble"]()
    del bundle["effect_attestation"]
    assert lab["check"](bundle) == (False, "EFFECT_BUNDLE_ENVELOPE_INVALID_NO_GO")


def test_missing_public_roots_denied(lab):
    assert lab["check"](eff_root=b"") == (
        False, "INDEPENDENT_ROOT_OR_CHALLENGE_MISSING_NO_GO"
    )
    assert lab["check"](wit_root=None) == (
        False, "INDEPENDENT_ROOT_OR_CHALLENGE_MISSING_NO_GO"
    )


def test_replayed_bundle_under_fresh_challenge_denied(lab):
    old = lab["assemble"]()
    assert lab["check"](old, challenge="b" * 64) == (
        False, "EFFECT_BUNDLE_CONTEXT_OR_FRESHNESS_DENIED_NO_GO"
    )


def test_expired_bundle_denied_even_with_valid_signatures(lab):
    assert lab["check"](clock=NOW + 20_000) == (
        False, "EFFECT_BUNDLE_CONTEXT_OR_FRESHNESS_DENIED_NO_GO"
    )


def test_artifact_mutated_after_bundle_signing_denied(lab):
    lab["artifact"].write_bytes(b"privileged-mutation-after-sign")
    assert lab["check"]() == (False, "SIGNED_EFFECT_ARTIFACT_MISMATCH_NO_GO")


def test_member_db_altered_after_effect_bundle_denied(lab):
    with sqlite3.connect(lab["anchor"]) as admin:
        admin.execute("DELETE FROM spent_challenges")
    assert lab["check"]() == (False, "RESOURCE_OR_ANCHOR_NOT_TERMINAL_NO_GO")


def test_absent_effect_file_denied_without_recreation(lab):
    lab["artifact"].unlink()
    assert lab["check"]() == (False, "EFFECT_ARTIFACT_ABSENT_NO_GO")
    assert not lab["artifact"].exists()


def test_member_witness_receipt_cannot_be_substituted(lab):
    bundle = lab["assemble"]()
    bundle["witness_certificate"]["signature"] = "AA=="
    assert lab["check"](bundle) == (
        False, "WITNESS_CERTIFICATE_SIGNATURE_INVALID_NO_GO"
    )


def test_join_to_different_witness_digest_denied_even_when_resigned(lab):
    bundle = lab["assemble"](join_override={
        "witness_certificate_sha256": "0" * 64,
    })
    assert lab["check"](bundle) == (
        False, "SIGNED_EFFECT_WITNESS_JOIN_MISMATCH_NO_GO"
    )


def test_pinned_root_substitution_compromises_both_signers_no_go(lab):
    # The same root administrator can replace BOTH pins, invent independent
    # signing "identities", sign the effect and an alternate witness member.
    attacker_resource = Ed25519PrivateKey.generate()
    attacker_witness = Ed25519PrivateKey.generate()
    counterfeit = lab["assemble"](
        attestor=attacker_resource, witness=attacker_witness,
    )
    # Also replace base member signature with attacker's key:
    original = counterfeit["witness_certificate"]
    unsigned = {k: v for k, v in original.items() if k != "signature"}
    original["signature"] = base64.b64encode(attacker_witness.sign(
        verify._quorum().canonical(unsigned)
    )).decode("ascii")
    # Join hash covers UNSIGNED contents, not previous signature.
    fake_effect_root = attacker_resource.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    fake_member_root = attacker_witness.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    assert lab["check"](
        counterfeit, eff_root=fake_effect_root, wit_root=fake_member_root
    ) == (True, "RESEARCH_CRYPTOGRAPHIC_EFFECT_BUNDLE_ONLY_HOLD")


def test_shared_host_forged_sql_and_bytes_can_be_attested_no_go(lab, tmp_path):
    # Existing genuine witness output is discarded; here attacker could
    # produce the same byte pattern and use two local keys, with no trusted
    # evidence that a real effect owner (not host admin) executed anything.
    lab["artifact"].unlink()
    prior.gate.create_disposable_effect(lab["artifact"], prior.PAYLOAD)
    fake = lab["assemble"]()
    assert lab["check"](fake)[0] is True


def test_success_does_not_fence_mutation_after_verification_no_go(lab):
    assert lab["check"]()[0] is True
    lab["artifact"].write_bytes(b"changed-after-positive-observation")
    assert lab["check"]()[0] is False


def test_verifier_has_no_private_key_or_release_entrypoint():
    code = MODULE.read_text()
    assert "Ed25519PrivateKey" not in code
    assert "RESEARCH_ONLY_NO_RESOURCE_OWNER_OR_RELEASE_ENTRYPOINT" in code
    assert "issue_with_effect" not in code
    assert "release_authorized" not in code


def test_shared_admin_fakes_commit_then_cross_signs_bundle_no_go(lab, tmp_path):
    """Two valid signatures and exact fsynced bytes still accept false CAS.

    An attacker who has source/anchor privileged SQL, the filesystem, and
    both signing keys can make the public verifier accept matching data.
    A cryptographic join does not prove an AUTHORIZED COMMIT happened.
    """
    other = tmp_path / "disposable-independent-test-files"
    other.mkdir()
    fictitious = prior.lab.__wrapped__(other)
    prior.fake_sql_anchor_commit_without_authorized_cas(fictitious)
    prior.gate.create_disposable_effect(fictitious["artifact"], prior.PAYLOAD)
    ok, reason, cert = fictitious["issue_effect"]()
    assert (ok, reason) == (True, "RESEARCH_WITNESS_LOCALLY_ISSUED_HOLD")
    # Construct identical receipt/bytes, but from the forged-anchor case.
    packet = lab["assemble"](cert_override=cert)
    witness_key = fictitious["keys"][WID]
    packet["witness_binding"]["signature"] = base64.b64encode(
        witness_key.sign(verify.canonical(
            verify._BIND_DOMAIN, packet["witness_binding"]["claim"]
        ))
    ).decode("ascii")
    check = verify.verify(
        fictitious["resource"], fictitious["anchor"], fictitious["artifact"],
        expected=fictitious["expected"], bundle=packet,
        pinned_effect_root=lab["effect_root"],
        pinned_witness_root=fictitious["roots"][WID]["public"],
        expected_witness_id=WID, expected_challenge=CHALLENGE,
        now_ms=NOW,
    )
    assert check == (True, "RESEARCH_CRYPTOGRAPHIC_EFFECT_BUNDLE_ONLY_HOLD")
    # The evidence is coherent, but it was NEVER the result of an
    # authorized anchor-CAS; the verdict is research-only by construction.


def test_effect_attestation_does_not_cryptographically_authorize_future_effect(lab):
    # It verifies only the currently sampled artifact and SQL rows.
    # A privileged resource writer may change them *immediately after*.
    packet = lab["assemble"]()
    assert lab["check"](packet)[0] is True
    with sqlite3.connect(lab["resource"]) as admin:
        admin.execute("UPDATE effect SET generation=31337")
    assert lab["check"](packet) == (
        False, "RESOURCE_OR_ANCHOR_NOT_TERMINAL_NO_GO"
    )


def test_member_commit_without_join_is_uncertain_not_permission_to_reissue(lab):
    # The #393 member tip is committed *before* this verifier ever receives a
    # joined bundle. A crash between those actions can leave seq12 consumed
    # but no deliverable signed effect-binding envelope.
    unbound = lab["assemble"]()
    del unbound["witness_binding"]
    assert lab["check"](unbound) == (
        False, "EFFECT_BUNDLE_ENVELOPE_INVALID_NO_GO"
    )
    assert lab["issue_effect"]() == (
        False, "WITNESS_STALE_OR_ALREADY_ISSUED", None
    )


def test_two_independent_signatures_are_not_quorum_release_authorization(lab):
    bundle = lab["assemble"]()
    assert lab["check"](bundle)[1] == (
        "RESEARCH_CRYPTOGRAPHIC_EFFECT_BUNDLE_ONLY_HOLD"
    )
    # No release_authorized flag, execution token, or retry recommendation
    # is ever issued by the verifier.
    assert set(bundle) == {
        "effect_attestation", "witness_certificate", "witness_binding",
    }
    assert all("release_authorized" not in part for part in bundle.values())
