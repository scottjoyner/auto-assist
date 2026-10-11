"""Offline-only AssistX Neo4j consumer census for rollout gate B02.

AST-based conservative source inventory: finds Neo4jClient method declarations,
direct session/driver use outside the central client, and callsites through
graph-like aliases. It is not a complete runtime call graph or proof of data
parity. Dynamic calls, aliases and imports require independent manual review.

Usage: PYTHONPATH=tests python3 tests/trace_graph_consumer_census.py --root src/assistx
No graph connection, credentials, Docker or production mutation.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path

GRAPH_CLIENT = "neo4j_client.py"
PRIORITY_TERMS = (
    "task", "claim", "recovery", "heartbeat", "review", "complete",
    "dispatch", "reservation", "trace", "intent", "control", "agent",
)
DIRECT_METHODS = {"_session", "session", "execute_query", "run", "driver"}


def _function(node: ast.AST) -> str:
    return getattr(node, "name", "<module>")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return _call_name(node.value) + "." + node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _call_name(node.func) + "()"
    return "<dynamic>"


def _function_nodes(root: ast.AST):
    stack = [(root, "<module>")]
    while stack:
        node, current = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            current = node.name
            yield current, node
        for child in ast.iter_child_nodes(node):
            stack.append((child, current))


def inventory(root: Path) -> dict:
    if not root.is_dir():
        raise ValueError("MISSING_SOURCE_ROOT")
    client_path = root / GRAPH_CLIENT
    if not client_path.is_file():
        raise ValueError("MISSING_NEO4J_CLIENT")
    client_ast = ast.parse(client_path.read_text(), filename=str(client_path))
    cls = [n for n in client_ast.body
           if isinstance(n, ast.ClassDef) and n.name == "Neo4jClient"]
    if len(cls) != 1:
        raise ValueError("NEO4J_CLIENT_CLASS_NOT_UNIQUE")
    methods = sorted({
        m.name for m in cls[0].body
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not m.name.startswith("__")
    })
    records = []
    session_sites = []
    for path in sorted(root.rglob("*.py")):
        if path.name == GRAPH_CLIENT or (
                "tests" in path.parts or "contracts" in path.parts
                or "__pycache__" in path.parts):
            continue
        module = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(), filename=module)
        for owner, fn in _function_nodes(tree):
            # Walk only within this function; skip nested functions so each
            # static call is attributed to its actual lexical owner.
            nodes = list(ast.walk(fn))
            for call in nodes:
                if not isinstance(call, ast.Call):
                    continue
                symbol = _call_name(call.func)
                attr = (call.func.attr if isinstance(
                    call.func, ast.Attribute) else "")
                direct = (
                    symbol.endswith("._session")
                    or symbol.endswith(".driver.session")
                    or symbol.endswith(".session")
                    or symbol == "GraphDatabase.driver"
                    or symbol.endswith("._session()")
                    or symbol.endswith(".driver.session()")
                )
                if direct:
                    session_sites.append({
                        "file": module, "function": owner,
                        "line": call.lineno, "symbol": symbol,
                        "class": "DIRECT_SESSION_REVIEW",
                    })
                if attr not in methods:
                    continue
                # Mark potential client calls, never infer a safe origin
                # from a variable name or from mere method-name agreement.
                base = _call_name(call.func.value)
                if base in {"self", "super()"}:
                    continue
                if any(token in base for token in (
                        "neo", "graph", "client", "db", "store", "persist"
                )):
                    records.append({
                        "file": module, "function": owner,
                        "line": call.lineno, "symbol": symbol,
                        "method": attr, "class": "POTENTIAL_NEO4J_METHOD",
                    })
    # A conservative sentinel ensures direct sessions are never considered
    # migrated just because one get_task fixture is green.
    method_counts = Counter(x["method"] for x in records)
    file_counts = Counter(x["file"] for x in records + session_sites)
    return {
        "schema": "assistx-neo4j-consumer-census-v1",
        "scope": "OFFLINE_SOURCE_ONLY",
        "source_root": root.as_posix(),
        "neo4j_client_declared_methods": len(methods),
        "potential_graph_method_calls": len(records),
        "direct_session_calls_outside_client": len(session_sites),
        "files_with_evidence": len(file_counts),
        "highest_callsite_files": [
            {"file": f, "observed_calls": n}
            for f, n in file_counts.most_common(25)
        ],
        "highest_method_calls": [
            {"method": name, "observed_calls": n}
            for name, n in method_counts.most_common(25)
        ],
        "direct_session_calls": sorted(
            session_sites,
            key=lambda x: (x["file"], x["line"]),
        ),
        "task_policy_calls": sorted([
            record for record in records
            if any(term in (
                record["function"] + " " + record["method"]).lower()
                   for term in PRIORITY_TERMS)
        ], key=lambda x: (x["file"], x["line"])),
        "interpretation": {
            "not_runtime_call_graph": True,
            "not_complete_dynamic_alias_analysis": True,
            "not_per_consumer_field_parity": True,
            "not_authority_to_change_production": True,
            "zero_live_graph_reads": True,
        },
        "release_gate": "B02_UNACCEPTED_PENDING_MANUAL_CONSUMER_AUDIT",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="src/assistx")
    args = parser.parse_args()
    print(json.dumps(inventory(Path(args.root)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
