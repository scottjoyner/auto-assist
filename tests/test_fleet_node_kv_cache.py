from assistx import fleet_node_agent


def _task(fingerprint="f" * 64):
    return {
        "id": "task-cache",
        "allocation_cache_id": "cache-preferred",
        "required_capabilities": ["llm"],
        "payload": {
            "model": "model-a",
            "prompt": "continue",
            "kv_cache": {
                "prefix_id": "prefix-" + ("a" * 64),
                "compatibility_fingerprint": fingerprint,
                "privacy_scope": "project",
                "scope_id": "project-a",
            },
        },
    }


def test_cache_adapter_can_only_add_allowlisted_inference_fields(
    tmp_path, monkeypatch
):
    calls = []

    def fake_http(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/v1/kv-cache/resolve"):
            return 200, {
                "cache_id": "cache-a",
                "mode": "restore",
                "compatibility_fingerprint": "f" * 64,
                "tokens_saved": 120,
                "prefill_ms_saved": 900,
                "restore_ms": 40,
                "request_fields": {
                    "cache_prompt": True,
                    "slot_id": 3,
                    "model": "untrusted-model",
                    "messages": [{"role": "system", "content": "untrusted"}],
                    "temperature": 2,
                },
            }
        return 200, {
            "choices": [{"message": {"content": "continued"}}],
        }

    monkeypatch.setattr(fleet_node_agent, "_http", fake_http)
    outcome = fleet_node_agent.execute_task(
        _task(),
        "http://inference",
        str(tmp_path),
        node_id="node-a",
        cache_control_url="http://cache-control/",
    )

    assert outcome["status"] == "DONE"
    assert outcome["result"]["answer"] == "continued"
    assert outcome["result"]["kv_cache_event"]["outcome"] == "RESTORE"
    resolve_body = calls[0][2]["data"]
    assert resolve_body["preferred_cache_id"] == "cache-preferred"
    inference_body = calls[1][2]["data"]
    assert inference_body["model"] == "model-a"
    assert inference_body["messages"] == [
        {"role": "user", "content": "continue"}
    ]
    assert inference_body["cache_prompt"] is True
    assert inference_body["slot_id"] == 3
    assert "temperature" not in inference_body


def test_cache_adapter_ignores_incompatible_resolution(tmp_path, monkeypatch):
    calls = []

    def fake_http(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/v1/kv-cache/resolve"):
            return 200, {
                "cache_id": "cache-wrong",
                "mode": "local",
                "compatibility_fingerprint": "0" * 64,
                "request_fields": {"slot_id": 99},
            }
        return 200, {
            "choices": [{"message": {"content": "fresh"}}],
        }

    monkeypatch.setattr(fleet_node_agent, "_http", fake_http)
    outcome = fleet_node_agent.execute_task(
        _task(),
        "http://inference",
        str(tmp_path),
        node_id="node-a",
        cache_control_url="http://cache-control",
    )

    assert outcome["status"] == "DONE"
    assert "kv_cache_event" not in outcome["result"]
    assert "slot_id" not in calls[1][2]["data"]
