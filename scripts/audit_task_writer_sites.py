#!/usr/bin/env python3
"""Conservative Task Cypher site census. No DB connections or application imports.

Lexical findings are candidates, not proof of exhaustive source-authoritative
coverage. Dynamic queries, non-Python writers and privileged Bolt access HOLD.
Does not print source query text, Task payloads or secrets.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

WRITE = re.compile(r"\b(CREATE|MERGE|SET|REMOVE|DELETE|FOREACH|DROP)\b", re.I)
TASK = re.compile(r":\s*Task\b", re.I)
QUERY_METHODS = {"run", "execute_query"}
TX_METHODS = {"execute_write", "write_transaction", "execute_read", "read_transaction"}
EXCLUDED = {"subprocess", "asyncio", "shutil", "os", "pytest", "multiprocessing"}


def literal(n, env, depth=0):
    if n is None or depth > 6:
        return "", False, "unresolved"
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value, True, "literal"
    if isinstance(n, ast.Name):
        if n.id not in env:
            return "", False, "unresolved_name"
        return literal(env[n.id], env, depth+1)
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        x, a, _ = literal(n.left, env, depth+1)
        y, b, _ = literal(n.right, env, depth+1)
        return x+y, a and b, "concatenation"
    if isinstance(n, ast.JoinedStr):
        return "".join(p.value if isinstance(p, ast.Constant) and isinstance(p.value, str)
                       else " UNKNOWN_INTERPOLATION " for p in n.values), False, "f_string"
    return "", False, "dynamic_expression"


def scope(call):
    ancestors = []
    n = call
    while n is not None:
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
            ancestors.append(n)
        n = getattr(n, "_census_parent", None)
    env = {}
    func = "<module>"
    for frame in reversed(ancestors):
        if isinstance(frame, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = frame.name
        for stmt in frame.body:
            if stmt.lineno >= call.lineno:
                break
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        env[target.id] = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if stmt.value is not None:
                    env[stmt.target.id] = stmt.value
    return env, func


def inspect(path, root):
    rel = path.relative_to(root).as_posix()
    raw = path.read_bytes()
    file_hash = hashlib.sha256(raw).hexdigest()[:12]
    try:
        tree = ast.parse(raw, filename=rel)
    except (SyntaxError, UnicodeError):
        return [dict(path=rel,line=0,function="",call="",kind="parse_error",
                     confidence="unresolved",file_sha12=file_hash)]
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._census_parent = parent
    sites = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        method = n.func.attr
        if method not in QUERY_METHODS | TX_METHODS:
            continue
        owner = n.func.value.id if isinstance(n.func.value, ast.Name) else ""
        if owner in EXCLUDED:
            continue
        env, func = scope(n)
        if method in TX_METHODS:
            kind, known, query, reason = "transaction_callback", False, "", "callback_unresolved"
        else:
            arg = n.args[0] if n.args else next(
                (k.value for k in n.keywords if k.arg in ("query","cypher")), None)
            query, known, reason = literal(arg, env)
            task = bool(TASK.search(query))
            write = bool(WRITE.search(query))
            if not known:
                kind = "dynamic_query"
            elif task and write:
                kind = "task_write_candidate"
            elif task:
                kind = "task_read"
            elif write:
                kind = "other_graph_write"
            else:
                kind = "other_graph_read"
        sites.append(dict(path=rel,line=n.lineno,function=func,call=method,
                          kind=kind,confidence="candidate" if known else "unresolved",
                          query_sha12=hashlib.sha256(query.encode()).hexdigest()[:12]
                          if query else None,file_sha12=file_hash,reason=reason))
    return sites


def build(root):
    root = root.resolve()
    paths = subprocess.check_output(
        ["git","-C",str(root),"ls-files","-z","--","src","scripts"]
    ).split(b"\0")
    sites = []
    scanned = 0
    for encoded in paths:
        if not encoded:
            continue
        rel = Path(encoded.decode())
        if (rel.suffix != ".py" or
            any(part in ("tests","test","fixtures","__pycache__") for part in rel.parts)
            or rel.name.startswith("test_") or rel.name == "audit_task_writer_sites.py"):
            continue
        scanned += 1
        sites.extend(inspect(root/rel,root))
    sites.sort(key=lambda x:(x["path"],x["line"],x["kind"]))
    kinds = {k:sum(s["kind"]==k for s in sites) for k in sorted({s["kind"] for s in sites})}
    unresolved = sum(s["confidence"]=="unresolved" for s in sites)
    head = subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"],text=True).strip()
    return dict(schema="assistx-task-writer-census.v1",revision=head,tracked_python_files=scanned,
                source_authority_complete=False,production_dispatch_authorized=False,
                decision="HOLD",kind_counts=kinds,unresolved_sites=unresolved,
                site_count=len(sites),sites=sites,
                limitations=["lexical candidates are not proven Task mutation writers",
                             "unknown dynamic queries/callbacks remain unresolved",
                             "non-Python, external, runtime and direct Bolt writers not inventoried",
                             "source-only scan does not prove global transactional fencing"])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument("--summary-only",action="store_true")
    p.add_argument("--fail-on-unresolved",action="store_true")
    args=p.parse_args()
    r=build(args.repo)
    if args.summary_only:
        r.pop("sites")
    print(json.dumps(r,sort_keys=True,indent=2))
    return 2 if args.fail_on_unresolved and r["unresolved_sites"] else 0


if __name__=="__main__":
    raise SystemExit(main())
