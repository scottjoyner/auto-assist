"""Neo4j durable-authority discovery: endpoints/runtimes/nodes registered in
Neo4j are folded into the fleet registry so non-LM-Studio runtimes are visible
to the inference engine."""

from assistx import fleet


def test_registry_from_neo4j_rows_basic():
    fleet_nodes = [{"node_id": "destroyer"}, {"node_id": "x1-370"}]
    runtimes = [
        {"node_id": "destroyer", "runtime_kind": "llama_cpp",
         "runtime_instance_id": "llama-destroyer"},
        {"node_id": "x1-370", "runtime_kind": "lmstudio",
         "runtime_instance_id": "lmstudio-x1-370"},
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
        # endpoint with no model identity keeps the node but adds no model entry
        {
            "node_id": "x1-370",
            "endpoint_id": "x1-370:svc",
            "base_url": "http://127.0.0.1:9000",
            "status": "online",
        },
        # status outside the strict allowlist must be skipped
        {
            "node_id": "destroyer",
            "endpoint_id": "destroyer:provisioning:1234",
            "model_endpoint_id": "provisioning-model",
            "base_url": "http://100.81.57.77:1235",
            "status": "provisioning",
        },
        # expired endpoint must be skipped
        {
            "node_id": "destroyer",
            "endpoint_id": "destroyer:expired:1234",
            "model_endpoint_id": "expired-model",
            "base_url": "http://100.81.57.77:1236",
            "status": "online",
            "expires_at_ts": 1_000,
        },
    ]
    nodes, model_map = fleet.registry_from_neo4j_rows(
        fleet_nodes=fleet_nodes,
        runtime_instances=runtimes,
        endpoints=endpoints,
    )
    assert "destroyer" in nodes
    assert "x1-370" in nodes
    # A routable Neo endpoint emits a router-shaped id; base_url is metadata only.
    assert nodes["destroyer"]["models"]["destroyer:llama-server:1234"] == \
        "llama_cpp-destroyer.destroyer:llama-server:1234"
    assert nodes["destroyer"]["endpoint_urls"]["destroyer:llama-server:1234"] == \
        "http://100.81.57.77:1234"
    assert nodes["x1-370"]["models"]["ornith-1.5-35b"] == \
        "lmstudio-x1-370.ornith-1.5-35b"
    assert nodes["x1-370"]["endpoint_urls"]["ornith-1.5-35b"] == \
        "http://127.0.0.1:1234"
    assert fleet.parse_router_model_id(
        nodes["destroyer"]["models"]["destroyer:llama-server:1234"]
    ) == ("llama_cpp", "destroyer", "destroyer:llama-server:1234")
    # offline / non-allowlisted / expired endpoints never enter the model map
    assert "dead-model" not in model_map
    assert "provisioning-model" not in model_map
    assert "expired-model" not in model_map
    assert "destroyer:llama-server:1234" in model_map
    assert "x1-370:svc" not in model_map  # no model identity
    assert nodes["x1-370"]["models"] == {
        "ornith-1.5-35b": "lmstudio-x1-370.ornith-1.5-35b",
    }
    # Every emitted id is router-parseable; no base_url is used as a model id.
    for node in nodes.values():
        for model_id in node["models"].values():
            assert fleet.parse_router_model_id(model_id) is not None
            assert not model_id.startswith("http://")
    for entries in model_map.values():
        for _nid, full_id, _model in entries:
            assert fleet.parse_router_model_id(full_id) is not None
            assert not full_id.startswith("http://")


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
    nodes, model_map = fleet.registry_from_neo4j_rows(
        endpoints=endpoints,
        runtime_instances=[
            {"node_id": "n1", "runtime_kind": "lmstudio",
             "runtime_instance_id": "rt-n1"},
        ],
    )
    # first wins for model->full id (and URL metadata); dedup keeps one entry
    assert nodes["n1"]["models"]["m1"] == "lmstudio-n1.m1"
    assert nodes["n1"]["endpoint_urls"]["m1"] == "http://n1:1"
    assert len(model_map["m1"]) == 1
