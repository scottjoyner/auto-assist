import importlib.util
import json
import pathlib
import sqlite3
import subprocess
import tempfile


MODULE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "scripts"
    / "export_opencode_session_traces.py"
)
SPEC = importlib.util.spec_from_file_location("trace_exporter", MODULE_PATH)
trace_exporter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(trace_exporter)


def test_export_preserves_model_tokens_tools_and_git_shape():
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
                "ses1", None, str(root), "trace-test",
                json.dumps({"providerID": "zai", "id": "glm-4.5-flash", "variant": "default"}),
                0.0, 100, 20, 5, 50, 0, 1, 2,
            ),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("m1", "ses1", 1, 1, json.dumps({"role": "user"})),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("m2", "ses1", 2, 2, json.dumps({"role": "assistant"})),
        )
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            ("p1", "m1", "ses1", 1, 1, json.dumps({"type": "text", "text": "objective"})),
        )
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            ("p2", "m2", "ses1", 2, 2, json.dumps({"type": "tool", "tool": "bash"})),
        )
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            ("p3", "m2", "ses1", 3, 3, json.dumps({"type": "step-finish", "reason": "stop"})),
        )
        con.commit()
        con.close()

        records = trace_exporter.export_sessions(db)
        assert len(records) == 1
        row = records[0]
        assert row["provider"] == "zai"
        assert row["model"] == "glm-4.5-flash"
        assert row["cost"] == 0.0
        assert row["tokens"]["input"] == 100
        assert row["tools"] == {"bash": 1}
        assert row["last_finish_reason"] == "stop"
        assert row["objective"]["chars"] == len("objective")
        assert len(row["objective"]["sha256"]) == 64
        assert "is_git" in row["git"]


def test_export_records_exact_requested_and_resolved_model():
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
        # A router alias route: the requested model is a generic token and
        # no resolved model is recorded.
        con.execute(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ses_alias", None, str(root), "alias-route",
                json.dumps({"providerID": "openrouter", "id": "openrouter/free"}),
                0.0, 10, 5, 0, 0, 0, 1, 2,
            ),
        )
        # An exact-model route with a resolved model reported by the provider.
        con.execute(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ses_exact", None, str(root), "exact-route",
                json.dumps({
                    "providerID": "kilo",
                    "id": "kilo/kimi-k2-turbo",
                    "resolvedID": "kilo/kimi-k2-turbo-2026-01",
                }),
                0.0, 20, 8, 0, 0, 0, 1, 2,
            ),
        )
        con.commit()
        con.close()

        records = trace_exporter.export_sessions(db)
        by_session = {r["session_id"]: r for r in records}

        alias = by_session["ses_alias"]
        assert alias["requested_model"] == "openrouter/free"
        assert alias["resolved_model"] is None
        assert alias["route_kind"] == "alias"
        assert alias["effective_model"] is None
        assert alias["model_attribution"] == "unresolved-router-alias"
        assert alias["model_identity_complete"] is False

        exact = by_session["ses_exact"]
        assert exact["requested_model"] == "kilo/kimi-k2-turbo"
        assert exact["resolved_model"] == "kilo/kimi-k2-turbo-2026-01"
        assert exact["route_kind"] == "exact"
        assert exact["effective_model"] == "kilo/kimi-k2-turbo-2026-01"
        assert exact["model_attribution"] == "resolved-upstream"
        assert exact["model_identity_complete"] is True


