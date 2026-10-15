"""Static proof the two-cluster research lab cannot run implicitly.

No test in this module uses Docker, a service, network or credentials.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "scripts/probe-two-disposable-postgres-clusters.sh"
ANCHOR = ROOT / "scripts/disposable-external-highwater.sql"


def test_default_execution_fails_before_docker():
    env = os.environ.copy()
    env.pop("ALLOW_TWO_DISPOSABLE_PG_LABS", None)
    result = subprocess.run(
        ["bash", str(LAB)], env=env, capture_output=True, text=True,
        check=False, timeout=8,
    )
    assert result.returncode != 0
    assert "BLOCKED explicit_synthetic_opt_in_required" in result.stderr
    assert "ISOLATED_PAIR_START" not in result.stdout


def test_exact_two_isolated_networkless_volatile_containers_and_cleanup():
    body = LAB.read_text()
    for required in (
        "--pull never", "--network none", "--read-only",
        "--cap-drop ALL", "--security-opt no-new-privileges",
        "--pids-limit 64", "--memory 512m",
        "--tmpfs /var/lib/postgresql/data:",
        "--tmpfs /var/run/postgresql:",
        "--tmpfs /tmp:", "trap cleanup EXIT INT TERM",
        "created_resource=1", "created_anchor=1",
        "docker rm -f", "none|true|0|{}",
    ):
        assert required in body
    for forbidden in (
        "--privileged", "--network host", "--publish", "--volume",
        "--mount type=bind", "docker pull", "docker compose up",
        "--restart unless-stopped",
    ):
        assert forbidden not in body


def test_correct_scram_bootstrap_and_cross_cluster_identity_denials():
    body = LAB.read_text()
    assert "--env POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256" in body
    assert "--env POSTGRES_HOST_AUTH_METHOD=scram-sha-256" in body
    for required in (
        "resource_operator_to_anchor_writer",
        "resource_observer_to_anchor_reader",
        "anchor_writer_to_resource_operator",
        "anchor_reader_to_resource_observer",
        "resource_superuser_password_on_anchor",
        "resource_signer_cross_cluster_role_absent",
    ):
        assert required in body


def test_separate_database_resource_and_anchor_principals():
    sql = ANCHOR.read_text()
    assert "CREATE ROLE hw_owner NOLOGIN;" in sql
    assert "CREATE ROLE hw_writer LOGIN PASSWORD :'writer_password'" in sql
    assert "CREATE ROLE hw_reader LOGIN PASSWORD :'reader_password'" in sql
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;" in sql
    assert "CREATE TABLE hw.highwater" in sql
    assert "CREATE TABLE lab.effect_state" not in sql


def test_anchor_security_definer_and_explicit_public_execute_revoke():
    sql = ANCHOR.read_text()
    assert "SECURITY DEFINER SET search_path=pg_catalog" in sql
    revoke = "REVOKE EXECUTE ON FUNCTION hw.advance(bigint,bigint,bigint,bigint,text) FROM PUBLIC;"
    grant = "GRANT EXECUTE ON FUNCTION hw.advance(bigint,bigint,bigint,bigint,text) TO hw_writer;"
    assert revoke in sql and grant in sql
    assert sql.index(revoke) < sql.index(grant)
    assert "GRANT SELECT ON hw.highwater TO hw_reader;" in sql
    assert "GRANT UPDATE" not in sql


@pytest.mark.parametrize("token", [
    "p_expected_seq", "p_new_term", "p_new_seq", "p_new_nonce",
])
def test_anchor_update_cas_contract_contains_expected_fields(token):
    sql = ANCHOR.read_text()
    assert token in sql
    assert "AND term=p_term AND commit_seq=p_expected_seq" in sql
    assert "ROW(p_new_term,p_new_seq) <= ROW(p_term,p_expected_seq)" in sql
    assert "RAISE EXCEPTION 'anchor_stale_or_restored'" in sql


def test_resource_restore_surviving_anchor_and_anchor_restore_no_go_both_tested():
    body = LAB.read_text()
    assert "DENIED resource_rollback_detectable_from_independent_anchor" in body
    assert "NO_GO shared_host_administrator_can_restore_separate_anchor" in body
    assert "NO_GO resource_dba_fabricates_receipt_even_with_separate_anchor" in body


def test_physical_runner_never_claims_production_authorization():
    body = LAB.read_text()
    assert "TWO_PG_LABS: PASS_ROLE_ISOLATION_WITH_SHARED_HOST_NO_GO" in body
    assert "production" not in body.lower().split('TWO_PG_LABS: PASS_ROLE_ISOLATION_WITH_SHARED_HOST_NO_GO')[-1]
