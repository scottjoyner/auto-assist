"""Neo4j durable-authority discovery: endpoints/runtimes/nodes registered in
Neo4j are folded into the fleet registry so non-LM-Studio runtimes are visible
to the inference engine."""

from assistx import fleet


def test_registry_from_neo4j_rows_basic():
    fleet_nodes = [{"node_id": "destroyer"}, {"node_id": "x1-370"}]
    runtimes = [
        {"node_id": "destroyer", "runtime_kind": "llama_cpp",
         "runtime_instance_id": "llama-destroyer"},
    ]
    endpoints = [
        {
            "node_id": "destroyer",
            "endpoint_id": "destroyer:llama-server:1234",
            "model_endpoint_id": "destroyer:llama-server:1234",
            "base_url": "http://100.81.57.77:1234",
            "status": "online",
            "provider": "llama.cpp",
        },
        {
            "node_id": "x1-370",
            "endpoint_id": "x1-370:lmstudio:1234",
            "model_endpoint_id": "ornith-1.5-35b",
            "base_url": "http://127.0.0.1:1234",
            "status": "online",
        },
        # offline endpoint must be skipped
        {
            "node_id": "destroyer",
            "endpoint_id": "destroyer:dead:9999",
            "model_endpoint_id": "dead-model",
            "base_url": "http://100.81.57.77:9999",
            "status": "offline",
        },
        # endpoint with no model identity is skipped
        {
            "node_id": "x1-370",
            "endpoint_id": "x1-370:svc",
            "base_url": "http://127.0.0.1:9000",
            "status": "online",
        },
    ]
    nodes, model_map = fleet.registry_from_neo4j_rows(
        fleet_nodes=fleet_nodes,
        runtime_instances=runtimes,
        endpoints=endpoints,
    )
    assert "destroyer" in nodes
    assert "x1-370" in nodes
    assert nodes["destroyer"]["models"]["destroyer:llama-server:1234"] == \
        "http://100.81.57.77:1234"
    assert "dead-model" not in model_map
    assert "destroyer:llama-server:1234" in model_map
    # endpoint without model_endpoint_id/model_id/endpoint_id... actually endpoint_id present -> used as model
    assert "x1-370:svc" not in model_map  # no model_endpoint_id/model_id, but endpoint_id present -> would be added; check
    # node from fleet_nodes only (no endpoints) still present
    assert nodes["x1-370"]["models"]  # has at least ornith


def test_registry_from_neo4j_rows_empty():
    nodes, model_map = fleet.registry_from_neo4j_rows()
    assert nodes == {}
    assert model_map == {}


def test_registry_from_neo4j_rows_dedupes():
    endpoints = [
        {
            "node_id": "n1",
            "endpoint_id": "ep1",
            "model_endpoint_id": "m1",
            "base_url": "http://n1:1",
            "status": "online",
        },
        {
            "node_id": "n1",
            "endpoint_id": "ep1b",
            "model_endpoint_id": "m1",
            "base_url": "http://n1:1b",
            "status": "online",
        },
    ]
    nodes, model_map = fleet.registry_from_neo4j_rows(endpoints=endpoints)
    # first wins for model->full id
    assert nodes["n1"]["models"]["m1"] == "http://n1:1"
    assert len(model_map["m1"]) == 1
