"""Test-only hook for real normal Hermes startup + patched adapter.

No real tasks or model/network subprocesses are authorized.
"""
from __future__ import annotations
import os
import pathlib
import runpy
import subprocess
from unittest.mock import patch
from assistx.agents import hermes_agent_adapter as adapter

ORIGINAL = adapter.run_loop
COUNT = [0]

class EmptyReply:
    def raise_for_status(self):
        pass
    def json(self):
        return {"items": []}

def fake_http(method, url, **kwargs):
    assert method == "GET" and url.startswith("http://127.0.0.1:1/api/agent/tasks?")
    assert kwargs.get("headers", {}).get("Authorization") == "Bearer SYNTHETIC_BOOTSTRAP_ONLY"
    assert kwargs.get("headers", {}).get("X-AssistX-Agent-Id") == "hermes-synthetic-canary"
    COUNT[0] += 1
    return EmptyReply()

def forbidden_child(*args, **kwargs):
    raise AssertionError("Hermes subprocess forbidden in synthetic mode")

def synthetic_dispatch():
    assert os.geteuid() == 1000 and adapter._strict_executor_auth_installed
    assert adapter.KNOWLEDGE_SPLIT_ENABLED
    assert os.environ["HERMES_REQUIRE_MOUNT_ISOLATION"] == "true"
    assert os.environ["HERMES_REQUIRE_SIGNED_CORPUS"] == "true"
    assert os.environ["HERMES_CORPUS_TRUST_KEY_PATH"] == "/app/attestation/trust.pub"
    assert os.environ["HERMES_KNOWLEDGE_WRITE_MOUNT"] == "/workspace"
    assert pathlib.Path("/workspace/hermes-home/config.yaml").is_file()
    assert pathlib.Path("/workspace/.hermes/config.yaml").is_symlink()
    with patch("requests.request", side_effect=fake_http),          patch.object(subprocess, "Popen", side_effect=forbidden_child),          patch.object(adapter, "fleet", None),          patch.object(adapter, "swarm_memory", None),          patch.object(adapter, "SELFTASK_BULK_MODELS", ["synthetic/model"]),          patch.object(adapter, "HERMES_DELEGATE_OPENCODE_TIERS", []):
        runpy.run_path("/workspace/smoke.py", run_name="__main__")
        # The real adapter's background job launcher is intentionally
        # disabled only for the no-task poll after the self-task fixture.
        with patch.object(adapter, "SELFTASK_BULK_MODELS", []):
            ORIGINAL(once=True)
    assert COUNT[0] == 1
    print("HERMES_SPLIT_NORMAL_ENTRYPOINT_PASS", flush=True)

adapter.run_loop = synthetic_dispatch
