from __future__ import annotations

import pytest

from assistx.continuity_state import (
    ContinuityConfig,
    ContinuityConflict,
    ContinuityRejected,
    InMemoryContinuityStore,
    build_signed_event,
    event_signature,
)


def store() -> InMemoryContinuityStore:
    return InMemoryContinuityStore(
        ContinuityConfig(
            cluster_id="fleet",
            node_id="beelink",
            signing_secret="continuity-secret-123456",
            event_stream_maxlen=100,
        )
    )


def test_signed_event_is_idempotent_and_tamper_evident():
    state = store()
    event = build_signed_event(
        cluster_id="fleet",
        source_node_id="beelink",
        epoch=0,
        kind="runtime.projection.updated",
        payload={"generation": 7},
        durability="durable",
        secret=state.config.signing_secret,
        idempotency_key="runtime-generation-7",
    )

    first = state.append_event(event)
    second = state.append_event(event)

    assert first["commit_state"] == "pending"
    assert second["idempotent_replay"] is True
    assert len(state.pending_durable_events()) == 1

    tampered = dict(event)
    tampered["payload"] = {"generation": 8}
    with pytest.raises(ContinuityRejected, match="signature mismatch"):
        state.append_event(tampered)


def test_epoch_and_role_leases_are_fenced():
    state = store()
    state.advance_epoch(4, "witness:beelink-4")
    lease = state.acquire_role_lease(
        role="continuity_leader",
        holder_node_id="beelink",
        epoch=4,
        ttl_ms=30_000,
        fence_proof="witness:beelink-4",
    )
    assert lease["epoch"] == 4

    with pytest.raises(ContinuityConflict, match="another node"):
        state.acquire_role_lease(
            role="continuity_leader",
            holder_node_id="xwing",
            epoch=4,
            ttl_ms=30_000,
            fence_proof="assistx-lease:xwing",
        )

    with pytest.raises(ContinuityConflict, match="stale"):
        state.acquire_role_lease(
            role="scheduler_lite",
            holder_node_id="xwing",
            epoch=3,
            ttl_ms=30_000,
            fence_proof="assistx-lease:xwing",
        )


def test_task_claim_distributes_by_capability_and_token():
    state = store()
    state.advance_epoch(2, "witness:epoch-2")
    state.record_heartbeat(
        {
            "node_id": "xwing",
            "capabilities": ["code", "lmstudio"],
            "status": "healthy",
            "max_slots": 2,
            "memory_available_mb": 6000,
        }
    )
    state.submit_task(
        {
            "task_id": "task-1",
            "title": "Repair router config",
            "epoch": 2,
            "required_capabilities": ["code"],
            "payload": {"repository": "auto-router"},
        }
    )

    assert (
        state.claim_task(
            node_id="beelink",
            capabilities=["lmstudio"],
            epoch=2,
        )
        is None
    )
    claimed = state.claim_task(
        node_id="xwing",
        capabilities=["code", "lmstudio"],
        epoch=2,
    )
    assert claimed and claimed["claimed_by"] == "xwing"

    with pytest.raises(ContinuityConflict, match="token mismatch"):
        state.complete_task(
            task_id="task-1",
            node_id="xwing",
            claim_token="wrong",
            status="completed",
        )

    completed = state.complete_task(
        task_id="task-1",
        node_id="xwing",
        claim_token=claimed["claim_token"],
        status="completed",
        result={"commit": "abc123"},
    )
    assert completed["state"] == "completed"


def test_context_manifest_retains_metadata_not_prompt_material():
    state = store()
    manifest = state.put_context_manifest(
        {
            "cache_id": "kvc-1",
            "prefix_id": "prefix-opaque",
            "model_id": "local/qwen",
            "scope_id": "project:auto-assist",
            "compatibility_fingerprint": "a" * 64,
            "node_id": "xwing",
            "endpoint_id": "lmstudio-xwing",
            "runtime": "lmstudio",
            "storage_tier": "host",
            "portable": False,
            "token_count": 4096,
            "bytes": 0,
            "expires_at_ms": 9_999_999_999_999,
        }
    )
    assert manifest["prefix_id"] == "prefix-opaque"
    matches = state.find_context_manifests(
        prefix_id="prefix-opaque",
        model_id="local/qwen",
        scope_id="project:auto-assist",
        compatibility_fingerprint="a" * 64,
    )
    assert matches[0]["cache_id"] == "kvc-1"

    with pytest.raises(ContinuityRejected, match="raw prompt"):
        state.put_context_manifest(
            {
                **manifest,
                "cache_id": "kvc-2",
                "prompt": "secret prompt",
            }
        )


def test_event_signature_changes_when_epoch_changes():
    state = store()
    event = build_signed_event(
        cluster_id="fleet",
        source_node_id="beelink",
        epoch=1,
        kind="test",
        payload={"ok": True},
        durability="recoverable",
        secret=state.config.signing_secret,
    )
    original = event["signature"]
    event["epoch"] = 2
    assert event_signature(event, state.config.signing_secret) != original
