"""Receiver local fsync acceptance with externally pinned monotonic anchors."""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_trace_asymmetric_custody import two_keys as two_keys_fixture  # noqa: F401 -- imported pytest fixture
from test_trace_producer_manifest import custody as producer_fixture  # noqa: F401 -- imported pytest fixture
from test_trace_segment_plan import history as huge_history  # noqa: F401 -- transitive fixture

from assistx.trace_asymmetric_custody import (
    GENESIS,
    attest_with_producer_public_key,
    receipt_digest,
)
from assistx.trace_execution_adapter import TraceDenied
from assistx.trace_producer_manifest import manifest_digest
from assistx.trace_receiver_ledger import (
    NAME,
    append_receiver_receipt,
    read_receiver_ledger,
)


@pytest.fixture
def case(tmp_path, two_keys_fixture):  # noqa: F811 -- pytest fixture
    root = tmp_path / "ledger"
    root.mkdir(mode=0o700)
    c = two_keys_fixture
    params = dict(
        root=root,
        witness_verifier=c["witness"].public_key(),
        witness_id="independent-custodian-1",
        producer_id="controller-a",
        producer_key_id="manifest-key-2026-a",
        external_expected_sequence=1,
        external_expected_previous_digest=GENESIS,
        expected_manifest_sha256=manifest_digest(c["producer"]["manifest"]),
        expected_node_id="xwing",
        expected_journal_sha256=c["producer"]["journal_sha"],
        expected_index_sha256=c["producer"]["index_sha"],
        minimum_observed_at_ms=1_800_100_001_000,
    )
    return c, params


def state(c, params):
    return read_receiver_ledger(
        params["root"],
        witness_verifier=params["witness_verifier"],
        witness_id=params["witness_id"],
        producer_id=params["producer_id"],
        producer_key_id=params["producer_key_id"],
    )


def test_initial_receipt_fsyncs_and_revalidates_from_disk(case):
    c, params = case
    assert state(c, params) == (0, GENESIS)
    proof = append_receiver_receipt(receipt=c["receipt"], **params)
    assert proof["ok"] and proof["receiver_local_fsynced"]
    assert proof["independent_anchor_fsynced"] is False
    assert proof["worm_storage_proven"] is False
    assert state(c, params) == (1, receipt_digest(c["receipt"]))
    assert (params["root"] / NAME).stat().st_mode & 0o777 == 0o600


def test_second_receipt_requires_explicit_external_prev_anchor(case):
    c, p = case
    append_receiver_receipt(receipt=c["receipt"], **p)
    previous = receipt_digest(c["receipt"])
    second = attest_with_producer_public_key(
        **{
            **c["kwargs"],
            "receipt_sequence": 2,
            "previous_receipt_sha256": previous,
            "observed_at_ms": 1_800_100_002_000,
        }
    )
    with pytest.raises(TraceDenied, match="receiver_external_anchor_mismatch"):
        append_receiver_receipt(receipt=second, **p)
    q = {**p, "external_expected_sequence": 2, "external_expected_previous_digest": previous}
    result = append_receiver_receipt(receipt=second, **q)
    assert result["sequence"] == 2
    assert state(c, p) == (2, receipt_digest(second))


def test_old_receipt_or_old_external_anchor_denied(case):
    c, p = case
    append_receiver_receipt(receipt=c["receipt"], **p)
    with pytest.raises(TraceDenied, match="receiver_external_anchor_mismatch"):
        append_receiver_receipt(receipt=c["receipt"], **p)
    assert len((p["root"] / NAME).read_text().splitlines()) == 1


def test_missing_ledger_after_prior_external_anchor_denied(case):
    c, p = case
    append_receiver_receipt(receipt=c["receipt"], **p)
    (p["root"] / NAME).unlink()  # simulate adversarial rollback of local state
    q = {**p, "external_expected_sequence": 2, "external_expected_previous_digest": receipt_digest(c["receipt"])}
    with pytest.raises(TraceDenied, match="receiver_external_anchor_mismatch"):
        append_receiver_receipt(receipt=c["receipt"], **q)
    assert not (p["root"] / NAME).exists()


def test_wrong_node_and_producer_manifest_denied(case):
    c, p = case
    for update in (
        {"expected_node_id": "scotts-macbook-air"},
        {"expected_manifest_sha256": "e" * 64},
        {"expected_journal_sha256": "f" * 64},
    ):
        with pytest.raises(TraceDenied, match="dual_custody_binding_mismatch"):
            append_receiver_receipt(receipt=c["receipt"], **{**p, **update})
    assert state(c, p) == (0, GENESIS)


def test_invalid_signer_and_tampered_receipt_denied(case):
    c, p = case
    tampered = copy.deepcopy(c["receipt"])
    tampered["records"] = 2
    with pytest.raises(TraceDenied, match="dual_custody_signature_invalid"):
        append_receiver_receipt(receipt=tampered, **p)
    assert state(c, p) == (0, GENESIS)


def test_torn_record_detected_and_not_truncated(case):
    c, p = case
    append_receiver_receipt(receipt=c["receipt"], **p)
    ledger = p["root"] / NAME
    original = ledger.read_bytes()
    ledger.write_bytes(original + b'{"truncated":')
    with pytest.raises(TraceDenied, match="receiver_ledger_torn_row"):
        state(c, p)
    assert ledger.read_bytes().endswith(b'{"truncated":')


def test_corrupted_existing_signature_does_not_reset_history(case):
    c, p = case
    append_receiver_receipt(receipt=c["receipt"], **p)
    ledger = p["root"] / NAME
    row = json.loads(ledger.read_text().strip())
    row["records"] = 200
    ledger.write_text(json.dumps(row) + "\n")
    with pytest.raises(TraceDenied, match="dual_custody_signature_invalid"):
        state(c, p)


def test_insecure_permissions_and_symlink_refuse(case, tmp_path):
    c, p = case
    p["root"].chmod(0o755)
    with pytest.raises(TraceDenied, match="unsafe_segment_directory"):
        append_receiver_receipt(receipt=c["receipt"], **p)
    p["root"].chmod(0o700)
    symlink = tmp_path / "bad-lock"
    symlink.write_text("attacker")
    (p["root"] / ".receiver-ledger.lock").symlink_to(symlink)
    with pytest.raises(TraceDenied, match="receiver_lock_unavailable"):
        append_receiver_receipt(receipt=c["receipt"], **p)


def test_insecure_existing_ledger_denied_without_overwrite(case):
    c, p = case
    append_receiver_receipt(receipt=c["receipt"], **p)
    ledger = p["root"] / NAME
    ledger.chmod(0o644)
    with pytest.raises(TraceDenied, match="segment_file_unsafe"):
        state(c, p)
    assert ledger.stat().st_mode & 0o777 == 0o644


def test_concurrent_same_external_genesis_only_one_wins(case):
    c, p = case

    def attempt(_):
        try:
            return append_receiver_receipt(receipt=c["receipt"], **p)["sequence"]
        except TraceDenied:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [0, 1]))
    assert sorted(map(str, results)) == ["1", "denied"]
    assert state(c, p) == (1, receipt_digest(c["receipt"]))


def test_unexpected_newline_json_and_nonautonomous_ack(case):
    c, p = case
    receipt = copy.deepcopy(c["receipt"])
    receipt["permit_production"] = True
    with pytest.raises(TraceDenied, match="dual_custody_schema_invalid"):
        append_receiver_receipt(receipt=receipt, **p)
    assert state(c, p) == (0, GENESIS)
