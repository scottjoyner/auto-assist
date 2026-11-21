#!/usr/bin/env python3
"""Bounded source-only Hermes Task mutation discovery, permanently HOLD-only.

This identifies obvious literal Cypher writers. Dynamic queries, aliases,
procedures, stored procedures, external services and non-Cypher mutations are
not exhaustively discoverable by an AST scan. Never use it as authority.
Only source paths and line numbers are emitted, never query text or secrets.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "src/assistx"
TASK = re.compile(r":Task\b")
NODE_CREATE = re.compile(r"\b(?:CREATE|MERGE)\s*\(\s*t\s*:\s*Task\b",re.I)
TASK_WRITE = re.compile(r"\b(?:SET|REMOVE)\s+t\s*(?:\.|\+\s*=|\[)",re.I)
TASK_DELETE = re.compile(r"\b(?:DETACH\s+)?DELETE\s+t\b",re.I)
EDGE = re.compile(r"\b(?:CREATE|MERGE|DELETE)\b",re.I)
FENCE = re.compile(r"SET\s+t\.hermes_fence_check_epoch",re.I)


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal(node.left)
        right = _literal(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        # Do not guess at f-string interpolation.
        return None
    return None


def _analyze_query(sql: str) -> tuple[str, bool] | None:
    if not TASK.search(sql):
        return None
    if TASK_DELETE.search(sql):
        kind="task_delete"
    elif NODE_CREATE.search(sql):
        kind="task_node_create_or_merge"
    elif TASK_WRITE.search(sql):
        kind="task_property_mutation"
    elif EDGE.search(sql):
        kind="task_related_graph_mutation"
    else:
        return None
    return kind, bool(FENCE.search(sql) and re.search(r"\bWITH\s+t\b",sql,re.I))


def inspect_source(source: str, label: str) -> dict[str, Any]:
    tree=ast.parse(source,filename=label)
    writers=[]
    unresolved=0
    sql_calls=0
    for n in ast.walk(tree):
        if not isinstance(n,ast.Call):
            continue
        meth=getattr(n.func,"attr",None)
        if meth not in ("run","execute_query","run_query","query"):
            continue
        if not n.args:
            unresolved+=1
            continue
        sql_calls+=1
        q=_literal(n.args[0])
        if q is None:
            unresolved+=1
            continue
        result=_analyze_query(q)
        if result is None:
            continue
        kind, structural_lock=result
        writers.append({
            "path":label,"line":n.lineno,
            "kind":kind,
            "structural_lock_marker":structural_lock,
            "generation_protocol_certified":False,
        })
    return {"writers":sorted(writers,key=lambda v:v["line"]),
            "unresolved_dynamic_sql_calls":unresolved,
            "total_literal_or_dynamic_query_calls":sql_calls}


def inventory(root: Path=ROOT) -> dict[str, Any]:
    if not root.is_dir():
        return {"decision":"HOLD","source_inventory_complete":False,
                "reason":"source_tree_not_accessible"}
    all_writers=[]
    dynamic=0
    file_count=0
    digest=hashlib.sha256(b"hermes-incomplete-static-task-writer-audit-v1\0")
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink() or not path.is_file():
            return {"decision":"HOLD","source_inventory_complete":False,
                    "reason":"unsafe_source_path"}
        file_count+=1
        if file_count>2000:
            return {"decision":"HOLD","source_inventory_complete":False,
                    "reason":"source_tree_exceeds_limit"}
        rel=path.relative_to(root).as_posix()
        data=path.read_bytes()
        if len(data)>4_000_000:
            return {"decision":"HOLD","source_inventory_complete":False,
                    "reason":"oversized_source_file"}
        digest.update(rel.encode("utf-8")+b"\0"+hashlib.sha256(data).digest())
        try:
            report=inspect_source(data.decode("utf-8"),rel)
        except (UnicodeError,SyntaxError):
            return {"decision":"HOLD","source_inventory_complete":False,
                    "reason":"unparseable_source"}
        all_writers.extend(report["writers"])
        dynamic+=report["unresolved_dynamic_sql_calls"]
    return {
        "schema":1,"mode":"static_task_mutation_candidate",
        "files_scanned":file_count,"source_sha256":digest.hexdigest(),
        "visible_static_task_writer_count":len(all_writers),
        "writers_without_structural_lock_marker":sum(
            not item["structural_lock_marker"] for item in all_writers),
        "unknown_dynamic_query_calls":dynamic,
        "writers":all_writers,
        "all_writers_generation_fenced":False,
        "source_inventory_complete":False,
        "external_effects_verified":False,
        "production_dispatch_authorized":False,
        "decision":"HOLD",
    }


if __name__=="__main__":
    print(json.dumps(inventory(),sort_keys=True))
