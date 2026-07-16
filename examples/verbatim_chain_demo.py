#!/usr/bin/env python3
"""
End-to-end demo of the verbatim return-contract chain, using the REAL
auto-assist adapter routing code.

It builds a task whose title classifies to the ``tool-small`` tier, mocks the
opencode-cli delegation (so no live opencode/AssistX/Neo4j is required), and
runs ``process_task()`` to prove the delegated machine-usable value lands in
``task.result.output`` -- exactly what auto-ingest emits and auto-assign /
auto-router consume.

Run:
    HERMES_DELEGATE_OPENCODE_TIERS=tool-small HERMES_DELEGATE_RETURN_FORMAT=verbatim \
        python examples/verbatim_chain_demo.py
"""
from __future__ import annotations

import importlib
import os
import sys
import unittest.mock as mock

# Ensure the adapter's module constants pick up the delegation env before import.
os.environ.setdefault("HERMES_DELEGATE_OPENCODE_TIERS", "tool-small")
os.environ.setdefault("HERMES_DELEGATE_RETURN_FORMAT", "verbatim")
os.environ.setdefault("HERMES_PROFILES_PATH", "/tmp/verbatim-demo-profiles.yaml")
os.environ.setdefault("HERMES_EVAL_PATH", "/tmp/verbatim-demo-model-profiles.json")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import src.assistx.agents.hermes_agent_adapter as adapter  # noqa: E402

importlib.reload(adapter)  # apply env-driven module constants


def main() -> int:
    expected = "PONG"
    task = {
        "id": "demo-1",
        "title": "fix a typo in the ingest tagger",  # -> tool-small tier
        "description": f"Reply with exactly the word {expected}",
    }

    # Simulate the opencode-cli child session returning the verbatim token.
    # We mock the inner run_hermes so the REAL run_hermes_delegated() builds its
    # directive (provider="opencode-cli", return_format="verbatim") and runs.
    def fake_run_hermes(prompt, timeout=None, model=None, provider=None, toolsets=None):
        assert 'provider="opencode-cli"' in prompt, "directive must force opencode-cli"
        assert 'return_format="verbatim"' in prompt, "directive must set return contract"
        assert "delegation" in (toolsets or ""), "delegation toolset must be enabled"
        return {"success": True, "output": expected, "session_id": "child", "elapsed": 0.5}

    with mock.patch.object(adapter, "run_hermes", fake_run_hermes):
        assistx = mock.MagicMock()
        assistx.claim_task.return_value = True
        assistx.get_context.return_value = {}

        adapter.process_task(assistx, task)

    assistx.complete_task.assert_called_once()
    _, kwargs = assistx.complete_task.call_args
    out = kwargs["result"]["output"]
    print(f"Task status     : {kwargs['status']}")
    print(f"result.output   : {out!r}")
    print(f"tier            : {kwargs['result'].get('tier')}")
    assert kwargs["status"] == "DONE"
    assert expected in out, f"expected verbatim token {expected!r} in {out!r}"
    print("\nOK: auto-ingest task -> delegate_task(opencode-cli, verbatim) -> auto-assign/auto-router consume result.output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
