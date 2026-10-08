"""Offline tests for guarded free-agent admission and event-only budget counting."""
import argparse
import importlib.util
import json
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "guarded_free_subagent.py"
SPEC = importlib.util.spec_from_file_location("guarded_free_subagent", MODULE)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

def test_scan_ignores_literal_429_and_counts_real_error(tmp_path):
    events = [
        {"type": "tool_use", "part": {"state": {"output": "429 quota overloaded"}}},
        {"type": "step_finish", "part": {"tokens": {"input": 700, "output": 100, "reasoning": 25}}},
        {"type": "error", "error": {"data": {"statusCode": 429}}},
    ]
    path = tmp_path / "sample.jsonl"
    path.write_text("\n".join(map(json.dumps, events)) + "\n")
    summary = guard.scan_trace(path)
    assert summary["usage"] == {"input": 700, "output": 100, "reasoning": 25, "steps": 1, "tools": 1}
    assert summary["errors"] == {"429": 1}

def test_empty_scan_and_invalid_json(tmp_path):
    path = tmp_path / "trace.jsonl"
    assert guard.scan_trace(path)["usage"]["input"] == 0
    path.write_text("not JSON\n")
    assert guard.scan_trace(path)["errors"] == {}

def test_cooldown_blocks_without_launch(tmp_path):
    state_dir = tmp_path / "guard"
    state_dir.mkdir()
    import time
    guard.atomic_json(state_dir / "kilo_free.json", {"next_allowed_epoch": time.time() + 200})
    args = argparse.Namespace(guard_root=state_dir)
    report = guard.run(args)
    assert report["status"] == "blocked"
    assert report["reason"] == "cooldown"

def test_single_slot_prevents_parallel_run(tmp_path):
    import fcntl
    state_dir = tmp_path / "guard"
    state_dir.mkdir()
    with (state_dir / "kilo_free.lock").open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        args = argparse.Namespace(guard_root=state_dir)
        report = guard.run(args)
        assert report["status"] == "blocked"
        assert report["reason"] == "concurrent_guarded_run"


def test_non_object_error_data_does_not_crash(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text(json.dumps({"type": "error", "error": {"data": "unexpected"}}) + "\n")
    assert guard.scan_trace(path)["errors"] == {"unknown": 1}

def test_negative_tokens_cannot_reduce_budget(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in [
        {"type": "step_finish", "part": {"tokens": {"input": 500, "output": 5}}},
        {"type": "step_finish", "part": {"tokens": {"input": -5000, "output": -100}}},
    ]) + "\n")
    assert guard.scan_trace(path)["usage"]["input"] == 500
    assert guard.scan_trace(path)["usage"]["output"] == 5

def test_account_home_is_stable_for_admission():
    import os
    import pwd
    assert guard.DEFAULT_ROOT == Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/state/auto-assist/guarded-free"

def test_malformed_event_shapes_do_not_disable_guard(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in [
        [], {"type": "step_finish", "part": "bad"},
        {"type": "step_finish", "part": {"tokens": "invalid"}},
        {"type": "tool_use", "part": None},
    ]) + "\n")
    summary = guard.scan_trace(path)
    assert summary["usage"]["steps"] == 2
    assert summary["usage"]["tools"] == 1

def test_existing_trace_is_not_reused(tmp_path):
    args = argparse.Namespace(guard_root=tmp_path / "guard",
        trace_dir=tmp_path / "traces", state=tmp_path / "state" / "live.jsonl",
        title="pilot-1")
    args.trace_dir.mkdir()
    (args.trace_dir / "pilot-1.stdout.jsonl").write_text("{}\n")
    report = guard.run(args)
    assert report == {"status": "blocked", "reason": "trace_name_already_exists"}
