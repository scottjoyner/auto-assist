"""Source-only contracts for live opt-in PostgreSQL issuer/executor experiment.

Never launches Docker in hosted pytest. Physical checks only via explicit
ALLOW_DISPOSABLE_PG_EFFECT_ADMISSION=YES_SYNTHETIC_ONLY runner.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "scripts/disposable-pg-effect-admission.sql"
RUNNER = ROOT / "scripts/probe-disposable-pg-effect-admission.sh"


@pytest.fixture(scope="module")
def sql():
    return SCHEMA.read_text()


@pytest.fixture(scope="module")
def runner():
    return RUNNER.read_text()


def test_files_are_present():
    assert SCHEMA.is_file()
    assert RUNNER.is_file()


def test_opt_in_required_before_docker(runner):
    assert 'ALLOW_DISPOSABLE_PG_EFFECT_ADMISSION' in runner
    assert '"YES_SYNTHETIC_ONLY"' in runner
    assert runner.index("explicit_synthetic_opt_in_required") < runner.index("command -v docker")


def test_default_denial_does_not_start_container():
    r = subprocess.run(
        ["bash", str(RUNNER)],
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
        capture_output=True, text=True, timeout=8, check=False,
    )
    assert r.returncode != 0
    assert "BLOCKED explicit_synthetic_opt_in_required" in r.stderr


def test_bash_syntax():
    subprocess.run(["bash", "-n", str(RUNNER)], check=True, timeout=6)


def test_physical_isolation_no_network_no_mounts(runner):
    for fragment in (
        "--pull never", "--network none", "--read-only", "--cap-drop ALL",
        "--security-opt no-new-privileges", "--pids-limit 64",
        "--memory 512m", "--cpus 1.5", "--tmpfs /var/lib/postgresql/data",
        "--tmpfs /var/run/postgresql", "none|true|0|{}",
    ):
        assert fragment in runner
    assert "--volume" not in runner and "--mount" not in runner
    assert "docker pull " not in runner


def test_labeled_ephemeral_container_always_removed(runner):
    assert "trap cleanup EXIT INT TERM" in runner
    assert 'docker rm -f "$name"' in runner
    assert "research.only=disposable-pg-effect-admission" in runner


def test_cached_image_and_scram(runner):
    assert "image=postgres:17-alpine" in runner
    assert "--auth-host=scram-sha-256" in runner
    assert "--env POSTGRES_HOST_AUTH_METHOD=scram-sha-256" in runner
    assert "docker image inspect" in runner


def test_distinct_transient_passwords_no_sourcing(runner):
    assert "secrets.token_hex(24)" in runner
    assert '[[ "$issuer_pw" != "$executor_pw"' in runner
    assert 'echo "$issuer_pw"' not in runner
    assert 'echo "$admin_pw"' not in runner
    assert "source .env" not in runner


def test_separate_issuer_without_superuser(sql):
    assert "CREATE ROLE fx_issuer LOGIN PASSWORD" in sql
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT" in sql
    assert "GRANT USAGE ON SCHEMA fx TO fx_issuer" in sql


def test_authorization_table_private(sql):
    assert "CREATE TABLE fx.authorizations" in sql
    assert "commit_seq bigint PRIMARY KEY" in sql
    assert "nonce text NOT NULL UNIQUE" in sql
    assert "spent boolean NOT NULL DEFAULT false" in sql
    assert "REVOKE ALL ON fx.authorizations FROM PUBLIC" in sql
    assert "GRANT SELECT ON fx.authorizations TO fx_observer,fx_reviewer" in sql


def test_legacy_arbitrary_executor_path_revoked(sql):
    assert re.search(r"REVOKE EXECUTE ON FUNCTION fx\.execute_effect\(", sql)
    assert "FROM fx_executor;" in sql


def test_only_issuer_may_issue(sql):
    assert "GRANT EXECUTE ON FUNCTION fx.issue_claim" in sql
    assert "TO fx_issuer;" in sql
    assert "REVOKE EXECUTE ON FUNCTION fx.issue_claim" in sql


def test_only_executor_may_consume(sql):
    assert "GRANT EXECUTE ON FUNCTION fx.execute_issued" in sql
    assert "TO fx_executor;" in sql
    assert "REVOKE EXECUTE ON FUNCTION fx.execute_issued" in sql


def test_definer_search_path_is_fixed(sql):
    assert sql.count("SECURITY DEFINER") == 2
    assert sql.count("SET search_path=pg_catalog") == 2
    assert "FROM fx.authority" in sql
    assert "UPDATE fx.authorizations SET spent=true" in sql
    assert "INSERT INTO fx.applied" in sql


def test_issuer_checks_term_epoch_owner_revocation_under_lock(sql):
    assert sql.count("FOR UPDATE") == 2
    for claim in (
        "authority_row.term != p_term",
        "authority_row.epoch != p_epoch",
        "authority_row.owner_id != p_owner",
        "authority_row.revoked",
        "issuer_stale_or_revoked",
    ):
        assert claim in sql


def test_executor_checks_term_epoch_owner_revocation_under_lock(sql):
    assert "executor_stale_or_revoked" in sql
    assert "issuer_receipt_missing_or_spent" in sql
    assert "AND spent=false RETURNING commit_seq" in sql


def test_claim_has_exact_receipt_identity_and_digest(sql):
    for expression in (
        "commit_seq=p_seq", "term=p_term", "epoch=p_epoch",
        "owner_id=p_owner", "nonce=p_nonce", "digest=p_digest",
    ):
        assert expression in sql
    assert "'^[0-9a-f]{64}$'" in sql
    assert "'^[0-9a-f]{32}$'" in sql


def test_atomic_consumption_and_effect_in_one_definer_function(sql):
    start = sql.index("CREATE FUNCTION fx.execute_issued")
    end = sql.index("$FUNC$;", start)
    part = sql[start:end]
    assert part.index("UPDATE fx.authorizations SET spent=true") < part.index("INSERT INTO fx.applied VALUES")
    assert "RETURN admitted;" in part


def test_runtime_has_wrong_digest_nonce_negative_probes(runner):
    for marker in (
        "missing_issuer_receipt", "duplicate_admission", "substituted_digest",
        "substituted_nonce", "already_consumed_receipt",
        "denied_calls_consumed_receipt", "executor_cannot_issue",
        "issuer_cannot_execute", "executor_no_direct_unspend",
    ):
        assert marker in runner


def test_revoke_after_issue_and_wrong_term_denied(runner):
    assert "revocation_after_issuance" in runner
    assert "stale_consumption" in runner
    assert "wrong_term_after_regrant" in runner


def test_true_separate_executor_connections_concurrent_one_winner(runner):
    assert "actual_concurrent_executor_connections_single_consumption" in runner
    assert 'SELECT count(*) FROM fx.applied WHERE commit_seq=14' in runner
    assert 'wait "$first";wait "$second"' in runner


def test_fabricated_source_commit_cannot_be_considered_proven(runner):
    assert "issuer_can_authorize_fictional_digest_without_real_resource_commit" in runner
    assert "superuser_can_fabricate_effect_without_issuer" in runner
    assert "superuser_rewinds_owner_term_and_reopens_stale_epoch" in runner


def test_no_prod_network_or_remote_actions(runner):
    for prohibited in ("ssh ", "kubectl ", "docker compose ", "docker pull "):
        assert prohibited not in runner
    assert "SYNTHETIC_ONLY" in runner


def test_no_release_or_promotion_label(runner):
    assert "PASS_RESTRICTED_ISSUER_WITH_COMMON_ADMIN_NO_GO" in runner
    assert "PRODUCTION_GO" not in runner
    assert "release_authorized=true" not in runner
