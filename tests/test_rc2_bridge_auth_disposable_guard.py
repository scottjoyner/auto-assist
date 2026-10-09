"""Offline immutability and denial guards for synthetic bridge-auth experiment.

No Docker, HTTP, local service, credential or graph activity is executed here.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_rc2_bridge_auth_isolated.py"
spec = importlib.util.spec_from_file_location("bridge_auth_canary", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def argv(approved=False):
    return (["--approve-disposable-bridge-auth"] if approved else []) + [
        "--expected-source-sha", "a" * 40,
        "--server-image-id", "sha256:" + "b" * 64,
        "--client-image-id", "sha256:" + "c" * 64,
    ]


def test_missing_approval_does_not_execute_any_command(monkeypatch):
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *a, **kw: pytest.fail("unapproved process invocation"),
    )
    assert module.main(argv()) == 2


@pytest.mark.parametrize("flag,bad", [
    ("--expected-source-sha", "short"),
    ("--server-image-id", "git-assistx:latest"),
    ("--client-image-id", "invalid"),
])
def test_invalid_sha_never_launches_docker(monkeypatch, flag, bad):
    args = argv(True)
    args[args.index(flag) + 1] = bad
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *a, **kw: pytest.fail("invalid preflight launched subprocess"),
    )
    assert module.main(args) == 2


def test_external_network_never_satisfies_isolation(monkeypatch):
    payload = [{
        "Id": "a" * 64, "Name": module.NETWORK,
        "Internal": False, "Attachable": False,
        "Ingress": False, "Containers": {},
    }]
    monkeypatch.setattr(module, "okay", lambda *a, **kw: json.dumps(payload))
    with pytest.raises(RuntimeError, match="not an approved"):
        module.ensure_network("a" * 64, set())


def test_unexpected_bridge_peer_fails_closed(monkeypatch):
    payload = [{
        "Id": "a" * 64, "Name": module.NETWORK,
        "Internal": True, "Attachable": False,
        "Ingress": False,
        "Containers": {"1": {"Name": "unexpected"}},
    }]
    monkeypatch.setattr(module, "okay", lambda *a, **kw: json.dumps(payload))
    with pytest.raises(RuntimeError, match="unexpected"):
        module.ensure_network("a" * 64, set())


def test_runner_mounts_only_source_and_uses_immutable_images():
    text = SCRIPT.read_text(encoding="utf8")
    assert '"--internal"' in text
    assert '"--no-access-log"' in text
    assert 'args.server_image_id' in text
    assert 'args.client_image_id' in text
    assert '"--read-only"' in text
    assert '"--cap-drop","ALL"' in text
    assert 'source={ROOT/' in text
    assert 'readonly' in text
    assert "TRUSTED_AUTH_HEADER=Tailscale-User-Login" in text
    assert "ASSISTX_REQUIRE_BASIC_AUTH=1" in text
    assert "ASSISTX_REQUIRE_BASIC_AUTH=0" in text
    assert "BRIDGE_AUTH_DIFFERENTIAL_DISPOSABLE_PASS" in text
    for forbidden in ("/nas/", "NEO4J_PASSWORD=", "PAPERCLIP_TOKEN=",
                      "OPENAI_API_KEY=", '"--publish"', '"--privileged"'):
        assert forbidden not in text


def test_peer_has_deny_401_and_valid_200_witnesses():
    code = module.PEER
    assert '"anonymous":401' in code
    assert '"wrong_basic":401' in code
    assert '"valid_basic":200' in code
    assert '"traces_forged":401' in code
    assert '"dashboard_api_forged":401' in code
    assert 'observed["forged"] == 200' in code
