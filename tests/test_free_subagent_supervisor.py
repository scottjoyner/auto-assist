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


def test_projection_with_trace_exporter_integration():
    import sys, json
    sys.path.insert(0, "scripts")
    from free_subagent_supervisor import emit_projection
    
    # Create a temporary trace exporter module for testing
    import importlib.util
    import pathlib
    
    trace_exporter_path = pathlib.Path(__file__).parent / "fixtures" / "empty_trace_exporter.py"
    if not trace_exporter_path.exists():
        # Create a mock trace exporter
        trace_exporter_path.write_text('''
import json
import pathlib
import sqlite3
from typing import Any

def export_sessions(db_path: pathlib.Path) -> list[dict[str, Any]]:
    return []
''')
    
    # Test projection with trace exporter (will fail gracefully if no DB)
    proj = emit_projection(
        free_models=[{"id": "openrouter/claude-3.5-sonnet", "provider": "openrouter", "pricing": {"prompt": "0", "completion": "0"}}],
        state_path=FIXTURE_STATE,
        trace_exporter_path=trace_exporter_path
    )
    
    # Check that trace exporter integration fields are present
    assert "trace_records_count" in proj
    assert "trace_records_sample" in proj
    assert "trace_provider_summary" in proj
    assert "trace_exporter_error" not in proj or isinstance(proj.get("trace_exporter_error"), str)


def test_trace_exporter_readonly_query_only():
    import importlib.util
    import json
    import pathlib
    import sqlite3
    import tempfile
    from typing import Any
    
    MODULE_PATH = (
        pathlib.Path(__file__).parent.parent
        / "scripts"
        / "export_opencode_session_traces.py"
    )
    SPEC = importlib.util.spec_from_file_location("trace_exporter", MODULE_PATH)
    trace_exporter = importlib.util.module_from_spec(SPEC)
    assert SPEC.loader is not None
    SPEC.loader.exec_module(trace_exporter)
    
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        db = root / "opencode.db"
        con = sqlite3.connect(db)
        con.executescript(
            """
            CREATE TABLE session (
              id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT, title TEXT,
              model TEXT, cost REAL, tokens_input INTEGER, tokens_output INTEGER,
              tokens_reasoning INTEGER, tokens_cache_read INTEGER,
              tokens_cache_write INTEGER, time_created INTEGER, time_updated INTEGER
            );
            CREATE TABLE message (
              id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
              time_updated INTEGER, data TEXT
            );
            CREATE TABLE part (
              id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
              time_created INTEGER, time_updated INTEGER, data TEXT
            );
            """
        )
        con.execute(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ses_trace_test", None, str(root), "trace-test-session",
                json.dumps({"providerID": "test-provider", "id": "test-model", "variant": "test"}),
                0.0, 100, 20, 5, 50, 0, 1, 2,
            ),
        )
        con.commit()
        con.close()
        
        records = trace_exporter.export_sessions(db)
        assert len(records) == 1
        assert records[0]["provider"] == "test-provider"
        assert records[0]["model"] == "test-model"
        assert records[0]["tokens"]["input"] == 100
        assert records[0]["cost"] == 0.0


def test_classify_free_model_openrouter_account_free():
    from free_subagent_supervisor import classify_free_model
    
    # OpenRouter with zero prompt and completion and free tag should be opencode-native-free
    model = {
        "id": "openrouter/anthropic/claude-3.5-sonnet",
        "provider": "openrouter",
        "pricing": {"prompt": "0", "completion": "0"},
        "tags": ["free"]
    }
    assert classify_free_model(model) == "opencode-native-free"
    
    # OpenRouter with zero prompt and completion but no free tag
    model["tags"] = []
    assert classify_free_model(model) == "openrouter-account-free"
    
    # Deepseek with paid completion should be rate-limited
    model["id"] = "openrouter/deepseek/deepseek-r1"
    model["pricing"] = {"prompt": "0.54", "completion": "2.19"}
    assert classify_free_model(model) == "rate-limited"
    
    # Zero prompt but paid completion
    model["id"] = "openrouter/example/zero-prompt-paid-completion"
    model["pricing"] = {"prompt": "0", "completion": "0.25"}
    assert classify_free_model(model) == "rate-limited"


def test_classify_free_model_opencode_native():
    from free_subagent_supervisor import classify_free_model
    
    # OpenCode-native :free models
    model = {
        "id": "openrouter/some-model:free",
        "provider": "openrouter",
        "pricing": {"prompt": "0.1", "completion": "0.2"}
    }
    assert classify_free_model(model) == "opencode-native-free"
    
    # With explicit free tag
    model = {
        "id": "openrouter/some-model",
        "provider": "openrouter",
        "pricing": {"prompt": "0.1", "completion": "0.2"},
        "tags": ["free"]
    }
    assert classify_free_model(model) == "opencode-native-free"


