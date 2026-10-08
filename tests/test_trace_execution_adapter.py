"""Synthetic trace execution acceptance; never invokes shell or network."""

from __future__ import annotations

import json
import os
import threading

import pytest

from assistx import fleet_node_agent
from assistx.trace_execution_adapter import (
    TraceDenied,
    TraceReceiptStore,
    run_trace_probe,
)


def prepared_root(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    return root


def make_task(*, task_id="task-1", target="node-a", command="probe.echo.v1", message="SAFE"):
    return {
        "id": task_id,
        "target_agent_id": target,
        "task_type": "trace_probe",
        "ticket_type": "trace_probe",
        "kind": "trace_probe",
        "payload": {"command_id": command, "message": message},
    }


def probe(root, task=None, *, claim_id="claim-1", node_id="node-a", enabled=True):
    return run_trace_probe(
        task or make_task(), node_id=node_id, claim_id=claim_id, audit_root=str(root), enabled=enabled
    )


def test_success_is_durably_audited_with_hashes_and_no_raw_payload(tmp_path):
    root = prepared_root(tmp_path)
    out = probe(root)
    assert out["status"] == "DONE"
    assert out["result"] == {"kind": "echo", "message": "SAFE"}
    receipt = TraceReceiptStore(str(root), node_id="node-a").verify()
    assert receipt["ok"] and receipt["records"] == 2
    rows = [json.loads(line) for line in (root / "journal.jsonl").read_text().splitlines()]
    assert [r["event"] for r in rows] == ["prepared", "completed"]
    assert rows[1]["prepared_hash"] == rows[0]["entry_hash"]
    assert rows[1]["prev_hash"] == rows[0]["entry_hash"]
    assert rows[0]["input_sha256"] != rows[1]["output_sha256"]
    assert "SAFE" not in (root / "journal.jsonl").read_text()
    assert (root / "journal.jsonl").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"enabled": False}, "trace_probe_disabled"),
        ({"claim_id": ""}, "invalid_claim_id"),
        ({"node_id": "node-b"}, "wrong_execution_node"),
        ({"task": make_task(command="shell.command")}, "unapproved_command_id"),
        (
            {
                "task": {
                    "id": "task-1",
                    "target_agent_id": "node-a",
                    "payload": {"command_id": "probe.noop.v1", "command": "rm -rf /"},
                }
            },
            "unapproved_probe_fields",
        ),
        ({"task": make_task(message="x" * 161)}, "invalid_probe_message"),
        ({"task": make_task(target="node-b")}, "wrong_execution_node"),
    ],
)
def test_denied_before_any_execution_or_audit_write(tmp_path, changes, reason):
    root = prepared_root(tmp_path)
    with pytest.raises(TraceDenied, match=reason):
        probe(root, **changes)
    assert not (root / "journal.jsonl").exists()


def test_noop_and_duplicate_claim_cannot_rerun(tmp_path):
    root = prepared_root(tmp_path)
    task = make_task(command="probe.noop.v1", message="")
    assert probe(root, task=task)["result"] == {"kind": "noop"}
    with pytest.raises(TraceDenied, match="attempt_already_recorded"):
        probe(root, task=task)
    assert TraceReceiptStore(str(root), node_id="node-a").verify()["records"] == 2


def test_durable_preparation_failure_prevents_action(tmp_path, monkeypatch):
    root = prepared_root(tmp_path)

    def fail_first(_self, _event, **_kwargs):
        raise TraceDenied("disk_unavailable")

    monkeypatch.setattr(TraceReceiptStore, "append", fail_first)
    with pytest.raises(TraceDenied, match="disk_unavailable"):
        probe(root)
    assert not (root / "journal.jsonl").exists()


def test_completion_failure_is_not_reported_as_success_and_blocks_retry(tmp_path, monkeypatch):
    root = prepared_root(tmp_path)
    original = TraceReceiptStore.append

    def fail_completion(self, event, *, first=False):
        if event["event"] == "completed":
            raise TraceDenied("completion_disk_failure")
        return original(self, event, first=first)

    monkeypatch.setattr(TraceReceiptStore, "append", fail_completion)
    with pytest.raises(TraceDenied, match="completion_disk_failure"):
        probe(root)
    assert TraceReceiptStore(str(root), node_id="node-a").verify()["records"] == 1
    with pytest.raises(TraceDenied, match="attempt_already_recorded"):
        probe(root)


def test_tampered_journal_is_denied(tmp_path):
    root = prepared_root(tmp_path)
    probe(root)
    with open(root / "journal.jsonl", "ab") as stream:
        stream.write(b'{"tampered":true}\n')
    with pytest.raises(TraceDenied, match="audit_chain_invalid"):
        probe(root, task=make_task(task_id="task-2"), claim_id="claim-2")
    with pytest.raises(TraceDenied, match="audit_chain_invalid"):
        TraceReceiptStore(str(root), node_id="node-a").verify()


def test_symlink_and_public_root_are_rejected(tmp_path):
    root = prepared_root(tmp_path)
    link = tmp_path / "shortcut"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(TraceDenied, match="trace_root_ownership_invalid"):
        probe(link)
    root.chmod(0o755)
    with pytest.raises(TraceDenied, match="trace_root_permissions_unsafe"):
        probe(root)


def test_concurrent_same_claim_exactly_one_execution(tmp_path):
    root = prepared_root(tmp_path)
    outcomes = []
    barrier = threading.Barrier(2)

    def work():
        barrier.wait()
        try:
            outcomes.append(("DONE", probe(root)["status"]))
        except TraceDenied as exc:
            outcomes.append(("DENIED", str(exc)))

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(row[0] for row in outcomes) == ["DENIED", "DONE"]
    assert TraceReceiptStore(str(root), node_id="node-a").verify()["records"] == 2


