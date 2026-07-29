import hashlib

import pytest

from assistx.kv_cache import (
    build_manifest,
    cache_match,
    cache_value,
    compatibility_fingerprint,
    prefix_digest,
    runtime_capabilities,
)


def compatibility(**overrides):
    value = {
        "model_artifact_hash": "weights-sha256",
        "model_id": "qwen-35b-q4",
        "model_quantization": "Q4_K_M",
        "kv_k_quantization": "q8_0",
        "kv_v_quantization": "q8_0",
        "tokenizer_hash": "tokenizer-sha256",
        "chat_template_hash": "template-sha256",
        "adapter_hash": None,
        "runtime": "llama_cpp",
        "runtime_version": "b7000",
        "cache_format_version": "llama-slot-v1",
        "context_length": 131072,
        "rope_config_hash": "rope-sha256",
    }
    value.update(overrides)
    return value


def manifest(**overrides):
    value = {
        "cache_id": "cache-1",
        "prefix_id": "prefix-" + "a" * 64,
        "node_id": "xwing",
        "endpoint_id": "xwing.llama",
        "model_id": "qwen-35b-q4",
        "runtime": "llama_cpp",
        "compatibility": compatibility(),
        "privacy_scope": "project",
        "scope_id": "auto-assist",
        "token_count": 20_000,
        "bytes": 256 * 1024 * 1024,
        "storage_tier": "host",
        "portable": True,
        "ttl_seconds": 3600,
    }
    value.update(overrides)
    return build_manifest(value, now_ms=1_000)


def test_prefix_digest_is_keyed_scoped_and_never_contains_prompt_tokens():
    first = prefix_digest(
        [1, 2, 3],
        secret="secret",
        privacy_scope="project",
        scope_id="auto-assist",
    )
    same = prefix_digest(
        [1, 2, 3],
        secret="secret",
        privacy_scope="project",
        scope_id="auto-assist",
    )
    other_scope = prefix_digest(
        [1, 2, 3],
        secret="secret",
        privacy_scope="private",
        scope_id="agent-1",
    )

    assert first == same
    assert first != other_scope
    assert first.startswith("prefix-")
    assert len(first) == len("prefix-") + hashlib.sha256().digest_size * 2


def test_compatibility_changes_for_weight_kv_and_template_quantization():
    base = compatibility_fingerprint(compatibility())

    assert base != compatibility_fingerprint(
        compatibility(model_quantization="Q5_K_M")
    )
    assert base != compatibility_fingerprint(
        compatibility(kv_k_quantization="q4_0")
    )
    assert base != compatibility_fingerprint(
        compatibility(chat_template_hash="new-template")
    )
    assert base != compatibility_fingerprint(
        compatibility(runtime_version="b7001")
    )


def test_manifest_refuses_unadvertised_distributed_restore():
    with pytest.raises(ValueError, match="distributed cache restore"):
        manifest(runtime="llama_cpp", storage_tier="distributed")

    distributed = manifest(
        runtime="sglang",
        storage_tier="distributed",
        compatibility=compatibility(
            runtime="sglang",
            runtime_version="0.5",
            cache_format_version="hicache-v1",
        ),
    )
    assert distributed["portable"] is True
    assert distributed["capabilities"]["distributed_restore"] is True


def test_exact_local_cache_wins_and_wrong_quant_is_rejected():
    local = manifest()
    request = {
        "prefix_id": local["prefix_id"],
        "privacy_scope": "project",
        "scope_id": "auto-assist",
        "compatibility_fingerprint": local["compatibility_fingerprint"],
    }

    hit = cache_match(
        request,
        {"node_id": "xwing", "model_id": "qwen-35b-q4"},
        [local],
        now_ms=2_000,
    )
    wrong_quant = cache_match(
        {
            **request,
            "compatibility_fingerprint": compatibility_fingerprint(
                compatibility(model_quantization="Q5_K_M")
            ),
        },
        {"node_id": "xwing", "model_id": "qwen-35b-q4"},
        [local],
        now_ms=2_000,
    )

    assert hit["mode"] == "local"
    assert wrong_quant["mode"] == "miss"


