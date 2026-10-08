"""Read-only source reconciliation for account and isolated OpenCode databases."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/snapshot_free_usage.py"
spec = importlib.util.spec_from_file_location("snapshot_free_usage", SCRIPT)
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


def fake_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE session(
        id TEXT, title TEXT, model TEXT, cost REAL,
        tokens_input INTEGER, tokens_output INTEGER,
        tokens_reasoning INTEGER, tokens_cache_read INTEGER,
        tokens_cache_write INTEGER, time_updated INTEGER)""")
    con.executemany("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_dual_db_dedupe_and_orphan(tmp_path):
    a = tmp_path / "account.db"
    b = tmp_path / "isolated.db"
    row_a = ("ses_common", "a", json.dumps({"providerID": "kilo_free", "id": "stepfun"}),
             0.0, 30, 4, 2, 0, 0, 1800000)
    row_b = ("ses_second", "b", json.dumps({"providerID": "openrouter", "id": "free"}),
             0.0, 80, 9, 1, 3, 0, 1800000)
    fake_db(a, [row_a])
    fake_db(b, [row_a, row_b])
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"session": "ses_common", "status": "running",
        "opencode_pid": 9999999, "title": "test-orphan"}) + "\n")
    result = usage.snapshot((a,b), (ledger,), now=1800, days=2)
    assert result["complete"]
    assert result["unique_sessions"] == 2
    assert result["duplicate_session_ids"] == ["ses_common"]
    assert result["orphan_count"] == 1
    assert result["ledgers"][0]["outcome"] == "incomplete"
    assert result["by_provider"]["kilo_free"]["tokens"]["input"] == 30
    assert result["by_provider"]["openrouter"]["tokens"]["input"] == 80


def test_incomplete_if_db_missing_or_truncated(tmp_path):
    missing = tmp_path / "missing.db"
    present = tmp_path / "empty.db"
    fake_db(present, [])
    value = usage.snapshot((present, missing), (), now=1800)
    assert value['complete'] is False
    assert value['databases'][1]['found'] is False


def test_read_only_does_not_change_source(tmp_path):
    db = tmp_path / 'account.db'
    fake_db(db, [('sess', 'name', '{}', 0.0, 1, 2, 0, 0, 0, 1800000)])
    size = db.stat().st_size
    usage.read_db(db, since_ms=0)
    assert db.stat().st_size == size
