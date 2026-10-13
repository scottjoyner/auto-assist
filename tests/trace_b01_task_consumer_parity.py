"""B01 get_task migration gate — pure source-pinned synthetic policy proof.

Compiles only three known, pure functions from the checked-in API source.
No live API import, graph socket, credentials, Docker, env or traffic.
The result is a NO-GO counterexample, not a production broker proposal.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

BROKER_BASIC_FIELDS = frozenset(("id", "status"))
BROKER_CLAIM_FIELDS = frozenset((
    "id", "status", "payload", "payload_json",
    "queue_class", "required_capabilities",
))
REQUIRED_HELPERS = frozenset((
    "_json_dict", "_is_claim_allowed_for_workflow_control", "_is_recovery_task",
))
VERDICT = "UNSAFE_GENERIC_GET_TASK_REPLACEMENT_NO_GO"


class ParityDenied(ValueError):
    pass
def compile_source_helpers(api_source: Path, *, mode: str) -> SimpleNamespace:
    """Compile only fixed source functions; do not import the FastAPI module."""
    if mode not in ("drain", "resume"):
        raise ParityDenied("UNREVIEWED_WORKFLOW_MODE")
    tree = ast.parse(api_source.read_text(), filename=str(api_source))
    nodes = {node.name: node for node in tree.body
             if isinstance(node, ast.FunctionDef)
             and node.name in REQUIRED_HELPERS}
    if set(nodes) != REQUIRED_HELPERS:
        raise ParityDenied("API_CONSUMER_SOURCE_CHANGED")
    sandbox = {
        "json": json, "Any": Any, "Dict": Dict,
        "_get_workflow_control": lambda: {"mode": mode},
    }
    sub_tree = ast.Module(body=list(nodes.values()), type_ignores=[])
    exec(compile(sub_tree, str(api_source), "exec"), sandbox)
    return SimpleNamespace(**{k: sandbox[k] for k in REQUIRED_HELPERS})


def _get_task_source(root: Path) -> ast.FunctionDef:
    path = root / "src/assistx/neo4j_client.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    cls = [n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "Neo4jClient"]
    if len(cls) != 1:
        raise ParityDenied("NEO4J_CLIENT_CLASS_CHANGED")
    fn = [n for n in cls[0].body
          if isinstance(n, ast.FunctionDef) and n.name == "get_task"]
    if len(fn) != 1:
        raise ParityDenied("NEO4J_GET_TASK_CHANGED")
    return fn[0]
def _api_functions(root: Path) -> dict:
    path = root / "src/assistx/api.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    wanted = {"api_claim_task","api_heartbeat_task",
              "api_complete_task","api_get_task"}
    found = {n.name:n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name in wanted}
    if set(found) != wanted:
        raise ParityDenied("TASK_ROUTE_SOURCE_CHANGED")
    return found


def _member_calls(fn: ast.FunctionDef, method: str) -> bool:
    return any(isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == method
               for node in ast.walk(fn))


def source_pins(root: Path) -> dict:
    get_task = _get_task_source(root)
    routes = _api_functions(root)
    source = ast.unparse(get_task)
    if "RETURN t" not in source or not _member_calls(get_task,"_session"):
        raise ParityDenied("FULL_GRAPH_GET_TASK_NOT_VERIFIED")
    if not all(_member_calls(routes[route],"get_task")
               for route in ("api_claim_task","api_heartbeat_task",
                             "api_complete_task")):
        raise ParityDenied("TASK_POLICY_CALLSITE_CHANGED")
    if not _member_calls(routes["api_get_task"],"_session"):
        raise ParityDenied("TASK_DETAIL_GRAPH_SESSION_CHANGED")
    client = (root / "tests/trace_task_read_broker_client_research.py").read_text()
    server = (root / "tests/trace_disposable_task_broker.py").read_text()
    if ("fixture-task-" not in client or "fixture-task-" not in server
            or "get_task_claim_guard" not in client):
        raise ParityDenied("BROKER_FIXTURE_SCOPE_CHANGED")
    return {
        "full_task_read_returns_graph_node":True,
        "claim_route_uses_legacy_get_task":True,
        "heartbeat_route_uses_legacy_get_task":True,
        "completion_route_uses_legacy_get_task":True,
        "task_detail_route_uses_direct_session":True,
        "broker_accepts_fixture_only_task_ids":True,
    }
def policy_counterexample(root: Path) -> dict:
    """Execute real checked-in policy helpers on synthetic Task projections."""
    pins = source_pins(root)
    api = root / "src/assistx/api.py"
    drain = compile_source_helpers(api, mode="drain")
    full = {
        "id":"fixture-task-A1bc","status":"READY",
        "required_capabilities":["recovery"],
        "queue_class":"critical",
        "payload_json":'{"runbook":"synthetic-only","queue_class":"critical"}',
        "payload":None,
    }
    basic = {key:full[key] for key in BROKER_BASIC_FIELDS}
    claim = {key:full[key] for key in BROKER_CLAIM_FIELDS}
    result = {
        **pins,
        "full_recovery_identified":drain._is_recovery_task(full),
        "basic_recovery_identified":drain._is_recovery_task(basic),
        "claim_plan_recovery_identified":drain._is_recovery_task(claim),
        "full_critical_allowed_in_drain":
            drain._is_claim_allowed_for_workflow_control(full)[0],
        "basic_critical_allowed_in_drain":
            drain._is_claim_allowed_for_workflow_control(basic)[0],
        "claim_plan_critical_allowed_in_drain":
            drain._is_claim_allowed_for_workflow_control(claim)[0],
    }
    if not (result["full_recovery_identified"]
            and not result["basic_recovery_identified"]
            and result["claim_plan_recovery_identified"]
            and result["full_critical_allowed_in_drain"]
            and not result["basic_critical_allowed_in_drain"]
            and result["claim_plan_critical_allowed_in_drain"]):
        raise ParityDenied("EXPECTED_TWO_FIELD_POLICY_COUNTEREXAMPLE_CHANGED")
    return {
        "schema":"assistx-b01-task-consumer-policy-negative-v1",
        "verdict":VERDICT,
        "source_pinned":True,
        "synthetic_only":True,
        **result,
        "graph_queries_executed":False,
        "real_task_parity_proven":False,
        "direct_graph_removed":False,
        "production_cutover_authorized":False,
    }


def main() -> int:
    print(json.dumps(policy_counterexample(
        Path(__file__).resolve().parents[1]), sort_keys=True))
    return 2  # The expected unsafe-replacement finding fails closed.


if __name__ == "__main__":
    raise SystemExit(main())