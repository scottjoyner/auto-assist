"""Synthetic-only negative controls for a fictional post-COMMIT witness."""
from __future__ import annotations

import base64
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "scripts/prove-independent-convergence-witness.py"
)
SPEC = importlib.util.spec_from_file_location("independent_convergence_research", SOURCE)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
NOW = 1792000000000
SHA = "a" * 64
REV = "b" * 64
FILE_SHA = "c" * 64
NONCE = "d" * 32
KEY_ID = "independent-resource-v1"


def fixture():
    private = Ed25519PrivateKey.generate()
    roots = {KEY_ID: private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )}
    claim = {
        "schema_version": 1,
        "audience": "convergence-evidence-research",
        "resource_id": "auto-router-runtime-projection",
        "generation": 7, "checksum": SHA, "revision_sha256": REV,
        "evidence_sha256": FILE_SHA, "term": 11, "commit_seq": 31,
        "nonce": NONCE, "issued_at_ms": NOW - 1000,
        "expires_at_ms": NOW + 60000,
    }
    expected = {key: claim[key] for key in mod._EXPECTED_KEYS}
    resource = {key: claim[key] for key in mod._RESOURCE_KEYS - {"committed"}}
    resource["committed"] = True
    anchor = {"term": 11, "commit_seq": 30, "last_nonce": "e" * 32}

    def signed(payload):
        return {
            "claim": deepcopy(payload),
            "key_id": KEY_ID,
            "signature": base64.b64encode(
                private.sign(mod._canonical_claim(payload))
            ).decode(),
        }

    return private, roots, signed, claim, expected, resource, anchor


def check(envelope, roots, expected, resource, anchor, *, readback=None, now=NOW):
    return mod.evaluate(
        envelope, trust_roots=roots, expected_evidence=expected,
        external_highwater=anchor,
        readback=(lambda: deepcopy(resource)) if readback is None else readback,
        now_ms=now,
    )


def test_valid_signature_current_double_readback_and_external_anchor_is_consistent():
    private, roots, signed, claim, expected, resource, anchor = fixture()
    observed = []
    def external_readback():
        observed.append("read")
        return deepcopy(resource)
    assert check(signed(claim), roots, expected, resource, anchor,
                 readback=external_readback) == (True, "RESEARCH_CONSISTENCY_ONLY")
    assert len(observed) == 2


def test_signed_fiction_without_resource_readback_must_deny():
    private, roots, signed, claim, expected, resource, anchor = fixture()
    result = mod.evaluate(
        signed(claim), trust_roots=roots, expected_evidence=expected,
        external_highwater=anchor, readback=None, now_ms=NOW,
    )
    assert result == (False, "independent_resource_readback_missing")


def test_signed_fiction_without_committed_effect_must_deny():
    private, roots, signed, claim, expected, resource, anchor = fixture()
    resource["committed"] = False
    assert check(signed(claim), roots, expected, resource, anchor) == (
        False, "resource_readback_not_authoritative"
    )


@pytest.mark.parametrize("field,new_value", [
    ("generation", 9), ("checksum", "f" * 64),
    ("revision_sha256", "e" * 64), ("term", 12), ("commit_seq", 33),
])
def test_valid_signature_with_fictional_effect_does_not_match_authoritative_readback(
    field, new_value,
):
    private, roots, signed, claim, expected, resource, anchor = fixture()
    claim[field] = new_value
    if field in expected:
        expected[field] = new_value  # Colluding source manifest also altered.
    assert check(signed(claim), roots, expected, resource, anchor) == (
        False, "witness_resource_commit_mismatch"
    )


def test_concurrent_effect_swap_between_readbacks_denied():
    private, roots, signed, claim, expected, resource, anchor = fixture()
    events = [deepcopy(resource), {**resource, "commit_seq": 32}]
    def observer():
        return events.pop(0)
    assert check(signed(claim), roots, expected, resource, anchor,
                 readback=observer) == (
        False, "resource_effect_changed_during_readback"
    )


