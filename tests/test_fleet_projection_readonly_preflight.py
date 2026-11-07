"""No live graph, signer, node token, Task API or inference in these tests."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/fleet-projection-readonly-preflight.py"
module_spec = importlib.util.spec_from_file_location("projection_readonly", PATH)
assert module_spec and module_spec.loader
preflight = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(preflight)

NOW = 1_800_000_000_000


def snapshot(*, expiry=NOW - 3600000, fresh=0):
    return {
        "canonical": {"total": 1, "generation": 896, "latest_expiry": expiry},
        **{k: {"total": 10, "fresh": fresh} for k in (
            "runtime", "loaded_models", "access_paths", "capacity", "work_supply"
        )},
    }


def test_expired_graph_reports_all_hold_categories():
    output = preflight.assess(snapshot(), ["synthetic-model"], NOW)
    assert output["status"] == "HOLD"
    assert output["task_admission"] == "HOLD"
    assert output["approval_candidate_ready"] is False
    assert output["canonical_expired_hours"] == 1
    assert len(output["reason_codes"]) == 7
    assert output["physical_loaded_model_count"] == 1
    assert output["graph"]["capacity"]["fresh_approved"] == 0


def test_fresh_graph_still_requires_independent_owner_approval():
    output = preflight.assess(snapshot(expiry=NOW + 3600000, fresh=1),
                              ["synthetic-model"], NOW)
    assert output["reason_codes"] == ["INDEPENDENT_OWNER_SIGNED_REAPPROVAL_REQUIRED"]
    assert output["status"] == "HOLD"
    assert output["approval_candidate_ready"] is False
    assert output["physical_capacity_attested"] is False
    assert output["signed_resource_commit_verified"] is False


def test_missing_canonical_and_loaded_model_hold():
    state = snapshot(expiry=0, fresh=0)
    state["canonical"]["total"] = 0
    result = preflight.assess(state, [], NOW)
    assert "CANONICAL_IDENTITY_UNRESOLVED" in result["reason_codes"]
    assert "NO_PHYSICAL_LOADED_MODEL_WITNESS" in result["reason_codes"]


class GraphSession:
    def __init__(self):
        self.queries = []
    def __enter__(self):return self
    def __exit__(self, *_):return False
    def run(self, query, **kwargs):
        self.queries.append((query, kwargs))
        assert not any(word in query.upper() for word in (
            " CREATE ", " MERGE ", " DELETE ", " SET ", " REMOVE ", " LOAD CSV ",
            " CALL ", " DROP ", " DETACH ",
        ))
        if "FleetProjectionState" in query:
            return FakeResult({"total": 1, "generation": 7,
                               "latest_expiry": NOW - 1000,
                               "operator_secret_must_not_emit": "synthetic-private-token"})
        return FakeResult({"total": 3, "fresh": 0,
                           "other_sensitive_data": "synthetic-password"})


class FakeResult:
    def __init__(self, row):self.row=row
    def single(self):return self.row


class Graph:
    def __init__(self):self.session=GraphSession()
    def _session(self):return self.session


def test_graph_queries_are_read_only_and_output_redacted():
    source = Graph()
    stats = preflight.read_graph_snapshot(source, NOW)
    assert len(source.session.queries) == 6
    assert all(a == {"now_ms": NOW} for _, a in source.session.queries)
    assert stats["canonical"] == {"total": 1, "generation": 7,
                                  "latest_expiry": NOW - 1000}
    text = json.dumps(preflight.assess(stats, ["synthetic-model"], NOW))
    assert "synthetic-private-token" not in text
    assert "synthetic-password" not in text


@pytest.mark.parametrize("bad_value", [None, -1, True, "3", 2.1])
def test_malformed_graph_counters_fail_closed(bad_value):
    class BadGraph(Graph):
        def __init__(self):
            super().__init__()
            self.session.run = lambda *_a, **_kw: FakeResult({
                "total": bad_value, "generation": 1, "latest_expiry": NOW
            })
    with pytest.raises(ValueError, match="projection_counter_malformed"):
        preflight.read_graph_snapshot(BadGraph(), NOW)


class FakeResponse:
    status = 200
    def __init__(self, value):self.content=value
    def __enter__(self):return self
    def __exit__(self, *_):return False
    def read(self, size):return self.content[:size]


class FakeOpener:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def open(self, req, **kwargs):
        self.calls.append((req.full_url, req.get_method(), kwargs))
        return FakeResponse(self.value)


def test_loaded_model_catalog_single_bounded_get():
    data = {"data": [
        {"id": "model-c", "state": "unloaded"},
        {"id": "model-b", "state": "loaded"},
        {"id": "model-a", "state": "loaded"},
        {"id": "model-b", "state": "loaded"},
    ]}
    client=FakeOpener(json.dumps(data).encode())
    assert preflight.read_local_loaded_models(opener=client) == ["model-a", "model-b"]
    assert client.calls == [(preflight.MODEL_URL, "GET", {"timeout": 4})]


def test_refuses_unpinned_model_catalog_url():
    with pytest.raises(ValueError, match="origin_not_pinned"):
        preflight.read_local_loaded_models(opener=FakeOpener(b"{}"), url="https://external.invalid/")


def test_oversize_catalog_and_bad_json_fail_closed():
    with pytest.raises(ValueError, match="oversized"):
        preflight.read_local_loaded_models(opener=FakeOpener(b"x" * (512 * 1024 + 2)))
    with pytest.raises(json.JSONDecodeError):
        preflight.read_local_loaded_models(opener=FakeOpener(b"not-json"))


def test_redirect_is_unconditionally_refused():
    assert preflight._NoRedirect().redirect_request(None,None,301,'Moved',{},'https://other.invalid/') is None


def test_no_approval_or_task_write_functions_exposed():
    assert not any(hasattr(preflight, s) for s in
                   ("approve", "write_graph", "claim_task", "apply_admission", "sign_projection"))