def test_live_claim_is_blocked_until_signed_status_gate_is_wired(tmp_path, monkeypatch):
    root = prepared_root(tmp_path)
    monkeypatch.setenv("FLEET_TRACE_EXECUTION_AUDIT_ROOT", str(root))
    monkeypatch.setenv("FLEET_TRACE_PROBE_ENABLED", "true")
    task = make_task()
    observed = []

    def fake_http(method, url, *, data=None, **kwargs):
        observed.append((method, url, data))
        if url.endswith("/claim"):
            authoritative = {**task, "claim_id": "claim-from-assistx"}
            return (200, {"claimed": True, "task": authoritative})
        return (200, {"ok": True})

    monkeypatch.setattr(fleet_node_agent, "_http", fake_http)
    fleet_node_agent._claim_and_run(
        assistx_url="http://test.invalid",
        router_url="http://not-used.invalid",
        auth=None,
        node_id="node-a",
        caps=["trace-probe"],
        task=task,
        lmstudio_url=None,
    )
    completions = [entry[2] for entry in observed if entry[1].endswith("/complete")]
    assert len(completions) == 1
    assert completions[0]["status"] == "FAILED"
    assert completions[0]["claim_id"] == "claim-from-assistx"
    assert not (root / "journal.jsonl").exists()


def test_advertisement_requires_opt_in_and_safe_storage(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEET_TRACE_PROBE_ENABLED", raising=False)
    monkeypatch.delenv("FLEET_LMSTUDIO_URL", raising=False)
    caps, _ = fleet_node_agent._detect_capabilities(None)
    assert "trace-probe" not in caps
    monkeypatch.setenv("FLEET_TRACE_PROBE_ENABLED", "true")
    monkeypatch.setenv("FLEET_NODE_ID", "node-a")
    monkeypatch.setenv("FLEET_TRACE_EXECUTION_AUDIT_ROOT", str(tmp_path / "missing"))
    caps, _ = fleet_node_agent._detect_capabilities(None)
    assert "trace-probe" not in caps
    root = prepared_root(tmp_path)
    monkeypatch.setenv("FLEET_TRACE_EXECUTION_AUDIT_ROOT", str(root))
    caps, _ = fleet_node_agent._detect_capabilities(None)
    assert "trace-probe" not in caps


def test_cross_node_audit_root_identity_is_rejected(tmp_path):
    root = prepared_root(tmp_path)
    probe(root)
    with pytest.raises(TraceDenied, match="audit_node_identity_mismatch"):
        TraceReceiptStore(str(root), node_id="node-b").verify()
    with pytest.raises(TraceDenied, match="audit_node_identity_mismatch"):
        probe(root, task=make_task(task_id="task-2", target="node-b"), claim_id="claim-2", node_id="node-b")


def test_missing_authoritative_claim_fails_before_writing(tmp_path, monkeypatch):
    root = prepared_root(tmp_path)
    monkeypatch.setenv("FLEET_TRACE_EXECUTION_AUDIT_ROOT", str(root))
    monkeypatch.setenv("FLEET_TRACE_PROBE_ENABLED", "true")
    task = make_task()
    observed = []

    def fake_http(method, url, *, data=None, **kwargs):
        if url.endswith("/claim"):
            return (200, {"claimed": True, "claim_id": "raw", "task": {}})
        observed.append((url, data))
        return (200, {})

    monkeypatch.setattr(fleet_node_agent, "_http", fake_http)
    fleet_node_agent._claim_and_run(
        assistx_url="http://test.invalid",
        router_url="http://unused.invalid",
        auth=None,
        node_id="node-a",
        caps=["trace-probe"],
        task=task,
        lmstudio_url=None,
    )
    outcomes = [row[1] for row in observed if row[0].endswith("/complete")]
    assert outcomes[0]["status"] == "FAILED"
    assert not (root / "journal.jsonl").exists()


def test_conflicting_explicit_llm_type_cannot_bypass_trace_fence(tmp_path, monkeypatch):
    root = prepared_root(tmp_path)
    monkeypatch.setenv("FLEET_TRACE_EXECUTION_AUDIT_ROOT", str(root))
    monkeypatch.setenv("FLEET_TRACE_PROBE_ENABLED", "true")
    task = make_task()
    task["task_type"] = "llm"  # hostile type override, canonical markers remain trace_probe
    events = []

    def fake_http(method, url, *, data=None, **kwargs):
        events.append((url, data))
        if url.endswith("/claim"):
            return (200, {"claimed": True, "task": {**task, "claim_id": "claim-fenced"}})
        return (200, {"ok": True})

    def execution_must_never_run(*args, **kwargs):
        raise AssertionError("normal LLM executor was wrongly reached")

    monkeypatch.setattr(fleet_node_agent, "_http", fake_http)
    monkeypatch.setattr(fleet_node_agent, "execute_task", execution_must_never_run)
    fleet_node_agent._claim_and_run(
        assistx_url="http://test.invalid",
        router_url="http://not-used.invalid",
        auth=None,
        node_id="node-a",
        caps=["trace-probe"],
        task=task,
        lmstudio_url=None,
    )
    completions = [row[1] for row in events if row[0].endswith("/complete")]
    assert len(completions) == 1 and completions[0]["status"] == "FAILED"
    assert not (root / "journal.jsonl").exists()
