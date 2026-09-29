"""Runtime-agnostic fleet discovery: router model ids of the form
``<runtime>-<node>.<model>`` are ingested for every known runtime prefix,
not just LM Studio."""

from assistx import fleet


def test_parse_router_model_id_known_runtimes():
    assert fleet.parse_router_model_id(
        "lmstudio-x1-370.refinedtoolcallv5-3b"
    ) == ("lmstudio", "x1-370", "refinedtoolcallv5-3b")
    assert fleet.parse_router_model_id(
        "fastflowlm-x1-npu.qwen3.6-moe:35b-a3b"
    ) == ("fastflowlm", "x1-npu", "qwen3.6-moe:35b-a3b")
    assert fleet.parse_router_model_id(
        "vllm-destroyer.qwen3-32b"
    ) == ("vllm", "destroyer", "qwen3-32b")


def test_parse_rejects_unattributable_ids():
    # Unknown runtime prefix: cannot attribute to a node.
    assert fleet.parse_router_model_id("gpt-4o") is None
    assert fleet.parse_router_model_id("weirdruntime-box.model-a") is None
    # No dash: no runtime prefix at all.
    assert fleet.parse_router_model_id("plain-model") is None
    # No dot: no model part after the node.
    assert fleet.parse_router_model_id("vllm-nodeonly") is None
    assert fleet.parse_router_model_id("") is None
    assert fleet.parse_router_model_id(None) is None


def test_norm_helpers_strip_any_runtime_prefix():
    assert (
        fleet._norm_node("lmstudio-destroyer.tailcb8954.ts.net") == "destroyer"
    )
    assert fleet._norm_node("vllm-destroyer.tailcb8954.ts.net") == "destroyer"
    assert fleet._norm_node("destroyer") == "destroyer"
    assert (
        fleet._norm_model("lmstudio-x1-370.refinedtoolcallv5-3b")
        == "refinedtoolcallv5-3b"
    )
    assert (
        fleet._norm_model("fastflowlm-x1-npu.qwen3.6-moe:35b-a3b")
        == "qwen3.6-moe:35b-a3b"
    )
    assert fleet._norm_model("refinedtoolcallv5-3b") == "refinedtoolcallv5-3b"


def test_node_of_supports_all_runtime_prefixes():
    assert (
        fleet._node_of("fastflowlm-x1-npu.qwen3.6-moe:35b-a3b") == "x1-npu"
    )
    assert fleet._node_of("lmstudio-x1-370.refinedtoolcallv5-3b") == "x1-370"
    assert fleet._node_of("gpt-4o") == ""


def _fake_router_response(payload):
    class _Response:
        def json(self):
            return payload

    return _Response()


def test_discover_ingests_non_lmstudio_runtimes(monkeypatch):
    payload = {
        "data": [
            {"id": "lmstudio-x1-370.refinedtoolcallv5-3b"},
            {"id": "fastflowlm-x1-npu.qwen3.6-moe:35b-a3b"},
            {"id": "vllm-destroyer.qwen3-32b"},
            # Not attributable to any node: must stay undiscovered.
            {"id": "gpt-4o"},
        ]
    }
    monkeypatch.setattr(fleet, "_load_value_data", lambda: None)
    monkeypatch.setattr(
        fleet.requests, "get", lambda *a, **k: _fake_router_response(payload)
    )
    monkeypatch.setattr(fleet, "_nodes", {})
    monkeypatch.setattr(fleet, "_model_map", {})
    monkeypatch.setattr(fleet, "_last_refresh", 0.0)

    fleet.discover(force=True)

    assert "x1-npu" in fleet._nodes
    assert fleet._nodes["x1-npu"]["models"] == {
        "qwen3.6-moe:35b-a3b": "fastflowlm-x1-npu.qwen3.6-moe:35b-a3b"
    }
    assert fleet._model_map["qwen3.6-moe:35b-a3b"] == [
        (
            "x1-npu",
            "fastflowlm-x1-npu.qwen3.6-moe:35b-a3b",
            "qwen3.6-moe:35b-a3b",
        )
    ]
    assert fleet._model_map["qwen3-32b"] == [
        ("destroyer", "vllm-destroyer.qwen3-32b", "qwen3-32b")
    ]
    # The unknown-prefix id contributed nothing to the registry.
    assert "gpt-4o" not in fleet._model_map
    assert all(
        "gpt-4o" != full for entries in fleet._model_map.values()
        for _, full, _ in entries
    )


def test_discover_skipped_prefix_logged_once(monkeypatch, caplog):
    payload = {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}
    monkeypatch.setattr(fleet, "_load_value_data", lambda: None)
    monkeypatch.setattr(
        fleet.requests, "get", lambda *a, **k: _fake_router_response(payload)
    )
    monkeypatch.setattr(fleet, "_nodes", {})
    monkeypatch.setattr(fleet, "_model_map", {})
    monkeypatch.setattr(fleet, "_last_refresh", 0.0)
    monkeypatch.setattr(fleet, "_UNATTRIBUTED_PREFIXES", set())

    with caplog.at_level("INFO"):
        fleet.discover(force=True)

    skipped = [
        r for r in caplog.records if "runtime prefix" in r.getMessage()
    ]
    assert len(skipped) == 1
    assert "gpt-4o" in skipped[0].getMessage()
