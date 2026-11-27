"""Regression tests for claim-fence mode parsing (sweep40).

Hermetic and synthetic: the module under test is loaded directly from source,
so no package import side effects, Redis, Neo4j, Docker or network access is
required. Only environment strings are changed, via pytest monkeypatch.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "assistx" / "strict_claims.py"


@pytest.fixture
def claims(monkeypatch):
    # A fresh module object per test keeps _INSTALLED and other globals isolated.
    spec = importlib.util.spec_from_file_location("sweep40_strict_claims", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("ASSISTX_LEGACY_CLAIMLESS_AGENTS", raising=False)
    return module


@pytest.mark.parametrize(
    "raw",
    [
        "true",
        "True",
        "TRUE",
        "1",
        "yes",
        "YES",
        "on",
        "ON",
        "enabled",
        "required",
        "enforce",
    ],
)
def test_affirmative_setting_enables_fleet_wide_fence(claims, monkeypatch, raw):
    """An operator asking for enforcement must not get the narrow default."""
    monkeypatch.setenv("ASSISTX_REQUIRE_WORKER_CLAIM_ID", raw)

    assert claims._mode() == "all"
    assert claims._claim_required("worker-a") is True


@pytest.mark.parametrize(
    "raw",
    ["al", "TRUEE", "enforcing", "full", "2", "strict-mode"],
)
def test_unrecognized_setting_fails_closed(claims, monkeypatch, raw):
    """A typo must not silently weaken an intended strict posture."""
    monkeypatch.setenv("ASSISTX_REQUIRE_WORKER_CLAIM_ID", raw)

    assert claims._mode() == "all"
    assert claims._claim_required("worker-b") is True


@pytest.mark.parametrize(
    ("raw", "expected_mode"),
    [
        ("all", "all"),
        ("strict", "all"),
        ("ALL", "all"),
        ("  strict  ", "all"),
        ("modern", "modern"),
        ("", "modern"),
        ("off", "disabled"),
        ("no", "disabled"),
        ("disabled", "disabled"),
        ("FALSE", "disabled"),
    ],
)
def test_documented_values_keep_their_meaning(claims, monkeypatch, raw, expected_mode):
    monkeypatch.setenv("ASSISTX_REQUIRE_WORKER_CLAIM_ID", raw)

    assert claims._mode() == expected_mode


def test_modern_mode_still_scopes_to_named_agents(claims, monkeypatch):
    monkeypatch.setenv("ASSISTX_REQUIRE_WORKER_CLAIM_ID", "modern")
    monkeypatch.setenv("ASSISTX_CLAIM_REQUIRED_AGENTS", "fleet-executor,worker-a")

    assert claims._claim_required("worker-a") is True
    assert claims._claim_required("worker-b") is False


def test_disabled_mode_still_bypasses_every_agent(claims, monkeypatch):
    monkeypatch.setenv("ASSISTX_REQUIRE_WORKER_CLAIM_ID", "false")
    monkeypatch.setenv("ASSISTX_LEGACY_CLAIMLESS_AGENTS", "")

    assert claims._claim_required("worker-a") is False
    assert claims._claim_required("fleet-executor") is False


def test_legacy_allowlist_still_overrides_strict_mode(claims, monkeypatch):
    """The staging cutover escape hatch is intentionally preserved."""
    monkeypatch.setenv("ASSISTX_REQUIRE_WORKER_CLAIM_ID", "true")
    monkeypatch.setenv("ASSISTX_LEGACY_CLAIMLESS_AGENTS", "worker-a")

    assert claims._claim_required("worker-a") is False
    assert claims._claim_required("worker-b") is True
