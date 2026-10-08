from __future__ import annotations
import importlib.util
import json
import pathlib
import tempfile
import sys

MODULE = pathlib.Path(__file__).resolve().parents[1] / "scripts/render_provider_burn_fragment.py"
sys.path.insert(0, str(MODULE.parent))
spec = importlib.util.spec_from_file_location("burn_fragment", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_allowlisted_render_and_html_escapes():
    data = {
        "state": "fresh", "complete": True, "updated_epoch": 1791450000,
        "observed_tokens_24h": 1234567, "forecast_next_24h": 1234567,
        "providers": [{
            "provider": "<script>alert(1)</script>", "presence": "catalog_reachable",
            "observed_tokens_1h": 0, "observed_tokens_24h": 1234567,
            "observed_tokens_7d": 2200000, "cache_read_24h": 400000,
            "forecast_next_24h": 1234567,
        }],
    }
    rendered = mod.render(data)
    assert "&lt;script&gt;" in rendered
    assert "<script>" not in rendered
    assert "1.23M" in rendered
    assert "Remaining free allowance: <strong>Unknown</strong>" in rendered


def test_stale_and_unavailable_are_visible():
    out = mod.render({"state": "unavailable", "complete": False})
    assert "Snapshot unavailable" in out
    assert "24h:" not in out
    stale = mod.render({"state": "stale", "complete": False,
                        "updated_epoch": 1791450000, "providers": []})
    assert "STALE" in stale and "UNKNOWN COVERAGE" in stale


def test_atomic_write_private_and_no_residual_files():
    with tempfile.TemporaryDirectory() as tmp:
        dest = pathlib.Path(tmp) / "panel.html"
        mod.atomic_private_write(dest, "hello")
        assert dest.read_text() == "hello"
        assert dest.stat().st_mode & 0o777 == 0o600
        mod.atomic_private_write(dest, "world")
        assert dest.read_text() == "world"
        assert sorted(p.name for p in pathlib.Path(tmp).iterdir()) == ["panel.html"]


def test_catalog_warning_is_advisory_and_html_escaped():
    rendered = mod.render({
        "state": "fresh", "complete": True, "updated_epoch": 1791450000,
        "observed_tokens_24h": 0, "forecast_next_24h": 0,
        "providers": [{"provider": "<unsafe>", "presence": "rate_limited",
                       "observed_tokens_24h": 0, "observed_tokens_1h": 0,
                       "observed_tokens_7d": 0, "cache_read_24h": 0,
                       "forecast_next_24h": 0}],
    }, history=[])
    assert "Catalog/credential advisory" in rendered
    assert "&lt;unsafe&gt;" in rendered
    assert "<unsafe>" not in rendered
    assert "Insufficient history" in rendered
