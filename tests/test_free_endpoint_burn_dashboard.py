"""Offline tests for the read-only free-endpoint burn dashboard; no APIs."""
from __future__ import annotations
import importlib.util
import json
import pathlib
import sqlite3
import tempfile

HERE = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "burn_dashboard", HERE / "scripts/free_endpoint_burn_dashboard.py"
)
burn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(burn)


def fake_db(path, *, session="ses-1", provider="openrouter", model="sample:free"):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE session (id TEXT, model TEXT, time_updated INT)")
    db.execute("CREATE TABLE part (session_id TEXT, time_created INT, data TEXT)")
    db.execute("INSERT INTO session VALUES (?,?,?)",
               (session, json.dumps({"providerID": provider, "id": model}), 10_000_000))
    db.execute("INSERT INTO part VALUES (?,?,?)", (session, 9_990_000,
        json.dumps({"type": "step-finish", "tokens": {
            "total": 100, "input": 25, "output": 10, "reasoning": 5,
            "cache": {"read": 60, "write": 0}}})))
    db.commit()
    db.close()


def test_parses_cache_as_distinct_token_class():
    values = burn.parse_step(json.dumps({"type": "step-finish",
        "tokens": {"total": 100, "input": 25, "output": 10,
                   "reasoning": 5, "cache": {"read": 60, "write": 0}}}))
    assert values["total"] == 100
    assert values["cache_read"] == 60
    assert burn.parse_step('{"type":"step-finish","tokens":{"input":-1}}') is None


def test_dedup_and_flat_pace_forecast_from_step_events():
    with tempfile.TemporaryDirectory() as root:
        a, b = pathlib.Path(root) / "a.db", pathlib.Path(root) / "b.db"
        fake_db(a)
        fake_db(b)
        events, sources = burn.load_events((a, b), now=10_000)
        assert len(events) == 1, "same OpenCode session must not be double counted"
        assert sources[1]["duplicates"] == 1
        result = burn.build_report(events, sources, now=10_000)
        assert result["complete"] is True
        row = result["models"][0]
        assert row["windows"]["1h"]["total"] == 100
        assert row["windows"]["24h"]["cache_read"] == 60
        assert row["forecast_next_24h_flat_pace"] == 100
        assert row["quota_remaining"] is None
        assert row["eligibility"] == "UNVERIFIED"
        assert result["by_provider"][0]["forecast_next_24h"] == 100


def test_stale_events_do_not_become_recent_burn():
    source = [("kilo", "model:free", 100.0, {
        "input": 2, "output": 2, "reasoning": 0,
        "cache_read": 0, "cache_write": 0, "total": 4})]
    result = burn.build_report(source, [], now=100.0 + 3 * 86400)
    row = result["models"][0]
    assert row["windows"]["7d"]["total"] == 4
    assert row["windows"]["24h"]["total"] == 0
    assert row["forecast_next_7d_flat_pace"] == 0


def test_html_escapes_untrusted_model_and_keeps_unknown_quota():
    source = [("unknown", "<script>alert(1)</script>", 9999.0, {
        "input": 1, "output": 0, "reasoning": 0,
        "cache_read": 0, "cache_write": 0, "total": 1})]
    page = burn.render_html(burn.build_report(source, [], now=10000.0))
    assert "&lt;script&gt;" in page
    assert "<script>alert(1)</script>" not in page
    assert "quota remaining" in page.lower()


def test_missing_database_is_partial_not_zero_usage():
    events, coverage = burn.load_events(("/definitely/missing-opencode.db",), now=10000)
    result = burn.build_report(events, coverage, now=10000)
    assert not result["complete"]
    assert coverage[0]["error"] == "missing_database"


def test_operator_planning_baseline_is_not_vendored_quota():
    tokens = {key: 0 for key in burn.KEYS}
    tokens.update({"total": 100, "input": 40, "output": 60})
    report = burn.build_report(
        [("openrouter", "alpha:free", 9999.0, tokens)], [{"complete": True, "error": None, "sessions": 1}],
        now=10000.0, policy={"openrouter": {"planned_tokens_per_day": 50}})
    row = report["models"][0]
    assert row["name_suggests_free"]
    assert row["eligibility"] == "UNVERIFIED"
    assert row["pace_vs_plan"] == 2.0
    assert row["quota_remaining"] is None
    assert report["by_provider"][0]["token_classes_24h"]["input"] == 40
    assert report["free_named_observed_24h"] == 100
