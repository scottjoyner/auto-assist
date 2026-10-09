"""Deny accidental double-metering from independent trace admission drafts.

This is a source-level integration sentinel, not a proof that any limiter
works in production. The policy must be reconciled before merging both designs.
"""
from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src/assistx/swarm_routes.py"


def test_trace_index_must_not_layer_independent_admission_policies():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    endpoint = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "api_list_traces"
    )
    defaults = " ".join(ast.unparse(item) for item in endpoint.args.defaults)
    calls = {
        node.func.id for node in ast.walk(endpoint)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    principal_meter = "_trace_read_budget" in defaults
    peer_and_fleet_meter = "_admit_trace_index" in calls
    assert not (principal_meter and peer_and_fleet_meter), (
        "Two independently authored trace index limiters have been combined: "
        "#146 per-operator all-GET quota and #147 index peer/fleet quota. "
        "Select one integrated, explicitly staged admission contract first; "
        "do not accidentally double-charge index reads or mix 429/503 outages."
    )


def test_trace_detail_and_evidence_remain_bound_to_strict_operator_auth():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for name in ("api_list_traces", "api_get_trace", "api_trace_task_evidence"):
        endpoint = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        defaults = " ".join(ast.unparse(item) for item in endpoint.args.defaults)
        assert "_trace_read_budget" in defaults or "_trace_read_auth" in defaults, name