def test_distributed_restore_accounts_for_transfer_cost():
    shared = manifest(
        runtime="sglang",
        storage_tier="distributed",
        compatibility=compatibility(
            runtime="sglang",
            runtime_version="0.5",
            cache_format_version="hicache-v1",
        ),
    )
    request = {
        "prefix_id": shared["prefix_id"],
        "privacy_scope": "project",
        "scope_id": "auto-assist",
        "compatibility_fingerprint": shared["compatibility_fingerprint"],
    }
    match = cache_match(
        request,
        {"node_id": "x1-370", "model_id": shared["model_id"]},
        [shared],
        now_ms=2_000,
    )
    economics = cache_value(
        match,
        tokens_per_second=100,
        transfer_mib_per_second=128,
    )

    assert match["mode"] == "restore"
    assert economics["prefill_seconds"] == 200
    assert economics["restore_seconds"] == 2
    assert economics["seconds_saved"] == 198
    assert economics["score_bonus"] == 0.18


def test_runtime_capabilities_fail_closed_for_unknown_backend():
    unknown = runtime_capabilities(
        "mystery",
        {"export_restore": True, "unrestricted_shell": True},
    )

    assert unknown["export_restore"] is False
    assert unknown["distributed_restore"] is False
    assert "unrestricted_shell" not in unknown


def test_manifest_api_requires_node_identity_and_omits_storage_locator(
    monkeypatch,
):
    from assistx import api

    class CacheNeo:
        closed = False
        stored = None

        def upsert_kv_cache_manifest(self, value, *, actor):
            self.stored = value
            return {
                **value,
                "artifact_ref": "file:///private/cache.bin",
                "compatibility_json": "{}",
                "capabilities_json": "{}",
                "registered_by": actor,
            }

        def close(self):
            self.closed = True

    neo = CacheNeo()
    monkeypatch.setenv(
        "ASSISTX_FLEET_NODE_TOKENS",
        '{"xwing":"node-token"}',
    )
    monkeypatch.setattr(api, "_neo", lambda: neo)
    body = api.KVCacheManifestIn(
        **{
            "cache_id": "cache-1",
            "prefix_id": "prefix-" + "a" * 64,
            "node_id": "xwing",
            "endpoint_id": "xwing.llama",
            "model_id": "qwen-35b-q4",
            "runtime": "llama_cpp",
            "compatibility": compatibility(),
            "privacy_scope": "project",
            "scope_id": "auto-assist",
            "token_count": 10_000,
            "bytes": 100,
            "storage_tier": "host",
            "artifact_ref": "file:///private/cache.bin",
            "portable": True,
        }
    )

    result = api.api_register_kv_cache_manifest(
        body,
        "node-token",
        "operator",
    )

    assert result["registered"] is True
    assert "artifact_ref" not in result["manifest"]
    assert neo.stored["compatibility_fingerprint"]
    assert neo.closed is True


def test_cache_event_api_records_only_authenticated_node(monkeypatch):
    from fastapi import HTTPException

    from assistx import api

    class CacheNeo:
        closed = False

        def record_kv_cache_event(self, *args, **kwargs):
            return {
                "event": {"outcome": kwargs["outcome"]},
                "manifest": {"runtime": "llama_cpp"},
            }

        def close(self):
            self.closed = True

    neo = CacheNeo()
    monkeypatch.setenv(
        "ASSISTX_FLEET_NODE_TOKENS",
        '{"xwing":"node-token"}',
    )
    monkeypatch.setattr(api, "_neo", lambda: neo)
    body = api.KVCacheEventIn(
        cache_id="cache-1",
        node_id="xwing",
        outcome="HIT",
        prefix_id="prefix-" + "a" * 64,
        tokens_saved=1000,
        prefill_ms_saved=2000,
    )

    with pytest.raises(HTTPException) as rejected:
        api.api_record_kv_cache_event(body, "wrong-token", "operator")
    assert rejected.value.status_code == 403

    result = api.api_record_kv_cache_event(
        body,
        "node-token",
        "operator",
    )
    assert result["event"]["outcome"] == "HIT"
    assert neo.closed is True
