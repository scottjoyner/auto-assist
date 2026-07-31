from __future__ import annotations

import time

import pytest

from assistx.continuity_replicator import (
    ContinuityReplicationError,
    ProjectionReplicator,
    ReplicationDocument,
    validate_context_projection,
    validate_runtime_projection,
)


def runtime_projection(expires_at_ms: int | None = None):
    now = int(time.time() * 1000)
    return {
        "generation": 7,
        "generated_at_ms": now,
        "expires_at_ms": expires_at_ms or now + 120_000,
        "checksum": "a" * 64,
        "signature": "b" * 64,
        "providers": [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "access_urls": [
                    "http://192.168.1.9:1234/v1",
                    "http://100.64.0.9:1234/v1",
                ],
                "models": [
                    {
                        "model_instance_id": "qwen-xwing-1",
                        "artifact_fingerprint": "sha256:abc",
                    }
                ],
            }
        ],
    }


def test_runtime_projection_rejects_expired_or_incomplete_data():
    now = int(time.time() * 1000)
    with pytest.raises(ContinuityReplicationError, match="expired"):
        validate_runtime_projection(runtime_projection(now - 1), now=now)

    incomplete = runtime_projection(now + 60_000)
    incomplete["providers"][0]["access_urls"] = []
    with pytest.raises(ContinuityReplicationError, match="identity is incomplete"):
        validate_runtime_projection(incomplete, now=now)


def test_context_projection_rejects_raw_prompt_material():
    now = int(time.time() * 1000)
    with pytest.raises(ContinuityReplicationError, match="forbidden raw context"):
        validate_context_projection(
            {
                "expires_at_ms": now + 60_000,
                "contexts": [{"cache_id": "k1", "prompt": "secret"}],
            },
            now=now,
        )


def test_replicator_fails_over_and_writes_epoch_bound_document():
    projection = runtime_projection()
    calls = []

    def http(method, url, **kwargs):
        calls.append((method, url, kwargs.get("data")))
        if url == "http://primary/runtime":
            return 200, projection
        if url.startswith("http://lan"):
            return 0, {"error": "lan down"}
        if url == "http://tailnet/v1/continuity/status":
            return 200, {
                "cluster_id": "fleet",
                "node_id": "beelink",
                "epoch": 9,
            }
        if url == "http://tailnet/v1/continuity/documents/runtime-projection":
            return 200, {"name": "runtime-projection"}
        return 404, {}

    replicator = ProjectionReplicator(
        documents=[
            ReplicationDocument(
                "runtime-projection",
                "http://primary/runtime",
                "runtime_projection",
            )
        ],
        target_urls=["http://lan", "http://tailnet"],
        target_token="continuity-token-123456",
        expected_cluster_id="fleet",
        expected_controller_ids=["beelink"],
        http=http,
    )
    result = replicator.replicate_once()
    assert result["ok"] is True
    assert result["target"] == "http://tailnet"
    put = next(call for call in calls if call[0] == "PUT")
    assert put[2]["epoch"] == 9
    assert put[2]["payload"]["generation"] == 7


def test_replicator_skips_unchanged_document_before_refresh_window():
    projection = runtime_projection()
    puts = []

    def http(method, url, **kwargs):
        if url == "http://primary/runtime":
            return 200, projection
        if url.endswith("/v1/continuity/status"):
            return 200, {
                "cluster_id": "fleet",
                "node_id": "beelink",
                "epoch": 3,
            }
        if method == "PUT":
            puts.append(kwargs["data"])
            return 200, {}
        return 404, {}

    replicator = ProjectionReplicator(
        documents=[
            ReplicationDocument(
                "runtime-projection",
                "http://primary/runtime",
                "runtime_projection",
            )
        ],
        target_urls=["http://beelink"],
        target_token="continuity-token-123456",
        expected_cluster_id="fleet",
        expected_controller_ids=["beelink"],
        http=http,
    )
    assert replicator.replicate_once()["documents"][0]["action"] == "updated"
    assert replicator.replicate_once()["documents"][0]["action"] == "unchanged"
    assert len(puts) == 1


def test_optional_document_failure_does_not_fail_required_replication():
    projection = runtime_projection()

    def http(method, url, **_kwargs):
        if url == "http://primary/runtime":
            return 200, projection
        if url == "http://primary/context":
            return 503, {"error": "not ready"}
        if url.endswith("/v1/continuity/status"):
            return 200, {
                "cluster_id": "fleet",
                "node_id": "beelink",
                "epoch": 2,
            }
        if method == "PUT":
            return 200, {}
        return 404, {}

    replicator = ProjectionReplicator(
        documents=[
            ReplicationDocument(
                "runtime-projection",
                "http://primary/runtime",
                "runtime_projection",
                required=True,
            ),
            ReplicationDocument(
                "context-projection",
                "http://primary/context",
                "context_projection",
                required=False,
            ),
        ],
        target_urls=["http://beelink"],
        target_token="continuity-token-123456",
        expected_cluster_id="fleet",
        expected_controller_ids=["beelink"],
        http=http,
    )
    result = replicator.replicate_once()
    assert result["ok"] is True
    assert result["documents"][1]["ok"] is False
    assert result["documents"][1]["required"] is False
