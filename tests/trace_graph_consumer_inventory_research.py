"""Static graph-consumer migration inventory; read-only, no imports of AssistX.

Research release gate B02. AST gives auditable call sites rather than treating
textual mentions as production callers. This scan intentionally over-approximates
method dispatch: names such as 'neo.get_task' may be injected elsewhere.
Unknown/dynamic targets are recorded, never silently marked safe to migrate.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from pathlib import Path
import json

GRAPH_METHODS = {
    "get_task", "claim_task", "heartbeat_task", "complete_task",
    "update_task_status", "create_tasks_batch", "get_ready_tasks",
    "get_review_tasks", "get_tasks_by_status", "create_dispatch",
    "get_dispatch", "list_dispatches", "record_kv_cache_event",
    "create_task_with_context", "upsert_ticket", "fleet_status",
}
WRITE_OPERATIONS = {
    "claim_task", "heartbeat_task", "complete_task", "update_task_status",
    "create_tasks_batch", "create_dispatch", "create_task_with_context",
    "upsert_ticket", "record_kv_cache_event",
}
GRAPH_CLIENT_NAMES = {"Neo4jClient", "GraphDatabase"}
QUERY_METHODS = {"run", "execute_query", "begin_transaction", "session"}
POLICY_SENSITIVE = {"api.py", "worker.py", "fleet_executor.py", "review.py"}

def _func_at(parents: list[str]) -> str:
    return ".".join(parents) if parents else "<module>"

def scan(root: Path) -> dict:
    sources = root / "src" / "assistx"
    if not (sources / "neo4j_client.py").is_file():
        raise FileNotFoundError("ASSISTX_SOURCE_ROOT_NOT_FOUND")
    records = []
    parse_errors = []
    def register(path: Path, tree: ast.AST):
        rel = str(path.relative_to(root))
        owners = []
        class Walker(ast.NodeVisitor):
            def visit_FunctionDef(self, n):
                owners.append(n.name)
                self.generic_visit(n)
                owners.pop()
            visit_AsyncFunctionDef = visit_FunctionDef
            def visit_ClassDef(self, n):
                owners.append(n.name)
                self.generic_visit(n)
                owners.pop()
            def visit_Call(self, node):
                f = node.func
                attr = f.attr if isinstance(f, ast.Attribute) else (
                    f.id if isinstance(f, ast.Name) else None)
                is_graph_constructor = attr in GRAPH_CLIENT_NAMES
                is_graph_method = isinstance(f, ast.Attribute) and attr in GRAPH_METHODS
                is_low_level = isinstance(f, ast.Attribute) and (
                    attr in QUERY_METHODS and (
                        isinstance(f.value, ast.Name) and f.value.id.lower() in {
                            "session", "tx", "neo4j", "driver", "db", "graph"
                        }
                        or (isinstance(f.value, ast.Attribute) and f.value.attr in {
                            "_driver", "_session", "driver"
                        })
                    )
                )
                if is_graph_constructor or is_graph_method or is_low_level:
                    kind = ("constructor" if is_graph_constructor else
                            "low_level" if is_low_level else
                            "write" if attr in WRITE_OPERATIONS else "read")
                    records.append({
                        "file": rel, "line": node.lineno,
                        "owner": _func_at(owners), "operation": attr,
                        "kind": kind, "review_required": True,
                        "confidence": (
                            "constructor" if is_graph_constructor else
                            "candidate_call_requires_type_review"
                        ),
                    })
                self.generic_visit(node)
        Walker().visit(tree)
    for path in sorted(sources.rglob("*.py")):
        if any(part in ("__pycache__", "tests") for part in path.relative_to(sources).parts):
            continue
        try:
            register(path, ast.parse(path.read_text(encoding="utf-8"), str(path)))
        except (SyntaxError, UnicodeError) as exc:
            parse_errors.append({"file": str(path.relative_to(root)),
                                 "type": type(exc).__name__})
    by_kind = Counter(x["kind"] for x in records)
    critical = [x for x in records if Path(x["file"]).name in POLICY_SENSITIVE
                and x["kind"] in ("read", "write", "low_level")]
    return {
        "schema": "assistx-graph-consumer-static-v1",
        "source_scope": "src/assistx excluding test/contract fixtures",
        "static_inventory_only": True,
        "live_graph_credential_use_verified": False,
        "every_call_fully_resolved": False,
        "safe_to_remove_direct_neo4j": False,
        "parse_errors": parse_errors,
        "statistics": {
            "source_files_scanned": len(list(sources.rglob("*.py"))),
            "candidate_graph_call_sites": len(records),
            "distinct_source_files_with_candidates":
                len({x["file"] for x in records}),
            "kinds": dict(sorted(by_kind.items())),
            "sensitive_paths_with_candidates":
                sorted({x["file"] for x in critical}),
        },
        "candidate_call_sites": sorted(records, key=lambda x: (
            x["file"], x["line"], x["operation"])),
    }

def render_markdown(result: dict) -> str:
    st = result["statistics"]
    lines = [
        "# AssistX static graph-consumer inventory — B02",
        "",
        "Automated **candidate** source call sites; all entries require owner/type "
        "review. This is **not** permission to sever a live network or merge "
        "a common Neo4j client replacement.",
        "",
        f"Files containing candidates: **{st['distinct_source_files_with_candidates']}**; "
        f"candidate sites: **{st['candidate_graph_call_sites']}**.",
        "",
        "| Module | Candidate calls | Writes | Low-level | Review posture |",
        "|---|---:|---:|---:|---|",
    ]
    groups = defaultdict(list)
    for x in result["candidate_call_sites"]:
        groups[x["file"]].append(x)
    for file, rows in sorted(groups.items(), key=lambda item: (
            -sum(r["kind"] == "write" for r in item[1]), -len(item[1]), item[0])):
        lines.append(
            f"| \`{file}\` | {len(rows)} | "
            f"{sum(r['kind']=='write' for r in rows)} | "
            f"{sum(r['kind']=='low_level' for r in rows)} | "
            "**P0 do not migrate blindly**" if Path(file).name in POLICY_SENSITIVE
            else f"| \`{file}\` | {len(rows)} | "
                 f"{sum(r['kind']=='write' for r in rows)} | "
                 f"{sum(r['kind']=='low_level' for r in rows)} | Review |"
        )
    lines.extend([
        "", "## Mandatory acceptance for each producer/consumer",
        "",
        "1. Ground exact current method, field read/write behavior, auth and "
        "side-effect semantics with owner signoff.",
        "2. Define a per-consumer restricted query/command and compatibility "
        "fixture (including failure paths, claim/recovery gates and schema drift).",
        "3. Verify exact behavior and failure receipt against existing API in "
        "staging, including raw Bolt denied only **after** migration.",
        "4. Treat dynamic driver access, unknown aliases and all parse errors as "
        "unresolved. Never claim this AST scan discovers every indirect use.",
        "",
        "## Priority operations (machine-readable references)",
        "",
    ])
    for row in result["candidate_call_sites"]:
        if row["kind"] == "write" and Path(row["file"]).name in POLICY_SENSITIVE:
            lines.append(f"- \`{row['file']}:{row['line']}\` "
                         f"\`{row['owner']}\` → \`{row['operation']}\` (write candidate)")
    return "\n".join(lines) + "\n"

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--markdown")
    parser.add_argument("--json")
    a = parser.parse_args()
    result = scan(Path(a.root).resolve())
    md = render_markdown(result)
    if a.markdown:
        Path(a.markdown).write_text(md)
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=2) + "\n")
    if not a.markdown and not a.json:
        print(json.dumps(result["statistics"], indent=2))

if __name__ == "__main__":
    main()
