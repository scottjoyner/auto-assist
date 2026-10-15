"""Source-only guardrails; actual PostgreSQL role proof is explicit opt-in.

These tests intentionally NEVER launch Docker or probe any real database.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/probe-disposable-postgres-acl.sh"
SQL = ROOT / "scripts/disposable-postgres-role-contract.sql"


def test_default_invocation_fails_without_docker_execution():
    env = {
        **os.environ,
        "PATH": "/usr/bin:/bin",
    }
    env.pop("ALLOW_DISPOSABLE_PG_LAB", None)
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True,
        timeout=6, check=False,
    )
    assert result.returncode != 0
    assert "BLOCKED explicit_synthetic_opt_in_required" in result.stderr
    assert "ISOLATED_START" not in result.stdout


def test_script_requires_offline_ephemeral_no_host_mounts():
    code = SCRIPT.read_text()
    for required in (
        "--network none", "--read-only", "--cap-drop ALL",
        "--security-opt no-new-privileges", "--pull never",
        "--tmpfs /var/lib/postgresql/data:",
        "--tmpfs /var/run/postgresql:",
        "--tmpfs /tmp:", "trap cleanup EXIT INT TERM",
        "--env POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
        "DENIED wrong_password",
        "POSTGRES_DISPOSABLE: PASS_ROLE_DENIAL_AND_COUNTEREXAMPLES_NO_GO",
    ):
        assert required in code
    for prohibited in (
        "--privileged", "--network host", "--publish", "--volume",
        "--mount type=bind", "docker compose up", "docker pull",
        "--restart always", "docker exec -it",
    ):
        assert prohibited not in code


def test_database_owner_and_anchor_principals_are_not_login_roles():
    sql = SQL.read_text()
    assert "CREATE ROLE lab_owner NOLOGIN;" in sql
    assert "CREATE ROLE lab_anchor_owner NOLOGIN;" in sql
    assert "ALTER TABLE lab.highwater OWNER TO lab_anchor_owner;" in sql


def test_public_execute_revoked_before_specific_operator_grant():
    sql = SQL.read_text()
    revoke = "REVOKE EXECUTE ON FUNCTION lab.apply_effect(bigint,bigint,bigint,text) FROM PUBLIC;"
    grant = "GRANT EXECUTE ON FUNCTION lab.apply_effect(bigint,bigint,bigint,text) TO lab_operator;"
    assert revoke in sql and grant in sql
    assert sql.index(revoke) < sql.index(grant)


def test_security_definer_uses_bounded_search_path_and_cas():
    sql = SQL.read_text()
    assert "SECURITY DEFINER SET search_path=pg_catalog" in sql
    assert "WHERE resource_id='auto-router-runtime-projection'" in sql
    assert "AND term=p_term AND commit_seq=p_expected_seq" in sql
    assert "RAISE EXCEPTION 'stale_term_or_sequence'" in sql
    assert "nonce text NOT NULL UNIQUE" in sql


def test_observer_only_receives_select_and_no_implicit_table_writes():
    sql = SQL.read_text()
    assert "GRANT SELECT ON lab.effect_state, lab.commit_receipts TO lab_observer;" in sql
    assert "GRANT SELECT ON lab.highwater TO lab_observer;" in sql
    assert "GRANT INSERT" not in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql


@pytest.mark.parametrize("name", ["lab_signer", "lab_verifier", "lab_operator", "lab_observer"])
def test_distinct_login_roles_have_no_superuser_or_role_grant(name):
    sql = SQL.read_text()
    assert f"CREATE ROLE {name} LOGIN PASSWORD :'" in sql
    role_statement = next(
        line for line in sql.splitlines() if line.startswith(f"CREATE ROLE {name} LOGIN")
    )
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;" in role_statement
