"""Signed external checkpoint reconciliation: deny false custody acceptance."""

from __future__ import annotations

import copy
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_trace_asymmetric_custody import two_keys as two_keys_fixture  # noqa: F401 -- transitive fixture
from test_trace_producer_manifest import custody as producer_fixture  # noqa: F401 -- transitive fixture
from test_trace_receiver_ledger import case as receiver_case  # noqa: F401 -- pytest fixture
from test_trace_segment_plan import history as huge_history  # noqa: F401 -- transitive fixture

from assistx.trace_asymmetric_custody import GENESIS, attest_with_producer_public_key, receipt_digest
from assistx.trace_execution_adapter import TraceDenied
from assistx.trace_receiver_checkpoint import (
    checkpoint_digest,
    reconcile_local_receipt,
    sign_fixture_checkpoint,
    verify_checkpoint,
)
from assistx.trace_receiver_ledger import append_receiver_receipt


@pytest.fixture
def case(receiver_case):  # noqa: F811 -- pytest fixture
    c, params = receiver_case
    authority = Ed25519PrivateKey.generate()
    assert authority.public_key() != c["witness"].public_key()
    values = {
        "authority_private": authority,
        "authority_id": "external-highwater-1",
        "witness_id": params["witness_id"],
        "producer_id": params["producer_id"],
        "producer_key_id": params["producer_key_id"],
        "receipt_sequence": 0,
        "receipt_sha256": GENESIS,
        "checkpoint_counter": 1,
        "previous_checkpoint_sha256": GENESIS,
        "issued_at_ms": 1_800_200_000_000,
    }
    checkpoint = sign_fixture_checkpoint(**values)
    opts = {
        "root": params["root"],
        "checkpoint": checkpoint,
        "authority_public": authority.public_key(),
        "witness_verifier": params["witness_verifier"],
        "authority_id": values["authority_id"],
        "witness_id": params["witness_id"],
        "producer_id": params["producer_id"],
        "producer_key_id": params["producer_key_id"],
        "expected_checkpoint_counter": 1,
        "expected_previous_checkpoint_sha256": GENESIS,
        "minimum_issued_at_ms": 1_800_200_000_000,
    }
    return c, params, values, opts


def test_empty_ledger_and_signed_genesis_checkpoint_sync(case):
    c, p, values, opts = case
    result = reconcile_local_receipt(**opts)
    assert result["state"] == "SYNCHRONIZED"
    assert result["local_sequence"] == 0
    assert result["receipt_sha256"] == GENESIS
    assert result["independent_worm_proven"] is False


def test_local_fsync_then_external_ack_missing_pending_not_custody(case):
    c, p, values, opts = case
    append_receiver_receipt(receipt=c["receipt"], **p)
    result = reconcile_local_receipt(**opts)
    assert result["state"] == "PENDING_EXTERNAL_RECONCILIATION"
    assert result["custody_acknowledged"] is False
    assert result["previous_receipt_sha256"] == GENESIS
    assert result["receipt_sha256"] == receipt_digest(c["receipt"])
    assert result["independent_worm_proven"] is False


def test_later_separately_signed_checkpoint_reconciles_pending(case):
    c, p, values, opts = case
    append_receiver_receipt(receipt=c["receipt"], **p)
    next_checkpoint = sign_fixture_checkpoint(
        **{
            **values,
            "receipt_sequence": 1,
            "receipt_sha256": receipt_digest(c["receipt"]),
            "checkpoint_counter": 2,
            "previous_checkpoint_sha256": checkpoint_digest(opts["checkpoint"]),
            "issued_at_ms": values["issued_at_ms"] + 1,
        }
    )
    result = reconcile_local_receipt(
        **{
            **opts,
            "checkpoint": next_checkpoint,
            "expected_checkpoint_counter": 2,
            "expected_previous_checkpoint_sha256": checkpoint_digest(opts["checkpoint"]),
        }
    )
    assert result["state"] == "SYNCHRONIZED"
    assert result["local_sequence"] == 1
    assert result["independent_worm_proven"] is False


