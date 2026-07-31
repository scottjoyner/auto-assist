from __future__ import annotations

from pathlib import Path

import pytest

from assistx.continuity_node_agent import (
    ContinuityAgentError,
    ContinuityClient,
    ContinuityEpochRollback,
    ContinuityNodeAgent,
    EpochGuard,
    SafeContinuityExecutor,
)


def test_epoch_guard_persists_highest_epoch(tmp_path):
    guard = EpochGuard(tmp_path / "epoch.json")
    assert guard.accept(4) == 4
    assert guard.accept(4) == 4
    with pytest.raises(ContinuityEpochRollback, match="rollback rejected"):
        guard.accept(3)


def test_client_fails_over_and_checks_controller_identity():
    calls = []

    def http(method, url, **_kwargs):
        calls.append(url)
        if url.startswith("http://lan"):
            return 0, {"error": "down"}
        return 200, {
            "cluster_id": "fleet",
            "node_id": "beelink",
            "epoch": 7,
        }

    client = ContinuityClient(
        ["http://lan", "http://tailnet"],
        token="continuity-token-123456",
        expected_cluster_id="fleet",
        expected_controller_ids=["beelink"],
        http=http,
    )
    assert client.status()["epoch"] == 7
    assert client.active_url == "http://tailnet"
    assert calls == [
        "http://lan/v1/continuity/status",
        "http://tailnet/v1/continuity/status",
    ]


def test_runtime_and_private_http_probes_are_bounded():
    def http(_method, url, **_kwargs):
        if url.endswith("/v1/models"):
            return 200, {"data": [{"id": "local/qwen"}]}
        return 200, {"ok": True}

    executor = SafeContinuityExecutor(
        lmstudio_url="http://127.0.0.1:1234",
        artifact_roots=[],
        http_allowlist=["http://127.0.0.1:8088"],
        http=http,
    )
    runtime = executor.execute({"kind": "runtime_probe", "payload": {}})
    assert runtime["models"] == ["local/qwen"]

    probe = executor.execute(
        {
            "kind": "http_probe",
            "payload": {"url": "http://127.0.0.1:8088/health"},
        }
    )
    assert probe["status_code"] == 200
    assert "body_sha256" in probe

    with pytest.raises(ContinuityAgentError, match="not allowlisted"):
        executor.execute(
            {
                "kind": "http_probe",
                "payload": {"url": "http://127.0.0.1:9999/health"},
            }
        )


def test_artifact_and_backup_handlers_reject_path_escape(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    (root / "system-1.backup").write_bytes(b"system")
    (root / "neo4j-1.backup").write_bytes(b"neo4j")
    executor = SafeContinuityExecutor(
        lmstudio_url=None,
        artifact_roots=[str(root)],
        http_allowlist=[],
    )

    checksum = executor.execute(
        {
            "kind": "artifact_checksum",
            "payload": {"path": str(root / "neo4j-1.backup")},
        }
    )
    assert checksum["bytes"] == 5

    verified = executor.execute(
        {
            "kind": "backup_verify",
            "payload": {
                "path": str(root),
                "required_databases": ["system", "neo4j"],
            },
        }
    )
    assert verified["artifact_count"] == 2
    assert len(verified["artifact_set_sha256"]) == 64

    outside = tmp_path / "outside.dump"
    outside.write_bytes(b"escape")
    with pytest.raises(ContinuityAgentError, match="outside every"):
        executor.execute(
            {
                "kind": "artifact_checksum",
                "payload": {"path": str(outside)},
            }
        )


def test_agent_claims_and_completes_allowlisted_task(tmp_path):
    class Client:
        active_url = "http://beelink"

        def __init__(self):
            self.completed = None

        def status(self):
            return {"cluster_id": "fleet", "node_id": "beelink", "epoch": 2}

        def heartbeat(self, report):
            return dict(report)

        def claim(self, **_kwargs):
            return {
                "task_id": "task-1",
                "claim_token": "claim-1",
                "kind": "artifact_checksum",
                "payload": {"path": str(tmp_path / "artifact.bin")},
            }

        def complete(self, **kwargs):
            self.completed = kwargs
            return {"task": {"state": kwargs["status"]}}

    (tmp_path / "artifact.bin").write_bytes(b"artifact")
    client = Client()
    agent = ContinuityNodeAgent(
        client=client,  # type: ignore[arg-type]
        executor=SafeContinuityExecutor(
            lmstudio_url=None,
            artifact_roots=[str(tmp_path)],
            http_allowlist=[],
        ),
        node_id="xwing",
        epoch_guard=EpochGuard(tmp_path / "epoch.json"),
        poll_interval=2,
    )
    result = agent.run_once()
    assert result["ok"] is True
    assert client.completed["status"] == "completed"
    assert len(client.completed["result"]["sha256"]) == 64


def test_agent_reports_failure_for_non_allowlisted_kind(tmp_path):
    class Client:
        active_url = "http://beelink"

        def __init__(self):
            self.completed = None

        def status(self):
            return {"cluster_id": "fleet", "node_id": "beelink", "epoch": 1}

        def heartbeat(self, report):
            return dict(report)

        def claim(self, **_kwargs):
            return {
                "task_id": "task-shell",
                "claim_token": "claim-shell",
                "kind": "script",
                "payload": {"command": "rm -rf /"},
            }

        def complete(self, **kwargs):
            self.completed = kwargs
            return {"task": {"state": kwargs["status"]}}

    client = Client()
    agent = ContinuityNodeAgent(
        client=client,  # type: ignore[arg-type]
        executor=SafeContinuityExecutor(
            lmstudio_url=None,
            artifact_roots=[str(tmp_path)],
            http_allowlist=[],
        ),
        node_id="xwing",
        epoch_guard=EpochGuard(tmp_path / "epoch.json"),
        poll_interval=2,
    )
    result = agent.run_once()
    assert result["ok"] is False
    assert client.completed["status"] == "failed"
    assert "not allowlisted" in client.completed["result"]["error"]
