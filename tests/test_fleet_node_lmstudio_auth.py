"""Opt-in LM Studio bearer auth for the real AssistX fleet node HTTP helper.

Hermetic: no real tokens, servers, GPUs, task dispatch, or network access.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import urllib.error
import urllib.request

import pytest
from assistx import fleet_node_agent
from assistx import lm_studio_client_auth as lm_auth

FAKE = "synthetic-agent-only-credential-20261024"


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "lmstudio-api-token"
    path.write_text(FAKE)
    path.chmod(0o600)
    return path


class Response:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def read(self):
        return b'{"data":[{"id":"synthetic"}]}'


def test_default_legacy_maintains_existing_get(monkeypatch):
    monkeypatch.delenv("FLEET_LM_AUTH_POLICY", raising=False)
    monkeypatch.delenv("FLEET_LM_TOKEN_FILE", raising=False)
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: (seen.append(req), Response())[1])
    status, body = fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")
    assert status == 200 and len(body["data"]) == 1
    assert seen[0].get_header("Authorization") is None


def test_explicit_required_get_has_private_bearer(monkeypatch, token_file):
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(token_file))
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: (seen.append(req), Response())[1])
    status, _ = fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")
    assert status == 200
    assert seen[0].get_header("Authorization") == "Bearer " + FAKE


def test_explicit_required_chat_post_has_bearer(monkeypatch, token_file):
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(token_file))
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: (seen.append(req), Response())[1])
    status, _ = fleet_node_agent._http("POST", "http://localhost:1234/v1/chat/completions",
                                       data={"model": "synthetic", "messages": []})
    assert status == 200
    assert seen[0].get_method() == "POST"
    assert seen[0].get_header("Authorization") == "Bearer " + FAKE


def test_required_missing_file_fails_before_network(monkeypatch):
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.delenv("FLEET_LM_TOKEN_FILE", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *args, **kwargs: pytest.fail("network should not run"))
    with pytest.raises(lm_auth.LMStudioAuthHold, match="token_path_required"):
        fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")


def test_no_silent_false_zero_library_on_invalid_credentials(monkeypatch):
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.delenv("FLEET_LM_TOKEN_FILE", raising=False)
    with pytest.raises(lm_auth.LMStudioAuthHold):
        fleet_node_agent._detect_capabilities("http://127.0.0.1:1234")


def test_foreign_assistx_basic_auth_preserved(monkeypatch, token_file):
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(token_file))
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: (seen.append(req), Response())[1])
    status, _ = fleet_node_agent._http(
        "GET", "http://100.64.43.123:8000/api/agent/tasks", auth=("alice", "example"),
    )
    assert status == 200
    assert seen[0].get_header("Authorization").startswith("Basic ")


def test_router_receives_no_lm_bearer(monkeypatch, token_file):
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(token_file))
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: (seen.append(req), Response())[1])
    fleet_node_agent._http("GET", "http://100.64.43.123:8088/api/fleet/nodes")
    assert seen[0].get_header("Authorization") is None


@pytest.mark.parametrize("policy", ["unknown", "legacy"])
def test_token_with_nonrequired_policy_denied(monkeypatch, token_file, policy):
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", policy)
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(token_file))
    with pytest.raises(lm_auth.LMStudioAuthHold):
        fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:99999/v1/models",
    "http://127.0.0.1:notaport/v1/models",
    "http://127.0.0.1:1234/admin",
    "http://user:pass@127.0.0.1:1234/v1/models",
    "http://127.0.0.1:1234/v1/models?redirect=1",
    "https://127.0.0.1:1234/v1/models",
])
def test_local_unsafe_model_urls_denied(monkeypatch, token_file, url):
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(token_file))
    with pytest.raises(lm_auth.LMStudioAuthHold):
        fleet_node_agent._http("GET", url)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o666])
def test_world_or_group_readable_tokens_denied(monkeypatch, token_file, mode):
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(token_file))
    token_file.chmod(mode)
    with pytest.raises(lm_auth.LMStudioAuthHold):
        fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")


def test_symlink_token_denied(monkeypatch, token_file):
    link = token_file.parent / "alias"
    link.symlink_to(token_file)
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(link))
    with pytest.raises(lm_auth.LMStudioAuthHold):
        fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")


def test_bad_token_newlines_denied(monkeypatch, token_file):
    token_file.write_text(FAKE + "\nAuthorization: other")
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(token_file))
    with pytest.raises(lm_auth.LMStudioAuthHold):
        fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")


def test_401_response_is_closed(monkeypatch, token_file):
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(token_file))
    exc = urllib.error.HTTPError(
        "http://127.0.0.1:1234/v1/models", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"invalid_token"}')
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kw: (_ for _ in ()).throw(exc))
    status, body = fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")
    assert status == 401 and body["error"] == "invalid_token"
    assert exc.fp is None or exc.fp.closed


def test_conflicting_authorization_denied(monkeypatch, token_file):
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(token_file))
    with pytest.raises(lm_auth.LMStudioAuthHold):
        fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models",
                               headers={"authorization": "Bearer injected"})