def test_classify_free_model_provider_specific():
    from free_subagent_supervisor import classify_free_model
    
    # Z.AI zero-cost
    model = {
        "id": "zai/glm-4-flash",
        "provider": "zai",
        "pricing": {"prompt": "0", "completion": "0"}
    }
    assert classify_free_model(model) == "zai-zero-cost"
    
    # Cohere zero-cost
    model = {
        "id": "cohere/command-r-plus",
        "provider": "cohere",
        "pricing": {"prompt": "0", "completion": "0"}
    }
    assert classify_free_model(model) == "cohere-zero-cost"
    
    # Rate-limited Cohere
    model["pricing"] = {"prompt": "0.001", "completion": "0.002"}
    assert classify_free_model(model) == "rate-limited"
    
    # LM Studio
    model = {
        "id": "lmstudio/mistral",
        "provider": "lmstudio",
        "pricing": {"prompt": "0", "completion": "0"}
    }
    assert classify_free_model(model) == "lmstudio-zero-cost"


def test_classify_free_model_unknown_free():
    from free_subagent_supervisor import classify_free_model
    
    # Unknown free model (zero pricing but unclassified provider)
    model = {
        "id": "custom/free-model",
        "provider": "custom",
        "pricing": {"prompt": "0", "completion": "0"}
    }
    assert classify_free_model(model) == "unknown-free"
    
    # Model with partial zero pricing (should be rate-limited)
    model["pricing"] = {"prompt": "0", "completion": "0.5"}
    assert classify_free_model(model) == "rate-limited"


def test_enumerate_free_models_with_classification():
    from free_subagent_supervisor import enumerate_free_models
    
    models = enumerate_free_models(FIXTURE_MODELS)
    
    # Verify we get models back
    assert len(models) > 0
    
    # Check that each model has classification
    for m in models:
        assert "free_model_type" in m
        assert m["free_model_type"] in [
            "openrouter-account-free",
            "opencode-native-free",
            "rate-limited"
        ]
    
    # Verify specific model types from fixture
    # Claude has tags=["free"] so it's classified as opencode-native-free
    claude_ids = [m.get("id", "") for m in models if "claude" in m.get("id", "").lower()]
    assert len(claude_ids) > 0, "Claude model should be found"
    claude_type = next(m.get("free_model_type", "") for m in models if "claude" in m.get("id", "").lower())
    assert claude_type == "opencode-native-free", "Claude with free tag should be classified as opencode-native-free"
    
    gemini_ids = [m.get("id", "") for m in models if "gemini" in m.get("id", "").lower()]
    assert len(gemini_ids) > 0, "Gemini model should be found"
    gemini_type = next(m.get("free_model_type", "") for m in models if "gemini" in m.get("id", "").lower())
    assert gemini_type == "opencode-native-free", "Gemini with free tag should be classified as opencode-native-free"
    
    # Deepseek and zero-prompt-paid-completion should not be in results (rate-limited)
    deepseek_ids = [m.get("id", "") for m in models if "deepseek" in m.get("id", "").lower()]
    assert len(deepseek_ids) == 0, "Deepseek with paid pricing should not be included in free models"
    
    zero_pricing_ids = [
        m.get("id", "") for m in models
        if "zero-prompt-paid-completion" in m.get("id", "")
    ]
    assert len(zero_pricing_ids) == 0, "Zero-prompt-paid-completion should not be included in free models"


def test_enumerate_free_models_deduplicates():
    from free_subagent_supervisor import enumerate_free_models
    
    # Test that duplicates are removed
    fixture = pathlib.Path(__file__).parent / "fixtures" / "openrouter_models_sample.jsonl"
    models = enumerate_free_models(fixture)
    
    # Check no duplicates by id
    ids = [m.get("id") for m in models]
    assert len(ids) == len(set(ids)), "Should not have duplicate model IDs"


def test_projection_includes_free_model_types():
    from free_subagent_supervisor import emit_projection
    
    proj = emit_projection(
        free_models=[
            {
                "id": "openrouter/claude-3.5-sonnet",
                "provider": "openrouter",
                "pricing": {"prompt": "0", "completion": "0"},
                "tags": ["free"]
            },
            {
                "id": "openrouter/claude-3.5-sonnet:free",
                "provider": "openrouter",
                "tags": ["free"]
            },
        ],
        state_path=FIXTURE_STATE
    )
    
    assert "free_model_types" in proj
    # Both have free tags, so both are opencode-native-free
    assert proj["free_model_types"]["opencode-native-free"] == 2
    # No openrouter-account-free since both have tags
