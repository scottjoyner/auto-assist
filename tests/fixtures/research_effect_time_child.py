#!/usr/bin/env python3
"""Explicitly opted-in research child only SIGKILLs its own PID."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/research-effect-time-term-fence.py"
SPEC = importlib.util.spec_from_file_location("effect_time_crash_worker", SCRIPT)
assert SPEC and SPEC.loader
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)


def run() -> int:
    if os.environ.get("ALLOW_SYNTHETIC_EFFECT_CHILD_KILL") != "TEST_CHILD_ONLY":
        return 4
    if len(sys.argv) != 3 or sys.argv[2] not in {
        "before_effect_commit", "after_commit_before_ack", "no_crash",
    }:
        return 5
    folder = Path(sys.argv[1])
    if (
        folder.is_symlink()
        or not (folder / ".synthetic-crash-lab-only").is_file()
        or (folder / ".synthetic-crash-lab-only").read_text() != "DISPOSABLE_ONLY\n"
    ):
        return 6
    request_path = folder / "research-effect-time-request.json"
    if request_path.is_symlink() or not request_path.is_file():
        return 7
    config = json.loads(request_path.read_text())

    def checkpoint(stage: str) -> None:
        if stage == sys.argv[2]:
            print("SELF_KILL_STAGE=" + stage, flush=True)
            os.kill(os.getpid(), signal.SIGKILL)

    success = lab.execute_once(
        folder / "synthetic-fenced-effects.sqlite",
        folder / "disposable-member" / "synthetic-a-owner.sqlite",
        folder / "research-resource.sqlite",
        folder / "research-anchor.sqlite",
        folder / "synthetic-resource-effect-v1.bin",
        expected=config["expected"],
        bundle_sha256=config["bundle_sha256"],
        previous_head_hash=config["previous_head_hash"],
        effect_root=base64.b64decode(config["effect_root"]),
        witness_root=base64.b64decode(config["witness_root"]),
        challenge=config["challenge"], now_ms=config["now_ms"],
        before_effect_commit=lambda: checkpoint("before_effect_commit"),
        after_commit=lambda: checkpoint("after_commit_before_ack"),
    )
    print(json.dumps({"research_only": success}), flush=True)
    return 0 if success == (True, "RESEARCH_LOCAL_EFFECT_TIME_FENCE_ONLY_HOLD") else 8


if __name__ == "__main__":
    raise SystemExit(run())