def test_checkpoint_ahead_of_missing_local_ledger_denied(case):
    c, p, values, opts = case
    followup = sign_fixture_checkpoint(
        **{
            **values,
            "receipt_sequence": 1,
            "receipt_sha256": receipt_digest(c["receipt"]),
            "checkpoint_counter": 2,
            "previous_checkpoint_sha256": checkpoint_digest(opts["checkpoint"]),
        }
    )
    with pytest.raises(TraceDenied, match="receiver_checkpoint_local_rollback"):
        reconcile_local_receipt(
            **{
                **opts,
                "checkpoint": followup,
                "expected_checkpoint_counter": 2,
                "expected_previous_checkpoint_sha256": checkpoint_digest(opts["checkpoint"]),
            }
        )


def test_wrong_remote_receipt_hash_fails_even_with_valid_signature(case):
    c, p, values, opts = case
    append_receiver_receipt(receipt=c["receipt"], **p)
    mismatched = sign_fixture_checkpoint(**{**values, "receipt_sequence": 1, "receipt_sha256": "e" * 64})
    with pytest.raises(TraceDenied, match="receiver_checkpoint_conflicting_head"):
        reconcile_local_receipt(**{**opts, "checkpoint": mismatched})


def test_two_unacknowledged_receipts_denied(case):
    c, p, values, opts = case
    append_receiver_receipt(receipt=c["receipt"], **p)
    digest1 = receipt_digest(c["receipt"])
    second = attest_with_producer_public_key(
        **{
            **c["kwargs"],
            "receipt_sequence": 2,
            "previous_receipt_sha256": digest1,
            "observed_at_ms": 1_800_100_002_000,
        }
    )
    append_receiver_receipt(
        receipt=second, **{**p, "external_expected_sequence": 2, "external_expected_previous_digest": digest1}
    )
    with pytest.raises(TraceDenied, match="receiver_checkpoint_multiple_unacknowledged"):
        reconcile_local_receipt(**opts)


@pytest.mark.parametrize(
    "mutate",
    [
        {"authority_id": "other-authority"},
        {"witness_id": "wrong-witness"},
        {"producer_id": "other-producer"},
        {"producer_key_id": "wrong-key"},
    ],
)
def test_authority_identity_mismatch_denied(case, mutate):
    c, p, values, opts = case
    with pytest.raises(TraceDenied, match="receiver_checkpoint_authority_mismatch"):
        reconcile_local_receipt(**{**opts, **mutate})


@pytest.mark.parametrize(
    "mutate",
    [
        {"expected_checkpoint_counter": 2},
        {"expected_previous_checkpoint_sha256": "f" * 64},
        {"minimum_issued_at_ms": 1_800_200_000_001},
    ],
)
def test_independent_checkpoint_head_replay_denied(case, mutate):
    c, p, values, opts = case
    with pytest.raises(TraceDenied, match="receiver_checkpoint_replay_or_rollback"):
        reconcile_local_receipt(**{**opts, **mutate})


def test_foreign_external_signer_denied(case):
    c, p, values, opts = case
    with pytest.raises(TraceDenied, match="receiver_checkpoint_signature_invalid"):
        reconcile_local_receipt(**{**opts, "authority_public": Ed25519PrivateKey.generate().public_key()})


def test_changed_signed_anchor_denied(case):
    c, p, values, opts = case
    changed = copy.deepcopy(opts["checkpoint"])
    changed["issued_at_ms"] += 1
    with pytest.raises(TraceDenied, match="receiver_checkpoint_signature_invalid"):
        reconcile_local_receipt(**{**opts, "checkpoint": changed})


def test_unknown_checkpoint_fields_denied(case):
    c, p, values, opts = case
    changed = {**opts["checkpoint"], "allow_shell": True}
    with pytest.raises(TraceDenied, match="receiver_checkpoint_schema_invalid"):
        reconcile_local_receipt(**{**opts, "checkpoint": changed})


def test_torn_local_ledger_is_never_recovered_by_checkpoint(case):
    c, p, values, opts = case
    append_receiver_receipt(receipt=c["receipt"], **p)
    path = p["root"] / "receiver-receipts.jsonl"
    old = path.read_bytes()
    path.write_bytes(old + b'{"unfinished"')
    with pytest.raises(TraceDenied, match="receiver_ledger_torn_row"):
        reconcile_local_receipt(**opts)
    assert path.read_bytes().endswith(b'{"unfinished"')


def test_external_checkpoint_never_creates_new_ledger_on_sync(case):
    c, p, values, opts = case
    assert reconcile_local_receipt(**opts)["state"] == "SYNCHRONIZED"
    assert not (p["root"] / "receiver-receipts.jsonl").exists()