def test_external_highwater_ahead_after_ledger_restore_denies_old_commit():
    private, roots, signed, claim, expected, resource, anchor = fixture()
    anchor.update(term=12, commit_seq=1)  # Independently held newer epoch.
    assert check(signed(claim), roots, expected, resource, anchor) == (
        False, "witness_replay_or_restored_highwater"
    )


@pytest.mark.parametrize("anchor", [
    None,
    {},
    {"term": 11, "commit_seq": 31, "last_nonce": "e"*32},
    {"term": 11, "commit_seq": 30, "last_nonce": NONCE},
    {"term": True, "commit_seq": 30, "last_nonce": "e"*32},
])
def test_anchor_missing_stale_or_replayed_denied(anchor):
    private, roots, signed, claim, expected, resource, _ = fixture()
    assert check(signed(claim), roots, expected, resource, anchor)[0] is False


def test_signed_fiction_with_colluding_fake_readback_can_still_appear_consistent():
    """Explicit NO-GO: this module cannot prove callback's authority."""
    private, roots, signed, claim, expected, resource, anchor = fixture()
    claim["generation"] = 99
    expected["generation"] = 99
    resource["generation"] = 99  # Malicious fake callback falsely claims COMMIT.
    assert check(signed(claim), roots, expected, resource, anchor) == (
        True, "RESEARCH_CONSISTENCY_ONLY"
    )


def test_wrong_public_key_forged_signature_denied():
    private, roots, signed, claim, expected, resource, anchor = fixture()
    alternate = Ed25519PrivateKey.generate()
    roots[KEY_ID] = alternate.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    assert check(signed(claim), roots, expected, resource, anchor) == (
        False, "witness_signature_invalid"
    )


def test_signature_does_not_rebind_to_modified_manifest():
    private, roots, signed, claim, expected, resource, anchor = fixture()
    envelope = signed(claim)
    envelope["claim"]["evidence_sha256"] = "0" * 64
    assert check(envelope, roots, expected, resource, anchor)[0] is False


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(audience="production-release"),
    lambda c: c.update(resource_id="untrusted-router"),
    lambda c: c.update(nonce="not-hex"),
    lambda c: c.update(commit_seq=True),
    lambda c: c.update(expires_at_ms=NOW + 600000),
    lambda c: c.update(issued_at_ms=NOW + 1),
])
def test_invalid_claim_scope_or_time_denied(mutation):
    private, roots, signed, claim, expected, resource, anchor = fixture()
    mutation(claim)
    assert check(signed(claim), roots, expected, resource, anchor)[0] is False


def test_resource_callback_exception_fails_closed():
    private, roots, signed, claim, expected, resource, anchor = fixture()
    def failed():
        raise OSError("Synthetic untrusted failure string")
    assert check(signed(claim), roots, expected, resource, anchor,
                 readback=failed) == (False, "resource_readback_unavailable")


def test_trusted_readback_does_not_count_without_pinned_key_or_highwater():
    private, roots, signed, claim, expected, resource, anchor = fixture()
    assert check(signed(claim), {}, expected, resource, anchor) == (
        False, "pinned_trust_root_unavailable"
    )
    assert check(signed(claim), roots, expected, resource, None) == (
        False, "external_highwater_missing_or_invalid"
    )


def test_rollback_of_the_only_highwater_anchor_is_not_cryptographically_detectable():
    """An attacker controlling the anchor can replay: external custody is P0."""
    private, roots, signed, claim, expected, resource, anchor = fixture()
    assert check(signed(claim), roots, expected, resource, anchor) == (
        True, "RESEARCH_CONSISTENCY_ONLY"
    )
    # A previously accepted receipt becomes replayable after state rollback
    # if *all* verifier memory is restored or controlled by the attacker.
    restored_anchor = {"term": 10, "commit_seq": 1, "last_nonce": "e" * 32}
    assert check(signed(claim), roots, expected, resource, restored_anchor) == (
        True, "RESEARCH_CONSISTENCY_ONLY"
    )
