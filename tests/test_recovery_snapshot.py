from __future__ import annotations

import json

import pytest

from assistx import recovery_snapshot
from assistx.recovery_snapshot import (
    RecoverySnapshotReplicator,
    private_http_url,
)
from assistx.runtime_projection import projection_checksum, projection_signature

SECRET = "snapshot-secret"


def projection():
    value = {
        "schema_version": "1",
        "source": "assistx",
        "generation": 4,
        "revision": "fleet-4",
        "generated_at_ms": 1_000,
        "expires_at_ms": 61_000,
        "providers": [
            {
                "name": "xwing",
                "type": "lmstudio",
                "enabled": True,
                "quota_class": "local",
                "base_url": "http://192.168.1.9:1234/v1",
                "access_urls": ["http://192.168.1.9:1234/v1"],
                "models": [],
            }
        ],
    }
    value["checksum"] = projection_checksum(value)
    value["signature"] = projection_signature(
        value["generation"],
        value["checksum"],
        value["generated_at_ms"],
        value["expires_at_ms"],
        SECRET,
    )
    return value


def test_snapshot_replication_verifies_and_writes_atomically(tmp_path, monkeypatch):
    document = projection()
    calls = []

    def request_json(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET":
            return document
        return {"record_id": "runtime_projection:canonical"}

    monkeypatch.setattr(recovery_snapshot, "_request_json", request_json)
    path = tmp_path / "runtime-projection.json"
    replicator = RecoverySnapshotReplicator(
        source_url="http://primary.lan:8000",
        target_url="http://127.0.0.1:27900",
        source_auth=("source", "pass"),
        target_auth=("target", "pass"),
        secret=SECRET,
        snapshot_path=path,
    )

    result = replicator.replicate()

    assert result["ok"] is True
    assert json.loads(path.read_text())["checksum"] == document["checksum"]
    assert calls[0][1].endswith("/api/router/runtime-projection")
    assert calls[1][1].endswith("/api/degraded/runtime-projection/publish")
    assert path.stat().st_mode & 0o777 == 0o600


def test_public_snapshot_endpoint_is_rejected():
    assert private_http_url("http://192.168.1.9:8000") is True
    assert private_http_url("https://node.tailnet.ts.net") is True
    assert private_http_url("https://example.com") is False

    with pytest.raises(ValueError, match="private or loopback"):
        RecoverySnapshotReplicator(
            source_url="https://example.com",
            target_url="http://127.0.0.1:27900",
            source_auth=("a", "b"),
            target_auth=("c", "d"),
            secret=SECRET,
            snapshot_path="/tmp/snapshot.json",
        )


def test_tampered_projection_never_reaches_recovery_target(tmp_path, monkeypatch):
    document = projection()
    document["generation"] = 99
    posted = []

    def request_json(method, _url, **_kwargs):
        if method == "POST":
            posted.append(True)
        return document

    monkeypatch.setattr(recovery_snapshot, "_request_json", request_json)
    replicator = RecoverySnapshotReplicator(
        source_url="http://primary.lan:8000",
        target_url="http://127.0.0.1:27900",
        source_auth=("a", "b"),
        target_auth=("c", "d"),
        secret=SECRET,
        snapshot_path=tmp_path / "snapshot.json",
    )

    with pytest.raises(ValueError, match="checksum mismatch"):
        replicator.replicate()
    assert posted == []
