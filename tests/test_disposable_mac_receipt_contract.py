"""Source-only, no-Docker tests for a disposable resource-bound receipt gate."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/probe-disposable-commit-bound-receipts.sh"
RESOURCE = ROOT / "scripts/disposable-resource-mac-receipts.sql"
ANCHOR = ROOT / "scripts/disposable-anchor-mac-receipts.sql"


def test_non_opt_in_refuses_before_containers():
    env = os.environ.copy()
    env.pop("ALLOW_DISPOSABLE_RECEIPT_LAB", None)
    result = subprocess.run(
        ["bash", str(RUNNER)], env=env, capture_output=True,
        text=True, check=False, timeout=6,
    )
    assert result.returncode != 0
    assert "BLOCKED explicit_synthetic_opt_in_required" in result.stderr
    assert "ISOLATED_PAIR_START" not in result.stdout


def test_two_isolated_restricted_disposable_clusters_are_required():
    text = RUNNER.read_text()
    for required in (
        "--pull never", "--network none", "--read-only", "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--tmpfs /var/lib/postgresql/data:", "--tmpfs /var/run/postgresql:",
        "trap cleanup EXIT INT TERM", "created_resource=1", "created_anchor=1",
        "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
        "none|true|0|{}",
    ):
        assert required in text
    for forbidden in (
        "--network host", "--privileged", "--publish", "--volume",
        "--mount type=bind", "docker pull", "docker compose up",
    ):
        assert forbidden not in text


def test_source_commit_and_mac_are_in_same_resource_transaction():
    sql = RESOURCE.read_text()
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in sql
    assert "CREATE FUNCTION lab.commit_with_mac(" in sql
    assert "SECURITY DEFINER SET search_path=pg_catalog" in sql
    assert "UPDATE lab.effect_state" in sql
    assert "INSERT INTO lab.commit_receipts" in sql
    assert "receipt_mac" in sql
    assert "public.hmac(" in sql
    assert "receipt_mac SET NOT NULL" in sql
    assert "stale_commit_sequence" in sql


def test_unverified_old_entrypoints_explicitly_revoked():
    assert ("REVOKE EXECUTE ON FUNCTION lab.apply_effect" in RESOURCE.read_text())
    assert ("REVOKE EXECUTE ON FUNCTION hw.advance(" in ANCHOR.read_text())
    assert '"legacy_unverified_anchor_function"' in RUNNER.read_text()
    assert '"legacy_unprotected_resource_commit"' in RUNNER.read_text()


def test_resource_and_anchor_proof_secrets_are_owner_only():
    resource = RESOURCE.read_text()
    anchor = ANCHOR.read_text()
    assert "CREATE TABLE lab.receipt_mac_key" in resource
    assert "CREATE TABLE hw.receipt_verifier_key" in anchor
    assert "GRANT SELECT ON lab.receipt_mac_key" not in resource
    assert "GRANT SELECT ON hw.receipt_verifier_key" not in anchor
    assert "REVOKE EXECUTE ON FUNCTION lab.commit_with_mac" in resource
    assert "REVOKE EXECUTE ON FUNCTION hw.advance_verified_receipt" in anchor


def test_anchor_validates_receipt_before_cas_mutation():
    sql = ANCHOR.read_text()
    assert "unverified_resource_commit_receipt" in sql
    assert sql.index("signed_value != p_receipt_mac") < sql.index("UPDATE hw.highwater")
    assert "p_new_seq != p_expected_seq + 1" in sql
    assert "term=p_term AND commit_seq=p_expected_seq" in sql
    assert "anchor_cas_stale" in sql


@pytest.mark.parametrize("token", [
    "convergence-receipt-hmac-v1", "auto-router-runtime-projection",
    "p_new_seq::text", "p_gen::text", "p_sum", "p_revision", "p_nonce",
])
def test_resource_and_anchor_bound_domain_components_present(token):
    assert token in ANCHOR.read_text()
    if token in ("convergence-receipt-hmac-v1", "auto-router-runtime-projection"):
        assert token in RESOURCE.read_text()


def test_runner_requires_missing_receipt_tamper_replay_denials():
    text = RUNNER.read_text()
    for required in (
        "DENIED anchor_advance_without_valid_resource_receipt",
        "DENIED wrong_effect_binding_and_nonadjacent_sequence",
        "PASS genuine_resource_receipt_verified_and_atomic_anchor_advance",
        "DENIED repeat_receipt_from_old_anchor_position",
        "PASS_RESTRICTED_CONTRACT_WITH_SHARED_CUSTODY_NO_GO",
        "anchor_writer_proof_key_access", "resource_operator_proof_key_access",
    ):
        assert required in text


def test_no_production_crypto_or_release_effects_are_added():
    text = RUNNER.read_text()
    assert "ALLOW_DISPOSABLE_RECEIPT_LAB" in text
    assert "POSTGRES_PASSWORD" in text
    assert "postgres:17-alpine" in text
    assert "systemctl " not in text
    assert "kubectl " not in text
    assert "terraform " not in text
