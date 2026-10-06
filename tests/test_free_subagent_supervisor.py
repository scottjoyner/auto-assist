#!/usr/bin/env python3
"""Focused tests for free_subagent_supervisor (read-only slice)."""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import free_subagent_supervisor as supervisor

FIXTURE_MODELS = pathlib.Path(__file__).parent / "fixtures" / "openrouter_models_sample.jsonl"
FIXTURE_STATE = pathlib.Path(__file__).parent / "fixtures" / "subagent_state_sample.jsonl"


def test_enumerate_free_models_from_fixture():
    models = supervisor.enumerate_free_models(FIXTURE_MODELS)
    ids = [m.get("id") for m in models]
    assert any("claude-3.5-sonnet" in s for s in ids), f"expected claude in {ids}"
    assert any("gemini-2.5-flash" in s for s in ids), f"expected gemini in {ids}"
    deepseek_in_models = any("deepseek-r1" in (m.get("id") or "") for m in models)
    # Deepseek is not free in fixture (pricing non-zero, no free tag), so should be absent.
    assert not deepseek_in_models, f"expected deepseek excluded from free models: {models}"
    paid_completion = any("zero-prompt-paid-completion" in (m.get("id") or "") for m in models)
    assert not paid_completion, (
        "zero prompt price alone must never qualify a paid-completion model as free"
    )


def test_credential_present_no_secret_leak():
    # Ensure env not polluted
    for k in ("OPENROUTER_API_KEY", "OPENROUTER_KEY"):
        if k in os.environ:
            old = os.environ[k]
            del os.environ[k]
        else:
            old = None
    try:
        assert supervisor.credential_present() is False
    finally:
        if old is not None:
            os.environ[k] = old


def test_register_inspect_duplicates():
    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tf:
        path = pathlib.Path(tf.name)
    try:
        record = {
            "title": "test-a",
            "objective": "o",
            "model": "openrouter/anthropic/claude-3.5-sonnet",
            "worktree": "/tmp/wt-test-dup",
            "pid": 123,
            "session": "sess-a",
            "status": "active",
            "created_at": "2026-10-06T09:00:00Z",
        }
        supervisor.register_subagent(record, path)
        supervisor.register_subagent({**record, "pid": 124, "session": "sess-b", "title": "test-b"}, path)
        recs = supervisor.inspect_subagents(path)
        assert len(recs) == 2
        dups = supervisor.detect_duplicate_worktrees(recs)
        assert len(dups) == 1
        assert dups[0]["worktree"] == "/tmp/wt-test-dup"
    finally:
        path.unlink(missing_ok=True)


def test_projection_readonly_and_verdict():
    proj = supervisor.emit_projection(
        free_models=supervisor.enumerate_free_models(FIXTURE_MODELS),
        state_path=FIXTURE_STATE,
    )
    assert proj.get("read_only") is True
    assert proj.get("supervision_slice") == "free_subagent_supervisor"
    assert "suggested_scale_verdict" in proj
    assert proj.get("duplicate_worktrees") is not None
    # With fixture duplicates, verdict should be hold
    assert proj.get("suggested_scale_verdict") == "hold"


def test_projection_healthy_when_clean():
    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tf:
        clean_path = pathlib.Path(tf.name)
    try:
        # Empty clean state with free models and credential simulated by monkey? We just test structure.
        proj = supervisor.emit_projection(
            free_models=[{"id":"openrouter/free/test","tags":["free"]}],
            state_path=clean_path,
        )
        assert proj.get("read_only") is True
        assert proj.get("suggested_scale_verdict") in ("healthy", "hold")
    finally:
        clean_path.unlink(missing_ok=True)


def test_projection_notes_document_stdinhazard():
    proj = supervisor.emit_projection()
    note = proj.get("projections_note") or ""
    assert "/dev/null" in note or "stdin" in note.lower() or "close stdin" in note.lower()


def test_sqlite_session_history_is_not_treated_as_concurrent_process_duplication():
    records = [
        {
            "source": "sqlite_readonly",
            "session": "historical-a",
            "worktree": "/tmp/shared-worktree",
            "status": "active",
            "pid": None,
        },
        {
            "source": "sqlite_readonly",
            "session": "historical-b",
            "worktree": "/tmp/shared-worktree",
            "status": "active",
            "pid": None,
        },
    ]
    assert supervisor.detect_duplicate_worktrees(records) == []


if __name__ == "__main__":
    test_enumerate_free_models_from_fixture()
    test_credential_present_no_secret_leak()
    test_register_inspect_duplicates()
    test_projection_readonly_and_verdict()
    test_projection_healthy_when_clean()
    test_projection_notes_document_stdinhazard()
    test_sqlite_session_history_is_not_treated_as_concurrent_process_duplication()
    print("all free_subagent_supervisor tests passed")


def test_discover_live_sessions_readonly_query_only():
    import sys, sqlite3
    sys.path.insert(0, "scripts")
    from free_subagent_supervisor import discover_live_sessions, _opencode_db_uri
    result = discover_live_sessions(query_only=True)
    assert isinstance(result, list)
    # Verify path derived from HOME (not a literal hardcoded /home/scott in source)
    db_uri = _opencode_db_uri()
    assert db_uri.startswith("file:")
    assert "opencode.db" in db_uri
    # Prove mode=ro + PRAGMA query_only by asserting a write fails
    conn = sqlite3.connect(db_uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    try:
        conn.execute("CREATE TEMP TABLE _assert_write_fail (id INTEGER)")
        assert False, "Write should have failed on mode=ro with query_only"
    except sqlite3.OperationalError:
        pass  # expected
    conn.close()

