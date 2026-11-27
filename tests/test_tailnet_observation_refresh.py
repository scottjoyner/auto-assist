"""Offline integration tests for a repeatable private observation refresh."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reconciliation-refresh-tailnet-models.sh"


def prepare(tmp_path: Path) -> tuple[Path, Path, Path]:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    input_file = tmp_path / "offline.json"
    input_file.write_text(json.dumps({
        "Self": {"ID": "test", "HostName": "offline-host", "Online": False,
                 "TailscaleIPs": ["100.70.0.1"]},
        "Peer": {},
    }), encoding="utf-8")
    return private, input_file, private / "witness.json"


def run_refresh(out: Path, input_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), "--output", str(out),
                           "--input", str(input_file)], text=True,
                          capture_output=True, timeout=15)
def test_refresh_is_idempotent_private_and_never_admits(tmp_path):
    private, input_file, output = prepare(tmp_path)
    for _ in range(2):
        result = run_refresh(output, input_file)
        assert result.returncode == 0, result.stderr
        witness = json.loads(output.read_text())
        assert witness["authority"] == "observational_only_not_runtime_admission"
        assert witness["observations"] == []
        assert witness["coverage"]["targets"] == 0
        assert output.stat().st_mode & 0o777 == 0o600
        receipt = output.with_suffix(".json.sha256")
        assert receipt.stat().st_mode & 0o777 == 0o600
        assert subprocess.run(["sha256sum", "-c", receipt.name],
                              cwd=private, capture_output=True).returncode == 0
        assert not list(private.glob(".observation-*/"))


def test_concurrent_refresh_fails_without_modifying_witness(tmp_path):
    private, input_file, output = prepare(tmp_path)
    assert run_refresh(output, input_file).returncode == 0
    before = output.read_bytes()
    with (private / ".observation-refresh.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_refresh(output, input_file)
        assert result.returncode == 75
    assert output.read_bytes() == before
def test_rejects_nonprivate_directory_and_symlink(tmp_path):
    private, input_file, output = prepare(tmp_path)
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    assert run_refresh(public / "witness.json", input_file).returncode == 65
    target = private / "target.json"
    link = private / "witness.json"
    link.symlink_to(target)
    assert run_refresh(link, input_file).returncode == 65
    assert not target.exists()