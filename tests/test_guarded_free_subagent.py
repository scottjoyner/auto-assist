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



def test_provider_preflight_requires_recent_healthy_catalog(tmp_path):
    import os
    p = tmp_path / 'presence.json'
    def put(status, checked):
        guard.atomic_json(p, {'schema': 'provider-presence/v1', 'providers': {
            'kilo_anonymous': {'status': status, 'checked_epoch': checked}}})
    assert guard.provider_preflight(p, now=1000)['reason'] == 'missing_presence_state'
    put('catalog_reachable', 900)
    assert guard.provider_preflight(p, now=1000)['allowed'] is True
    put('rate_limited', 1000)
    assert guard.provider_preflight(p, now=1000)['reason'] == 'provider_rate_limited'
    put('auth_or_billing_blocked', 1000)
    assert guard.provider_preflight(p, now=1000)['allowed'] is False
    put('catalog_reachable', -3000)
    assert guard.provider_preflight(p, now=1000)['reason'] == 'stale_presence'


def test_untrusted_provider_state_fails_closed(tmp_path):
    p = tmp_path / 'presence.json'
    p.write_text('{}')
    p.chmod(0o644)
    assert guard.provider_preflight(p)['reason'] == 'untrusted_presence_state'
    p.chmod(0o600)
    assert guard.provider_preflight(p)['reason'] == 'invalid_presence_state'


def test_provider_denial_records_decision_without_starting_agent(tmp_path):
    from types import SimpleNamespace
    args = SimpleNamespace(
        guard_root=tmp_path / 'guard',
        trace_dir=tmp_path / 'traces',
        state=tmp_path / 'state' / 'live.jsonl',
        title='denied-pilot', presence_state=tmp_path / 'absent.json')
    result = guard.run(args)
    assert result == {'status': 'blocked', 'reason': 'missing_presence_state'}
    audit = json.loads((args.guard_root / 'denied-pilot.admission.json').read_text())
    assert audit['provider'] == 'kilo_anonymous'
    assert audit['status'] == 'blocked'



def make_recent_db(path, rows):
    import sqlite3
    connection = sqlite3.connect(path)
    connection.execute(
        'CREATE TABLE session(id TEXT, model TEXT, tokens_input INTEGER, '
        'tokens_output INTEGER, cost REAL, time_updated INTEGER)')
    connection.executemany('INSERT INTO session VALUES (?,?,?,?,?,?)', rows)
    connection.commit()
    connection.close()


def test_recent_budget_dedupes_both_databases(tmp_path):
    a, b = tmp_path / 'a.db', tmp_path / 'b.db'
    model = json.dumps({'providerID': 'kilo_free', 'id': 'one:free'})
    row = ('session-1', model, 10000, 300, 0.0, 1000000)
    make_recent_db(a, [row])
    make_recent_db(b, [row])
    result = guard.recent_kilo_usage_preflight((a, b), now=1100)
    assert result['allowed'] is True
    assert result['sessions'] == 1
    assert result['input_tokens'] == 10000


def test_provider_burst_blocks_without_new_tokens(tmp_path):
    a, b = tmp_path / 'a.db', tmp_path / 'b.db'
    model = json.dumps({'providerID': 'kilo', 'id': 'two:free'})
    make_recent_db(a, [('session-2', model, 65000, 50, 0.0, 1000000)])
    make_recent_db(b, [])
    result = guard.recent_kilo_usage_preflight((a, b), now=1100)
    assert result['allowed'] is False
    assert result['reason'] == 'recent_provider_activity_cap'


def test_recent_budget_fails_closed_on_missing_db(tmp_path):
    result = guard.recent_kilo_usage_preflight(
        (tmp_path / 'missing.db',), now=1100)
    assert result['reason'] == 'usage_database_missing'


def test_recent_usage_cost_blocks(tmp_path):
    a = tmp_path / 'a.db'
    model = json.dumps({'providerID': 'kilo_free', 'id': 'free'})
    make_recent_db(a, [('session', model, 1, 1, 0.12, 1000000)])
    result = guard.recent_kilo_usage_preflight((a,), now=1100)
    assert result['reason'] == 'recent_nonzero_provider_cost'