def test_exporter_records_git_head_and_worktree():
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        # Initialise a real git repository so _git() records head/branch.
        subprocess.run(
            ["git", "-C", str(root), "init", "-q"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            [
                "git", "-C", str(root),
                "-c", "user.email=test@example.com",
                "-c", "user.name=Test",
                "commit", "--allow-empty", "-q", "-m", "init",
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
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
                "ses_git", None, str(root), "git-route",
                json.dumps({"providerID": "zai", "id": "zai/glm-4.7-flash"}),
                0.0, 1, 1, 0, 0, 0, 1, 2,
            ),
        )
        con.commit()
        con.close()

        records = trace_exporter.export_sessions(db)
        row = records[0]
        # Worktree and Git provenance (including head) are recorded.
        assert row["directory"] == str(root)
        assert row["git"]["is_git"] is True
        assert row["git"]["root"] == str(root)
        assert len(row["git"]["head"]) == 40
        assert row["git"]["branch"]
        assert row["time_created"] == 1
        assert row["time_updated"] == 2


def test_supervisor_annotates_trace_sessions_with_route_kind():
    import importlib.util

    supervisor_path = (
        pathlib.Path(__file__).parent.parent
        / "scripts"
        / "free_subagent_supervisor.py"
    )
    spec = importlib.util.spec_from_file_location("free_subagent_supervisor", supervisor_path)
    supervisor = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(supervisor)

    alias = supervisor._annotate_trace_session(
        {"session_id": "s1", "provider": "openrouter", "model": "openrouter/free"}
    )
    assert alias["requested_model"] == "openrouter/free"
    assert alias["route_kind"] == "alias"
    assert alias["effective_model"] is None
    assert alias["model_attribution"] == "unresolved-router-alias"
    assert alias["model_identity_complete"] is False

    exact = supervisor._annotate_trace_session(
        {
            "session_id": "s2",
            "provider": "zai",
            "model": "zai/glm-4.7-flash",
            "requested_model": "zai/glm-4.7-flash",
            "resolved_model": "zai/glm-4.7-flash",
        }
    )
    assert exact["requested_model"] == "zai/glm-4.7-flash"
    assert exact["route_kind"] == "exact"
    assert exact["effective_model"] == "zai/glm-4.7-flash"
    assert exact["model_attribution"] == "resolved-upstream"
    assert exact["model_identity_complete"] is True

    exact_without_provider_resolution = supervisor._annotate_trace_session(
        {
            "session_id": "s3",
            "provider": "kilo_free",
            "model": "stepfun/step-3.7-flash:free",
        }
    )
    assert exact_without_provider_resolution["route_kind"] == "exact"
    assert exact_without_provider_resolution["effective_model"] == "stepfun/step-3.7-flash:free"
    assert exact_without_provider_resolution["model_attribution"] == "exact-request"
    assert exact_without_provider_resolution["model_identity_complete"] is True


def test_export_adds_task_ownership_fields():
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
                "ses1", None, str(root), "trace-test-task-ownership",
                json.dumps({"providerID": "zai", "id": "glm-4.5-flash", "variant": "default"}),
                0.0, 100, 20, 5, 50, 0, 1, 2,
            ),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("m1", "ses1", 1, 1, json.dumps({"role": "user"})),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("m2", "ses1", 2, 2, json.dumps({"role": "assistant"})),
        )
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            ("p1", "m1", "ses1", 1, 1, json.dumps({"type": "text", "text": "objective"})),
        )
        con.commit()
        con.close()

        records = trace_exporter.export_sessions(db)
        assert len(records) == 1
        row = records[0]
        
        # Verify trace receipt contract schema version v2
        assert row["schema"] == "opencode-session-trace/v2"
        
        # Verify task/worktree ownership fields
        assert row["task_id"] == "trace-test-task-ownership"
        assert row["worktree_owners"] == ["ses1"]


def test_export_adds_node_session_correlation():
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
        # Create a directory that looks like a node/worktree path
        node_worktree = root / "node-x1-370" / "worktree-task-123"
        node_worktree.mkdir(parents=True, exist_ok=True)
        con.execute(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ses1", None, str(node_worktree), "trace-test-node-correlation",
                json.dumps({"providerID": "kilo", "id": "kilo/kilo-auto/free", "variant": "default"}),
                0.0, 100, 20, 5, 50, 0, 1, 2,
            ),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("m1", "ses1", 1, 1, json.dumps({"role": "user"})),
        )
        con.commit()
        con.close()

        records = trace_exporter.export_sessions(db)
        assert len(records) == 1
        row = records[0]
        
        # Verify node correlation fields
        assert row["node_id"] is not None
        assert row["node_session_correlation"]["node_id"] is not None
        assert row["node_session_correlation"]["session_id"] == "ses1"
        assert "node-x1-370" in row["node_session_correlation"]["directory"]


