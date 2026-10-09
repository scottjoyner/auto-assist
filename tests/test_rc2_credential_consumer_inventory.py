"""Synthetic no-value disclosure tests: Docker is never called during tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "rc2_credential_names_only",
    ROOT / "scripts" / "rc2-credential-consumer-inventory.py",
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def synthetic(name: str, extra: list[str] | None = None) -> dict:
    return {
        "Name": "/" + name, "State": {"Running": True},
        "Config": {"Env": [
            "BASIC_AUTH_USER=synthetic-name-do-not-output",
            "BASIC_AUTH_PASS=EXAMPLE_NEVER_LIVE_42",
            "TRUSTED_AUTH_HEADER=Tailscale-User-Login",
            "NEO4J_PASSWORD=SIMULATED_PRIVATE_123",
            "PAPERCLIP_API_KEY=SIMULATED_NEVER_PRINT",
            *(extra or []),
        ]},
    }


def test_redacted_summary_never_reports_a_credential_value():
    data = [synthetic(name) for name in module.TARGETS]
    summary = module.summarize(data)
    dumped = json.dumps(summary, sort_keys=True)
    assert summary["production_release_authorized"] is False
    assert summary["credential_rotation_performed"] is False
    for marker in ("EXAMPLE_NEVER_LIVE_42", "SIMULATED_PRIVATE_123",
                   "SIMULATED_NEVER_PRINT", "synthetic-name-do-not-output"):
        assert marker not in dumped
    assert all(
        item["groups"]["basic_auth"] == 2
        for item in summary["containers"]
    )


def test_unknown_container_never_assumed_clear():
    summary = module.summarize([synthetic(module.TARGETS[0])])
    assert summary["containers"][1]["state"] == "UNKNOWN"
    assert summary["containers"][1]["sensitive_variable_name_count"] is None
    assert summary["owner_custody_required"] is True


@pytest.mark.parametrize(
    "value",
    [
        [{"Name": "/unapproved", "Config": {"Env": []}}],
        [synthetic("assistx-api"), synthetic("assistx-api")],
        [{"Name": "/assistx-api", "Config": {"Env": [None]}}],
        [{"Name": "/assistx-api", "Config": {"Env": ["broken"]}}],
    ],
)
def test_malformed_or_unknown_container_fails_closed(value):
    with pytest.raises(ValueError):
        module.summarize(value)


def test_docker_error_does_not_output_inspect_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
):
    class Failed:
        returncode = 1
        stdout = b'{"Config":{"Env":["API_TOKEN=SIMULATED_PRIVATE_999"]}}'

    def no_docker(*args, **kwargs):
        return Failed()

    monkeypatch.setattr(module.subprocess, "run", no_docker)
    assert module.main() == 2
    printed = capsys.readouterr().out
    assert "SIMULATED_PRIVATE_999" not in printed
    assert "UNKNOWN" in printed
    assert "production_release_authorized" in printed
