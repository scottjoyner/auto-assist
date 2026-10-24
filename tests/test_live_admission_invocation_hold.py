"""Synthetic-only invocation safety for draft live-admission runner.

Never invoke --apply without DRY_RUN in tests. No model/API/graph calls.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assistx-fleet-admit-live.py"


def _rejected(*args: str, dry_run: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("DRY_RUN", None)
    if dry_run is not None:
        env["DRY_RUN"] = dry_run
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=8,
        check=False,
    )
    assert result.returncode == 2
    assert "FLEET_ADMISSION_BLOCKED:" in result.stderr
    assert "FLEET_ADMISSION_REFRESHED" not in result.stdout
    return result


def test_runner_without_apply_exits_before_any_live_work():
    _rejected()


def test_runner_with_unrecognized_flag_exits_before_any_live_work():
    _rejected("--help")


def test_misleading_dry_run_is_rejected_even_when_apply_was_requested():
    _rejected("--apply", dry_run="1")


def test_any_nonempty_dry_run_marker_is_rejected_even_with_apply():
    _rejected("--apply", dry_run="true")


def test_apply_alone_is_denied_without_any_model_or_runtime_effect():
    result = _rejected("--apply")
    assert "--apply alone cannot authorize live model canaries" in result.stderr
    assert "FLEET_ADMISSION_REFRESHED" not in result.stdout


def test_legacy_chat_directive_cannot_be_unblocked_by_env_marker():
    # The only previously admitted argv was --apply; the hold is unconditional,
    # not an environment toggle or caller-supplied boolean.
    env = os.environ.copy()
    env.pop("DRY_RUN", None)
    env["FLEET_APPROVED"] = "true"
    env["ALLOW_LIVE_PROVIDER"] = "1"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--apply"], cwd=ROOT, env=env,
        text=True, capture_output=True, check=False, timeout=8,
    )
    assert result.returncode == 2
    assert "independent resource-owned approval" in result.stderr


def test_apply_guard_precedes_state_and_egress():
    text = SCRIPT.read_text()
    release_gate = text.index("# RELEASE HOLD:")
    block = text.index('raise SystemExit(2)', release_gate)
    state_write = text.index("STATE.mkdir(parents=True, exist_ok=True)")
    assert release_gate < block < state_write
    assert block < text.index("completion_canary(LAN_BASE)")
    assert block < text.index('approve_script.read_bytes()')
    assert block < text.index('urllib.request.urlopen("http://127.0.0.1:8088/health"')


def test_guard_precedes_state_creation_and_all_live_calls():
    text = SCRIPT.read_text()
    guard = text.index('if sys.argv[1:] != ["--apply"] or os.environ.get("DRY_RUN"):')
    assert guard < text.index("STATE.mkdir(parents=True, exist_ok=True)")
    assert guard < text.index('lock = (STATE / "renew.lock").open("w")')
    assert guard < text.index("completion_canary(LAN_BASE)")
    assert guard < text.index("approve_script.read_bytes()")
    assert 'if os.getenv("DRY_RUN") == "1":' not in text
