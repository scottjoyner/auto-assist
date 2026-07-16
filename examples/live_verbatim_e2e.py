#!/usr/bin/env python3
"""
REAL end-to-end run of the verbatim return-contract chain.

Unlike verbatim_chain_demo.py (which mocks opencode), this drives the actual
stack:

    auto-ingest task (created in AssistX, target_agent_id=this adapter)
      -> auto-assist hermes_agent_adapter.process_task()
         -> hermes chat (real subprocess, parent agent = tool-small tier)
            -> delegate_task(provider="opencode-cli", return_format="verbatim")
               -> opencode run (real child session) returns the token
         -> task completed with result.output = the verbatim token
      -> auto-assign / auto-router consume result.output

Prereqs (all live in this environment):
    * AssistX on ASSISTX_API_URL (default http://localhost:8000)
    * Neo4j on bolt://127.0.0.1:7687
    * opencode on PATH
    * hermes on PATH with the opencode-cli provider + delegate_task return_format

Run:
    HERMES_DELEGATE_OPENCODE_TIERS=tool-small HERMES_DELEGATE_RETURN_FORMAT=verbatim \
        python examples/live_verbatim_e2e.py
"""
from __future__ import annotations

import importlib
import os
import sys
import time
import uuid

# --- configure the delegation seam BEFORE importing the adapter -------------
AGENT_ID = f"hermes-delegtest-{uuid.uuid4().hex[:8]}"
os.environ["HERMES_AGENT_ID"] = AGENT_ID
os.environ.setdefault("HERMES_DELEGATE_OPENCODE_TIERS", "tool-small")
os.environ.setdefault("HERMES_DELEGATE_RETURN_FORMAT", "verbatim")
os.environ.setdefault("HERMES_PROVIDER", "qwen35-9b-local")
os.environ.setdefault("HERMES_MODEL", "refinedtoolcallv5-3b")
os.environ.setdefault("HERMES_PROFILES_PATH", "/tmp/live-profiles.yaml")
os.environ.setdefault("HERMES_EVAL_PATH", "/tmp/live-model-profiles.json")
os.environ.setdefault("ASSISTX_API_URL", "http://localhost:8000")
os.environ.setdefault("ASSISTX_AUTH_USER", "admin")
os.environ.setdefault("ASSISTX_AUTH_PASS", "change-me")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import assistx.agents.hermes_agent_adapter as adapter  # noqa: E402

importlib.reload(adapter)  # apply env-driven module constants


def main() -> int:
    expected = "PONG"
    title = "fix a typo in the ingest tagger"  # -> tool-small tier -> delegated
    description = (
        f"Reply with exactly the word {expected}\n\n"
        "Return ONLY the exact value and nothing else."
    )

    # 1) auto-ingest side: create the READY task, targeted at this adapter so the
    #    production adapter (agent_id=hermes-local) never sees/claims it.
    from assistx.neo4j_client import Neo4jClient  # type: ignore

    neo = Neo4jClient()
    try:
        res = neo.create_task_with_context(
            title=title,
            task_type="swarm_task",
            status="READY",
            kind="swarm_task",
            target_agent_id=AGENT_ID,
            payload={
                "return_format": "verbatim",
                "expected": expected,
                "source_repo": "auto-ingest",
                "contract": "verbatim",
            },
        )
    finally:
        neo.close()
    task_id = res["task_id"]
    print(f"[auto-ingest] created task {task_id} (target_agent_id={AGENT_ID})")

    assistx = adapter.AssistXClient()

    # 2) auto-assist side: process the task with the REAL hermes subprocess.
    task = {"id": task_id, "title": title, "description": description, "kind": "swarm_task"}
    print(f"[auto-assist] process_task -> hermes chat -> delegate_task(opencode-cli, verbatim) ...")
    t0 = time.time()
    try:
        adapter.process_task(assistx, task)
    except Exception as e:  # surface but still report the task state
        print(f"[auto-assist] process_task raised: {e}")
    print(f"[auto-assist] process_task returned in {time.time()-t0:.1f}s")

    # 3) consume the verbatim result (what auto-assign / auto-router read).
    import requests

    doc = requests.get(
        f"{adapter.ASSISTX_URL}/api/tasks/{task_id}",
        auth=(adapter.ASSISTX_USER, adapter.ASSISTX_PASS),
        timeout=30,
    ).json()
    status = doc.get("task", {}).get("status")
    output = (doc.get("task", {}).get("result") or {}).get("output")
    print(f"[consumer]   task status   : {status}")
    print(f"[consumer]   result.output : {output!r}")
    assert status == "DONE", f"expected DONE, got {status}"
    assert output and expected in output, f"expected verbatim {expected!r} in {output!r}"
    print("\nOK: live auto-ingest -> delegate_task(opencode-cli, verbatim) -> auto-assign/auto-router consume result.output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
