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
