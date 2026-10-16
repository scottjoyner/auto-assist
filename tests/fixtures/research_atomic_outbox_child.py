#!/usr/bin/env python3
"""Test-only self-SIGKILL child, no private keys and no production paths."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/research-atomic-effect-bundle-outbox.py"
SPEC = importlib.util.spec_from_file_location("atomic_outbox_crash_worker", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def run() -> int:
    if os.environ.get("ALLOW_SYNTHETIC_OUTBOX_KILL") != "TEST_CHILD_ONLY":
        return 4
    if len(sys.argv) != 3 or sys.argv[2] not in (
        "after_outbox_insert_before_commit", "after_commit_before_ack", "no_crash"
    ):
        return 5
    directory = Path(sys.argv[1])
    if directory.is_symlink() or not (directory / ".synthetic-crash-lab-only").is_file():
        return 6
    request = directory / "research-atomic-outbox-request.json"
    if request.is_symlink() or not request.is_file():
        return 7
    data = json.loads(request.read_text())
    member = directory / "disposable-member" / "synthetic-a-owner.sqlite"
    effect = directory / "synthetic-resource-effect-v1.bin"
    resource = directory / "research-resource.sqlite"
    anchor = directory / "research-anchor.sqlite"
    if not (
        member.is_file() and resource.is_file()
        and anchor.is_file() and effect.is_file()
        and (member.parent / ".synthetic-crash-lab-only").is_file()
    ):
        return 8

    def checkpoint(stage: str):
        if stage == sys.argv[2]:
            print("SYNTHETIC_KILL_STAGE=" + stage, flush=True)
            os.kill(os.getpid(), signal.SIGKILL)

    result = mod.issue(
        member, witness_id="a-owner", tip=data["tip"], expected=data["expected"],
        resource=resource, anchor=anchor, artifact=effect, bundle=data["bundle"],
        effect_root=base64.b64decode(data["effect_root"]),
        witness_root=base64.b64decode(data["witness_root"]),
        challenge=data["challenge"], now_ms=data["now_ms"],
        callback=checkpoint,
    )
    print(json.dumps(result), flush=True)
    return 0 if result == (True, "RESEARCH_ATOMIC_OUTBOX_OBSERVED_HOLD") else 9


if __name__ == "__main__":
    raise SystemExit(run())
