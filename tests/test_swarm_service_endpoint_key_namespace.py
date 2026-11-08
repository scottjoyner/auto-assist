"""Non-admitting regression: separate model-vs-service graph identities.

Only mocks write sessions. Never changes Neo4j; old colliding production nodes
need a separately approved migration and uniqueness-constraint restoration.
"""

from __future__ import annotations

from hashlib import sha256

import pytest

from assistx.swarm_core import (
    EventValidationError,
    MODEL_SERVICE_KEY_PREFIX,
    RESERVED_SERVICE_KEY_PREFIX,
    _model_service_key,
    upsert_model_endpoint,
    upsert_swarm_node,
)


class Result:
    def __init__(self, row=None):
        self.row = row

    def single(self):
        return self.row

    def consume(self):
        return None


class Session:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def run(self, query, params=None):
        args = params or {}
        self.calls.append((query, args))
        if "RETURN e" in query:
            return Result({"e": {"model_endpoint_id": args["endpoint_id"], "endpoint_id": args["service_graph_id"]}})
        if "RETURN n" in query:
            return Result({"n": {"node_id": args["node_id"]}})
        return Result()


class Graph:
    def __init__(self):
        self.sessions = []

    def _session(self):
        session = Session()
        self.sessions.append(session)
        return session


def test_stable_scoped_model_key_keeps_external_id_unchanged():
    raw = "node42:model:https://local.invalid:1234"
    key = _model_service_key(raw)
    assert key == MODEL_SERVICE_KEY_PREFIX + sha256(raw.encode("utf-8")).hexdigest()
    assert len(key) == len(MODEL_SERVICE_KEY_PREFIX) + 64
    assert raw not in key
    assert _model_service_key(raw) == key
    assert _model_service_key("different_model") != key


def test_model_endpoint_created_with_private_service_key_not_raw_model_id():
    graph = Graph()
    input_value = {"node_id": "node42", "model_endpoint_id": "same-as-ordinary-service", "status": "online"}
    result = upsert_model_endpoint(graph, input_value)
    calls = graph.sessions[0].calls
    assert len(calls) == 1
    query, params = calls[0]
    assert result["model_endpoint_id"] == input_value["model_endpoint_id"]
    assert params["endpoint_id"] == input_value["model_endpoint_id"]
    assert params["service_graph_id"] == _model_service_key(input_value["model_endpoint_id"])
    assert "endpoint_id" not in params["props"]
    assert "ON CREATE SET" in query
    assert "e.endpoint_id=$service_graph_id" in query
    assert "e.endpoint_id=coalesce(e.endpoint_id, $service_graph_id)" in query
    assert "e.endpoint_id=coalesce(e.endpoint_id, $endpoint_id)" not in query
    assert "MERGE (e:ModelEndpoint:ServiceEndpoint {model_endpoint_id:$endpoint_id})" in query


def test_legacy_client_endpoint_id_injection_cannot_overwrite_hashed_service_key():
    graph = Graph()
    payload = {"node_id": "n1", "model_endpoint_id": "external-model-id", "endpoint_id": "injected-plain-service-id"}
    upsert_model_endpoint(graph, payload)
    query, params = graph.sessions[0].calls[0]
    assert params["endpoint_id"] == "external-model-id"
    assert params["service_graph_id"] == _model_service_key("external-model-id")
    assert "endpoint_id" not in params["props"]
    assert params["props"]["model_endpoint_id"] == "external-model-id"


def test_legacy_model_endpoint_key_is_not_automatically_rewritten():
    graph = Graph()
    upsert_model_endpoint(graph, {"node_id": "n1", "model_endpoint_id": "legacy"})
    query, _ = graph.sessions[0].calls[0]
    # The only assignment outside ON CREATE is COALESCE: already-present
    # ServiceEndpoint.endpoint_id survives unchanged. No live migration here.
    assert "ON CREATE SET e.created_at" in query
    assert "e.endpoint_id=coalesce(e.endpoint_id, $service_graph_id)" in query
    assert "SET e.endpoint_id=$service_graph_id" not in query


def test_model_upsert_missing_model_id_falls_back_to_legacy_raw_external_id():
    graph = Graph()
    upsert_model_endpoint(graph, {"node_id": "n1", "endpoint_id": "legacy-external-id"})
    query, args = graph.sessions[0].calls[0]
    assert args["endpoint_id"] == "legacy-external-id"
    assert args["service_graph_id"] == _model_service_key("legacy-external-id")


def test_two_semantically_different_endpoints_on_same_node_do_not_share_key():
    graph = Graph()
    upsert_swarm_node(
        graph, {"node_id": "n1", "services": [{"endpoint_id": "shared-external-id", "service_type": "lmstudio"}]}
    )
    model = Graph()
    upsert_model_endpoint(model, {"node_id": "n1", "model_endpoint_id": "shared-external-id"})
    services = [args for _, args in graph.sessions[0].calls if args.get("endpoint_id")]
    assert services[0]["endpoint_id"] == "shared-external-id"
    assert model.sessions[0].calls[0][1]["service_graph_id"] != services[0]["endpoint_id"]


@pytest.mark.parametrize(
    "key",
    [
        "model_ep:v1:" + "a" * 64,
        "model_ep:future-schema:one",
        "model_ep:",
    ],
)
def test_generic_service_rejects_reserved_model_namespace_before_any_write(key):
    graph = Graph()
    with pytest.raises(EventValidationError, match="reserved model endpoint graph identity namespace"):
        upsert_swarm_node(graph, {"node_id": "n1", "services": [{"endpoint_id": key}]})
    assert graph.sessions == []


def test_later_reserved_service_in_payload_rejects_before_partial_node_write():
    graph = Graph()
    with pytest.raises(EventValidationError):
        upsert_swarm_node(
            graph,
            {
                "node_id": "n1",
                "services": [{"endpoint_id": "safe-endpoint"}, {"endpoint_id": "model_ep:v1:collision"}],
                "capabilities": [{"capability_id": "must-never-be-written"}],
            },
        )
    assert graph.sessions == []


def test_existing_generic_service_id_still_works_and_is_unmodified():
    graph = Graph()
    result = upsert_swarm_node(
        graph, {"node_id": "n1", "services": [{"endpoint_id": "my-normal-service", "service_type": "api"}]}
    )
    assert result["node_id"] == "n1"
    service = [(q, p) for q, p in graph.sessions[0].calls if "MERGE (e:ServiceEndpoint" in q]
    assert len(service) == 1
    assert service[0][1]["endpoint_id"] == "my-normal-service"
    assert service[0][1]["props"]["service_type"] == "api"


def test_model_serves_model_ids_stay_bound_to_external_id():
    graph = Graph()
    upsert_model_endpoint(
        graph, {"node_id": "n1", "model_endpoint_id": "external-id", "models": [{"model_id": "small-lfm3"}]}
    )
    calls = graph.sessions[0].calls
    assert len(calls) >= 2
    assert calls[0][1]["service_graph_id"] == _model_service_key("external-id")
    assert calls[1][1]["endpoint_id"] == "external-id"
    assert calls[1][1]["model_id"] == "small-lfm3"


def test_namespace_does_not_embed_hostnames_urls_or_secret_like_input():
    literal = "https://secret-user:password@internal.example/v1/chat"
    key = _model_service_key(literal)
    assert "password" not in key and "internal.example" not in key
    assert key.startswith(RESERVED_SERVICE_KEY_PREFIX)
