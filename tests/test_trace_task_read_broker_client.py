"""Offline proof: read-only Task broker client never uses direct Neo4j."""
import io
import json
from unittest.mock import patch
from urllib.error import URLError

import pytest

from trace_task_read_broker_client_research import (
    BrokerUnavailable, TaskReadBrokerClient,
)


class Reply:
    def __init__(self, data):
        self._body = io.BytesIO(json.dumps(data).encode())
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, size=-1):
        return self._body.read(size)


def client():
    return TaskReadBrokerClient(
        "http://assistx-broker-gateway-deadbeef:8808",
        "disposable-unprivileged-broker-bearer-token",
    )


def test_get_task_shaped_broker_read_reuses_approved_static_plan():
    with patch("trace_task_read_broker_client_research.urllib.request.urlopen",
               return_value=Reply({
                   "task": {"id": "fixture-task-valid", "status": "READY"}
               })) as mocked:
        assert client().get_task("fixture-task-valid") == {
            "id": "fixture-task-valid", "status": "READY"}
    outgoing = mocked.call_args.args[0]
    data = json.loads(outgoing.data)
    assert data == {
        "plan_id": "get_task_read",
        "parameters": {"id": "fixture-task-valid"},
    }
    assert outgoing.get_method() == "POST"
    assert outgoing.get_header("Authorization").startswith("Bearer ")


def test_missing_task_does_not_trigger_direct_graph_fallback():
    with patch("trace_task_read_broker_client_research.urllib.request.urlopen",
               return_value=Reply({"task": None})):
        assert client().get_task("fixture-task-absent") is None


@pytest.mark.parametrize("reply", [
    {"unexpected": {}},
    {"task": {"id": "fixture-task-valid", "status": "READY",
              "sensitive": "must-not-escape"}},
    {"task": {"id": "fixture-task-another", "status": "READY"}},
    {"task": {"id": "fixture-task-valid", "status": 1}},
    {"task": []},
])
def test_broker_response_wrong_shape_never_becomes_a_trusted_task(reply):
    with patch("trace_task_read_broker_client_research.urllib.request.urlopen",
               return_value=Reply(reply)):
        with pytest.raises(BrokerUnavailable, match="BROKER_RESPONSE_UNTRUSTED"):
            client().get_task("fixture-task-valid")


def test_broker_outage_never_causes_raw_graph_query():
    with patch("trace_task_read_broker_client_research.urllib.request.urlopen",
               side_effect=URLError("outage")) as mocked:
        with pytest.raises(BrokerUnavailable, match="BROKER_REQUEST_DENIED"):
            client().get_task("fixture-task-valid")
    assert mocked.call_count == 1
    assert not hasattr(client(), "driver")
    assert not hasattr(client(), "neo4j")


@pytest.mark.parametrize("origin", [
    "http://neo4j:7474",
    "http://127.0.0.1:7475",
    "http://example.com",
    "https://arbitrary-target.example",
    "http://assistx-broker-gateway-deadbeef.evil.com:8808",
    "http://assistx-broker-gateway-deadbeef:8808@evil.com",
    "http://assistx-broker-gateway-deadbeef:8808/path",
    "http://assistx-broker-gateway-deadbeef:8808?redirect=evil",
    "http://assistx-broker-gateway-deadbeef:8808#evil",
    "http://assistx-broker-gateway-deadbeef:8080",
    "http://assistx-broker-gateway-nothex:8808",
])
def test_untrusted_direct_bolt_or_http_target_rejected(origin):
    with pytest.raises(ValueError, match="UNTRUSTED_RESEARCH_BROKER_ORIGIN"):
        TaskReadBrokerClient(origin, "research-fixture-token")


def test_invalid_task_id_denied_before_any_network():
    with patch("trace_task_read_broker_client_research.urllib.request.urlopen") as mocked:
        with pytest.raises(ValueError, match="INVALID_TASK_LOOKUP_ID"):
            client().get_task("MATCH (n) DELETE n")
    mocked.assert_not_called()
