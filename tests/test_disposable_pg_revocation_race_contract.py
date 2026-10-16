"""Source-only guards for disposable Postgres revocation/execute races.

No pytest test starts Docker. Opt-in physical runner is invoked separately
and contains intentional revoker-custody NO-GOs.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/probe-disposable-pg-effect-authority.sh"
SCHEMA = ROOT / "scripts/disposable-pg-effect-time-authority.sql"


def test_race_runner_still_blocks_by_default():
    process = subprocess.run(
        ["bash", str(RUNNER)], check=False, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}, timeout=5,
    )
    assert process.returncode != 0
    assert "BLOCKED explicit_synthetic_opt_in_required" in process.stderr


def test_race_runner_bash_is_valid():
    subprocess.run(["bash", "-n", str(RUNNER)], check=True, timeout=5)


def test_races_use_same_real_pg_authority_row_lock():
    sql = SCHEMA.read_text()
    runner = RUNNER.read_text()
    assert "FROM fx.authority WHERE resource_id='auto-router-runtime-projection'" in sql
    assert "FOR UPDATE" in sql
    assert "UPDATE fx.authority SET term=p_new_term" in sql
    assert "WHERE resource_id='auto-router-runtime-projection'" in sql
    assert "SELECT fx.rotate(" in runner
    assert "SELECT fx.execute_effect(" in runner


def test_revocation_first_race_has_proven_waiter_and_denied_effect():
    script = RUNNER.read_text()
    for phrase in (
        "REVOKER_LOCK_HELD", "pg_sleep(3)",
        "usename='fx_executor' AND wait_event_type='Lock'",
        "executor_lock_wait_unobserved", "stale_executor_won_after_revocation",
        "SELECT count(*) FROM fx.applied WHERE commit_seq=18",
        "postgres_revocation_wins_row_lock_effect_denied",
    ):
        assert phrase in script


def test_execution_first_race_ensures_revocation_waiter():
    script = RUNNER.read_text()
    for phrase in (
        "EXECUTOR_LOCK_HELD",
        "usename='fx_revoker' AND wait_event_type='Lock'",
        "revoker_lock_wait_unobserved",
        "SELECT count(*) FROM fx.applied WHERE commit_seq=19",
        "postgres_effect_wins_row_lock_revocation_prospective_only",
    ):
        assert phrase in script


def test_two_revoker_misuse_counterexamples_explicit():
    script = RUNNER.read_text()
    assert "restricted_revoker_can_assign_arbitrary_owner_without_signed_grant" in script
    assert "restricted_revoker_monotonic_epoch_can_reauthorize_previously_revoked_owner" in script
    assert re.search(r"fx\.rotate\(4,9,7,12,'arbitrary-owner',false\)", script)
    assert re.search(r"fx\.rotate\(12,17,13,18,'owner-a',false\)", script)
    # Both arbitrary claims still lead to successful executor calls.
    assert "fx.execute_effect(7,12,'arbitrary-owner'" in script
    assert "fx.execute_effect(13,18,'owner-a'" in script


def test_controlled_container_and_temp_cleanup_survive_failing_race():
    script = RUNNER.read_text()
    assert "trap cleanup EXIT INT TERM" in script
    assert "trap 'cleanup_race; cleanup' EXIT INT TERM" in script
    assert "docker rm -f" in script
    assert "rm -rf -- \"$race_scratch\"" in script
    assert "--network none" in script
    assert "--read-only" in script
    assert "--pull never" in script
    assert "secrets.token_hex(24)" in script


def test_race_does_not_copy_real_secrets_to_debug_files():
    script = RUNNER.read_text()
    assert "echo \"$admin_secret\"" not in script
    assert "echo \"$revoker_secret\"" not in script
    assert "echo \"$executor_secret\"" not in script
    assert "kubectl " not in script
    assert "ssh " not in script
    assert "--mount " not in script and "--volume " not in script


def test_distinct_sequence_and_nonce_controls_prevent_accidental_collisions():
    script = RUNNER.read_text()
    assert "fx.execute_effect(11,16,'owner-a',19,repeat('3',32)" in script
    assert "fx.execute_effect(13,18,'owner-a',20,repeat('2',32)" in script
    assert "duplicate_execution" in script
    assert "real_concurrent_executor_connections_one_effect_winner" in script


def test_output_cannot_claim_production_authority_or_go():
    script = RUNNER.read_text()
    assert "PASS_RESTRICTED_ROLES_AND_RACE_ORDER_WITH_CUSTODY_NO_GO" in script
    assert "PRODUCTION_GO" not in script
    assert "release_authorized=true" not in script
