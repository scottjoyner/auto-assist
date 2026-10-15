"""In-memory Ed25519 falsification: signature freshness is not resource authority."""
from __future__ import annotations

import base64
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SOURCE = Path(__file__).resolve().parents[1] / "scripts/verify-challenge-bound-highwater.py"
spec = importlib.util.spec_from_file_location("research_highwater_statement", SOURCE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
NOW = 1792000000000
CHALLENGE = "1" * 64
LAST_NONCE = "2" * 32
NEW_NONCE = "3" * 32
SHA = "a" * 64


def fixture():
    private = Ed25519PrivateKey.generate()
    pinned_roots = {
        mod._KEY_ID: private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    }
    effect = {
        "resource_id": "auto-router-runtime-projection",
        "term": 11, "commit_seq": 31, "generation": 7,
        "checksum": SHA, "revision_sha256": "b" * 64,
        "evidence_sha256": "c" * 64,
    }
    min_anchor = {"term": 11, "commit_seq": 30, "last_nonce": LAST_NONCE}
    claim = {
        "schema_version": 1,
        "audience": "research-highwater-consistency-only",
        "resource_id": "auto-router-runtime-projection",
        "term": 11, "commit_seq": 31,
        "last_nonce": NEW_NONCE,
        "challenge": CHALLENGE,
        "bound_effect_sha256": mod.effect_digest(effect),
        "issued_at_ms": NOW - 100,
        "expires_at_ms": NOW + 10_000,
        "key_epoch": 5,
    }
    def sign(to_sign, *, key=private):
        return {
            "claim": deepcopy(to_sign), "key_id": mod._KEY_ID,
            "signature": base64.b64encode(
                key.sign(mod._CLAIM_DOMAIN + mod._canonical(to_sign))
            ).decode("ascii"),
        }
    return sign, claim, effect, min_anchor, pinned_roots


def check(envelope, effect, anchor, roots, *,
          challenge=CHALLENGE, epoch=5, now=NOW):
    return mod.evaluate(
        envelope, expected_effect=effect, expected_challenge=challenge,
        minimum_highwater=anchor, pinned_roots=roots,
        pinned_key_epoch=epoch, now_ms=now,
    )


def test_valid_synthetic_statement_is_only_research_consistency():
    sign, claim, effect, anchor, roots = fixture()
    assert check(sign(claim), effect, anchor, roots) == (
        True, "RESEARCH_SIGNED_STATEMENT_ONLY"
    )


@pytest.mark.parametrize("mutation,reason", [
    (lambda c: c.update(challenge="4"*64), "challenge_replay_or_mismatch"),
    (lambda c: c.update(bound_effect_sha256="4"*64), "effect_binding_mismatch"),
    (lambda c: c.update(term=10), "anchor_does_not_match_resource_sequence"),
    (lambda c: c.update(commit_seq=29), "anchor_does_not_match_resource_sequence"),
    (lambda c: c.update(key_epoch=4), "pinned_key_epoch_mismatch"),
    (lambda c: c.update(audience="release-approved"), "signed_statement_shape_invalid"),
    (lambda c: c.update(schema_version=True), "signed_statement_shape_invalid"),
    (lambda c: c.update(last_nonce="not-a-nonce"), "signed_statement_shape_invalid"),
    (lambda c: c.update(expires_at_ms=NOW + 60_001), "signed_statement_not_fresh"),
    (lambda c: c.update(issued_at_ms=NOW+1), "signed_statement_not_fresh"),
])
def test_even_valid_signature_cannot_bypass_expected_public_contract(mutation, reason):
    sign, claim, effect, anchor, roots = fixture()
    mutation(claim)
    assert check(sign(claim), effect, anchor, roots) == (False, reason)


def test_replay_to_a_new_challenge_fails_even_if_old_signature_valid():
    sign, claim, effect, anchor, roots = fixture()
    old = sign(claim)
    assert check(old, effect, anchor, roots, challenge="f" * 64) == (
        False, "challenge_replay_or_mismatch"
    )


def test_unpinned_forged_signer_is_rejected():
    sign, claim, effect, anchor, roots = fixture()
    attacker = Ed25519PrivateKey.generate()
    assert check(sign(claim, key=attacker), effect, anchor, roots) == (
        False, "signed_statement_signature_invalid"
    )


def test_attacker_can_substitute_unprotected_trust_root_no_go():
    sign, claim, effect, anchor, roots = fixture()
    attacker = Ed25519PrivateKey.generate()
    roots[mod._KEY_ID] = attacker.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    # Intentional NO-GO: caller-provided trust roots can be swapped by a
    # common administrator. The code cannot attest independent custody.
    assert check(sign(claim, key=attacker), effect, anchor, roots) == (
        True, "RESEARCH_SIGNED_STATEMENT_ONLY"
    )


def test_restored_minimum_checkpoint_can_replay_a_valid_signed_claim_no_go():
    sign, claim, effect, anchor, roots = fixture()
    signed = sign(claim)
    new_anchor = {"term": 12, "commit_seq": 1, "last_nonce": "d"*32}
    assert check(signed, effect, new_anchor, roots) == (
        False, "external_checkpoint_rollback_or_conflict"
    )
    restored_anchor = deepcopy(anchor)
    assert check(signed, effect, restored_anchor, roots) == (
        True, "RESEARCH_SIGNED_STATEMENT_ONLY"
    )


def test_compromised_anchor_key_signs_fictional_resource_position_no_go():
    sign, claim, effect, anchor, roots = fixture()
    effect["generation"] = 9876
    claim["bound_effect_sha256"] = mod.effect_digest(effect)
    # Signer, expectation provider and high-water custodian collude.
    # Valid signature cannot establish a real transaction occurred.
    assert check(sign(claim), effect, anchor, roots) == (
        True, "RESEARCH_SIGNED_STATEMENT_ONLY"
    )


def test_unpinned_attestation_key_or_epoch_fails_closed():
    sign, claim, effect, anchor, roots = fixture()
    assert check(sign(claim), effect, anchor, {}, epoch=5) == (
        False, "external_root_unavailable"
    )
    assert check(sign(claim), effect, anchor, None, epoch=5) == (
        False, "independent_key_custody_not_configured"
    )
    assert check(sign(claim), effect, anchor, roots, epoch=0) == (
        False, "independent_key_custody_not_configured"
    )


def test_prior_checkpoint_same_position_requires_same_nonce():
    sign, claim, effect, anchor, roots = fixture()
    anchor.update(term=11, commit_seq=31, last_nonce=LAST_NONCE)
    assert check(sign(claim), effect, anchor, roots) == (
        False, "external_checkpoint_rollback_or_conflict"
    )
    anchor["last_nonce"] = NEW_NONCE
    assert check(sign(claim), effect, anchor, roots) == (
        True, "RESEARCH_SIGNED_STATEMENT_ONLY"
    )


def test_forward_position_must_advance_nonce():
    sign, claim, effect, anchor, roots = fixture()
    claim["last_nonce"] = LAST_NONCE
    assert check(sign(claim), effect, anchor, roots) == (
        False, "checkpoint_nonce_not_advanced"
    )


@pytest.mark.parametrize("source", [
    {}, None, "not a dict", {"claim": {}, "key_id": mod._KEY_ID},
])
def test_incomplete_or_invalid_envelopes_deny(source):
    sign, claim, effect, anchor, roots = fixture()
    assert check(source, effect, anchor, roots)[0] is False


@pytest.mark.parametrize("bad_anchor", [
    None, {}, {"term": True, "commit_seq": 30, "last_nonce": LAST_NONCE},
    {"term": 11, "commit_seq": 30, "last_nonce": "bad"},
])
def test_untrusted_previous_anchor_does_not_validate(bad_anchor):
    sign, claim, effect, anchor, roots = fixture()
    assert check(sign(claim), effect, bad_anchor, roots) == (
        False, "external_minimum_checkpoint_invalid"
    )


def test_effect_changes_after_statement_can_still_invalidate_actual_resource():
    sign, claim, effect, anchor, roots = fixture()
    signed = sign(claim)
    assert check(signed, effect, anchor, roots)[0] is True
    later_resource = dict(effect, generation=999)
    assert check(signed, later_resource, anchor, roots) == (
        False, "effect_binding_mismatch"
    )
    # The already-observed True verdict did not prevent a concurrent mutation.


def test_signed_statement_cli_cannot_authorize_releases():
    assert "NO_LIVE_AUTHORITY_OR_RELEASE_ENTRYPOINT" in SOURCE.read_text()
    # No TPM access; no persistence; no authorized resource readback.


def test_exact_same_signed_statement_is_repeatable_without_external_spent_state_no_go():
    """Current stateless verification is observation, not one-shot issuance."""
    sign, claim, effect, anchor, roots = fixture()
    fixed = sign(claim)
    first = check(fixed, effect, anchor, roots)
    second = check(fixed, effect, anchor, roots)
    assert first == second == (True, "RESEARCH_SIGNED_STATEMENT_ONLY")
    # Neither verification consumes the challenge or persists high-water;
    # a real one-shot protocol must bind to a separate atomic state owner.


def test_freshly_signed_challenge_for_already_observed_position_remains_valid_no_go():
    sign, claim, effect, anchor, roots = fixture()
    already = {"term": claim["term"], "commit_seq": claim["commit_seq"],
               "last_nonce": claim["last_nonce"]}
    refreshed = dict(claim, challenge="e" * 64)
    assert check(sign(refreshed), effect, already, roots,
                 challenge="e" * 64) == (True, "RESEARCH_SIGNED_STATEMENT_ONLY")
    # True == read-only observation; NOT a new resource commit authorization.


def test_strict_new_commit_observation_denies_equal_position():
    sign, claim, effect, anchor, roots = fixture()
    after = {"term": claim["term"], "commit_seq": claim["commit_seq"],
             "last_nonce": claim["last_nonce"]}
    result = mod.evaluate_strict_advance(
        sign(claim), expected_effect=effect, expected_challenge=CHALLENGE,
        minimum_highwater=after, pinned_roots=roots, pinned_key_epoch=5,
        now_ms=NOW,
    )
    assert result == (False, "no_strict_highwater_advance")


def test_even_strict_advancement_is_repeatable_without_atomic_external_cas_no_go():
    sign, claim, effect, anchor, roots = fixture()
    kwargs = dict(
        expected_effect=effect, expected_challenge=CHALLENGE,
        minimum_highwater=anchor, pinned_roots=roots, pinned_key_epoch=5,
        now_ms=NOW,
    )
    signed = sign(claim)
    first = mod.evaluate_strict_advance(signed, **kwargs)
    second = mod.evaluate_strict_advance(signed, **kwargs)
    assert first == second == (True, "RESEARCH_ADVANCEMENT_UNCONSUMED_ONLY")
