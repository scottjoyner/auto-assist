"""Offline bounded audit-ledger trend semantics: snapshots are gauges."""
import json
import pathlib
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from burn_history_projection import history_points, rolling_gauge_trend
from burn_history_html import recent_gauge_panel


def receipt(ts, value, provider="kilo"):
    return {"schema":"fleet-provider-burn-audit/v1",
            "source_generated_epoch": ts, "complete":True,
            "providers":[{"provider":provider,"last_24h":value}]}


def test_history_does_not_sum_rolling_snapshot_values():
    with tempfile.TemporaryDirectory() as root:
        p = pathlib.Path(root) / "observations.jsonl"
        p.write_text("\n".join(json.dumps(receipt(t, v)) for t,v in
                    ((1000, 1000), (1300, 800), (1600, 400))) + "\n")
        points = history_points(p, now=1600)
        gauge = rolling_gauge_trend(points)
        assert len(points) == 3
        assert gauge["sample_count"] == 3
        assert gauge["change"] == -600
        assert gauge["gauges"] == [1000, 800, 400]
        assert "sum" in recent_gauge_panel({"complete":False}, points).lower()


def test_corrupt_partial_or_future_samples_excluded():
    with tempfile.TemporaryDirectory() as root:
        p = pathlib.Path(root) / "observations.jsonl"
        p.write_text("not-json\n" +
             json.dumps(receipt(1000, 55)) + "\n" +
             json.dumps({**receipt(1500, 800), "complete":False}) + "\n" +
             json.dumps(receipt(1_000_000, 999999)) + "\n")
        values = history_points(p, now=1600)
        assert len(values) == 1
        assert values[0]["total"] == 55
        assert rolling_gauge_trend(values)["status"] == "insufficient_span"


def test_missing_ledger_and_escaping_provider_names():
    assert history_points(pathlib.Path("/tmp/absent-burn-ledger-q4k9j"), now=1000) == []
    page = recent_gauge_panel(
        {"complete":True,"updated_epoch":1600,
         "providers":[{"provider":"<script>","observed_tokens_24h":42}]}, [])
    assert "Insufficient history" in page
    assert "<script>" not in page
