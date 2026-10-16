"""Source-only opt-in and privilege boundary checks; no Docker runs in pytest."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "scripts/disposable-pg-effect-time-authority.sql"
RUNNER = ROOT / "scripts/probe-disposable-pg-effect-authority.sh"


@pytest.fixture(scope="module")
def script():
    return RUNNER.read_text()


@pytest.fixture(scope="module")
def sql():
    return SCHEMA.read_text()


def test_research_files_are_self_contained():
    assert RUNNER.is_file() and SCHEMA.is_file()


def test_optin_flag_is_exact_string(script):
    assert 'ALLOW_DISPOSABLE_PG_EFFECT_AUTHORITY' in script
    assert '"YES_SYNTHETIC_ONLY"' in script
    assert "explicit_synthetic_opt_in_required" in script


def test_default_run_is_denied_before_docker(script):
    assert script.index('explicit_synthetic_opt_in_required') < script.index('command -v docker')
    result = subprocess.run(
        ["bash", str(RUNNER)],
        capture_output=True, text=True, timeout=8, check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
    )
    assert result.returncode != 0
    assert "BLOCKED explicit_synthetic_opt_in_required" in result.stderr


def test_shell_syntax():
    subprocess.run(["bash", "-n", str(RUNNER)], check=True, timeout=5)


def test_no_network_no_host_volumes_no_image_pull(script):
    assert '--network none' in script
    assert '--read-only' in script
    assert '--cap-drop ALL' in script
    assert '--security-opt no-new-privileges' in script
    assert '--pull never' in script
    assert 'image=postgres:17-alpine' in script
    assert '--tmpfs /var/lib/postgresql/data' in script
    assert re.search(r'--pids-limit\s+64', script)
    assert re.search(r'--memory\s+512m', script)
    assert '--volume' not in script and '--mount' not in script
    assert 'docker pull ' not in script


def test_every_temp_database_container_cleanup_installed(script):
    assert 'trap cleanup EXIT INT TERM' in script
    assert 'docker rm -f "$name"' in script
    assert 'research.only=disposable-fx-authority' in script


def test_ephemeral_synthetic_secrets_never_literal_or_printed(script):
    assert "secrets.token_hex(24)" in script
    assert 'echo "$executor_secret"' not in script
    assert 'echo "$admin_secret"' not in script
    assert "source .env" not in script
    assert '--env PGPASSWORD="$secret"' in script


def test_postgres_host_authentication_explicitly_scram(script):
    assert '--auth-host=scram-sha-256' in script
    assert '--env POSTGRES_HOST_AUTH_METHOD=scram-sha-256' in script
    assert 'psql -h 127.0.0.1 -U "$role"' in script


def test_no_prod_staging_host_or_kube_action(script):
    assert 'kubectl ' not in script
    assert 'ssh ' not in script
    assert 'docker compose ' not in script
    assert 'deploy' not in script.lower()


def test_owner_role_nonlogin_and_all_callers_nonsuperuser(sql):
    assert "CREATE ROLE fx_owner NOLOGIN;" in sql
    for role in ("fx_executor", "fx_observer", "fx_reviewer", "fx_revoker"):
        assert f"CREATE ROLE {role} LOGIN" in sql
    assert sql.count("NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT") == 4


def test_no_public_schema_or_table_privileges(sql):
    assert "REVOKE ALL ON SCHEMA fx FROM PUBLIC" in sql
    assert "REVOKE ALL ON fx.authority,fx.applied FROM PUBLIC" in sql
    assert "GRANT INSERT ON" not in sql
    assert "GRANT UPDATE ON" not in sql
    assert "GRANT DELETE ON" not in sql


def test_execution_authz_through_narrow_stored_function_only(sql):
    assert "SECURITY DEFINER SET search_path=pg_catalog" in sql
    assert "FOR UPDATE" in sql
    assert "INSERT INTO fx.applied VALUES" in sql
    assert "GRANT EXECUTE ON FUNCTION fx.execute_effect" in sql
    assert "TO fx_executor" in sql
    assert "GRANT EXECUTE ON FUNCTION fx.rotate" in sql
    assert "TO fx_revoker" in sql


def test_public_execute_grants_revoked(sql):
    assert "REVOKE EXECUTE ON FUNCTION fx.execute_effect" in sql
    assert "REVOKE EXECUTE ON FUNCTION fx.rotate" in sql


def test_apply_checks_term_epoch_owner_revocation_during_lock(sql):
    for term in ("current_row.term != p_term", "current_row.epoch != p_epoch",
                 "current_row.owner_id != p_owner", "current_row.revoked"):
        assert term in sql
    assert "stale_or_revoked_at_effect_time" in sql


def test_apply_unique_effect_identity(sql):
    assert "commit_seq bigint PRIMARY KEY" in sql
    assert "nonce text NOT NULL UNIQUE" in sql


def test_revoker_monotonic_expected_cas(sql):
    assert "ROW(p_new_term,p_new_epoch) <= ROW(p_expected_term,p_expected_epoch)" in sql
    assert "term=p_expected_term AND epoch=p_expected_epoch" in sql
    assert "stale_authority_advance" in sql


def test_role_denial_runtime_witnesses_explicit(script):
    for marker in (
        "executor_direct_forge", "executor_direct_revocation_rewind",
        "revoker_direct_forge", "observer_execute", "reviewer_revoke",
        "executor_revoke", "revoker_execute", "duplicate_execution",
        "stale_owner_term", "revoked_owner", "nonmonotonic_revoke",
    ):
        assert marker in script


def test_physical_concurrent_effect_connections_one_winner_test(script):
    assert "real_concurrent_executor_connections_one_effect_winner" in script
    assert "SELECT count(*) FROM fx.applied WHERE commit_seq=16" in script


def test_superuser_bypass_positively_exercised_not_hidden(script):
    for marker in (
        "database_superuser_forges_applied_row_bypassing_executor",
        "database_superuser_rewinds_authority_and_reopens_old_receipt",
        "executor_can_claim_arbitrary_digest_without_resource_attestation",
    ):
        assert marker in script
    assert "admin_sql \"INSERT INTO fx.applied VALUES" in script
    assert "admin_sql \"UPDATE fx.authority SET" in script


def test_runtime_cannot_emit_go_label(script):
    assert "PASS_RESTRICTED_ROLES_WITH_SUPERUSER_AND_PROVENANCE_NO_GO" in script
    assert "PRODUCTION_GO" not in script
    assert "production_release_authorized" not in script


def test_sql_has_no_production_table_names(sql):
    assert "CREATE SCHEMA fx" in sql
    assert "public.resource_effect" not in sql
    assert "neo4j" not in sql.lower()
