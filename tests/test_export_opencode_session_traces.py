import importlib.util
import json
import pathlib
import sqlite3
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