def test_export_adds_free_provider_classification():
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
                "ses1", None, str(root), "trace-test-free-provider",
                json.dumps({
                    "providerID": "kilo",
                    "id": "opencode-native-free:free",
                    "pricing": {"prompt": 0.0, "completion": 0.0}
                }),
                0.0, 100, 20, 5, 50, 0, 1, 2,
            ),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("m1", "ses1", 1, 1, json.dumps({"role": "user"})),
        )
        con.commit()
        con.close()

        records = trace_exporter.export_sessions(db)
        assert len(records) == 1
        row = records[0]
        
        # Verify free provider classification
        assert row["free_model_type"] == "opencode-native-free"
        assert row["is_free_provider"] is True


def test_export_adds_retry_error_tracking():
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
                "ses1", None, str(root), "trace-test-retry-error",
                json.dumps({"providerID": "zai", "id": "zai/glm-4.7-flash"}),
                0.0, 100, 20, 5, 50, 0, 1, 2,
            ),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("m1", "ses1", 1, 1, json.dumps({"role": "user"})),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("m2", "ses1", 2, 2, json.dumps({"role": "assistant", "error": "timeout"})),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("m3", "ses1", 3, 3, json.dumps({"role": "assistant", "error": "rate_limit"})),
        )
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            ("p1", "m1", "ses1", 1, 1, json.dumps({"type": "text", "text": "objective"})),
        )
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            ("p2", "m2", "ses1", 2, 2, json.dumps({"type": "step-finish", "reason": "stop"})),
        )
        con.commit()
        con.close()

        records = trace_exporter.export_sessions(db)
        assert len(records) == 1
        row = records[0]
        
        # Verify retry/error tracking fields
        assert row["assistant_error_count"] == 2
        assert row["retry_count"] == 0  # Default
        assert row["max_retries"] == 3
        assert len(row["error_summary"]["assistant_errors"]) == 2
        assert "timeout" in row["error_summary"]["assistant_errors"][0]
        assert "rate_limit" in row["error_summary"]["assistant_errors"][1]


def test_export_adds_enhanced_cost_token_breakdown():
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
                "ses1", None, str(root), "trace-test-cost-breakdown",
                json.dumps({"providerID": "zai", "id": "zai/glm-4.7-flash"}),
                100.0, 1000, 500, 100, 50, 50, 1, 2,
            ),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("m1", "ses1", 1, 1, json.dumps({"role": "user"})),
        )
        con.commit()
        con.close()

        records = trace_exporter.export_sessions(db)
        assert len(records) == 1
        row = records[0]
        
        # Verify cost breakdown
        assert row["cost"] == 100.0
        assert row["cost_breakdown"]["total_cost"] == 100.0
        assert row["cost_breakdown"]["input_cost"] == 70.0
        assert row["cost_breakdown"]["output_cost"] == 30.0
        
        # Verify token breakdown
        assert row["token_breakdown"]["total_tokens"] == 1700
        assert row["token_breakdown"]["input_tokens"] == 1000
        assert row["token_breakdown"]["output_tokens"] == 500
        assert row["token_breakdown"]["reasoning_tokens"] == 100
        assert row["token_breakdown"]["cache_read_tokens"] == 50
        assert row["token_breakdown"]["cache_write_tokens"] == 50
        
        # Verify efficiency metrics
        assert row["efficiency_metrics"]["cost_per_1k_input_tokens"] == 70.0
        assert row["efficiency_metrics"]["cost_per_1k_output_tokens"] == 60.0
