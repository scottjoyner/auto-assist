#!/usr/bin/env python3
"""Fail-closed *change-detection* gate for known Task Cypher call sites.

Fingerprint static Task mutation query literals AND every dynamic query
expression currently seen by the narrow Python AST scanner. No database
credentials, query contents, Task payloads, or production access. The
committed baseline is NOT a trust anchor; PR authors can edit it. Dynamic
queries synthesized elsewhere, procedures, other languages, and external
services may bypass discovery. Never infer global generation fencing.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

from hermes_task_writer_inventory import ROOT, _analyze_query, _literal

BASELINE = Path(__file__).resolve().parents[1] / "tests/fixtures/hermes_task_surface_baseline.json"
DOMAIN = b"hermes-task-surface-incomplete-audit-v1\\0"
METHODS = frozenset(("run","execute_query","run_query","query"))
MAX_FILES=2000
MAX_BYTES=4_000_000


def _sha(value: str) -> str:
    return hashlib.sha256(DOMAIN + value.encode("utf-8")).hexdigest()


class _Calls(ast.NodeVisitor):
    def __init__(self, path: str):
        self.path=path
        self.scopes=[]
        self.found=[]

    def _enter(self, name, node):
        self.scopes.append((name,node))
        self.generic_visit(node)
        self.scopes.pop()

    def visit_ClassDef(self,node):
        self._enter(node.name,node)

    def visit_FunctionDef(self,node):
        self._enter(node.name,node)

    def visit_AsyncFunctionDef(self,node):
        self._enter(node.name,node)

    def visit_Call(self,node):
        meth=getattr(node.func,"attr",None)
        if meth in METHODS:
            scope=".".join(s[0] for s in self.scopes) or "<module>"
            enclosing=self.scopes[-1][1] if self.scopes else None
            if not node.args:
                kind="dynamic_unknown"
                sql=None
                subject="NO_QUERY_ARGUMENT"
            else:
                sql=_literal(node.args[0])
                kind="dynamic_unknown" if sql is None else "static"
                subject=ast.dump(node.args[0],annotate_fields=True,
                                 include_attributes=False)
            classification = _analyze_query(sql) if sql is not None else None
            if classification is not None:
                kind="static_task_writer"
            if kind != "static":
                # For dynamic calls, fingerprint the *whole* enclosing scope
                # so local f-string constructions/assignments cannot silently
                # change even if run(query) remains unchanged.
                owner=ast.dump(enclosing,annotate_fields=True,
                               include_attributes=False) if enclosing else "<module>"
                self.found.append({
                    "path":self.path,"scope":scope,"kind":kind,
                    "call_digest":_sha(subject),
                    "scope_digest":_sha(owner),
                    "mutation_kind":classification[0] if classification else "unresolved",
                    "has_task_structural_lock_marker":
                        bool(classification[1]) if classification else False,
                })
        self.generic_visit(node)


def surface(root: Path=ROOT) -> dict:
    if not root.is_dir():
        raise ValueError("source_tree_unavailable")
    discovered=[]
    nfiles=0
    for p in sorted(root.rglob("*.py")):
        if p.is_symlink() or not p.is_file():
            raise ValueError("unsafe_source_path")
        nfiles+=1
        if nfiles>MAX_FILES:
            raise ValueError("source_file_limit_exceeded")
        raw=p.read_bytes()
        if len(raw)>MAX_BYTES:
            raise ValueError("source_file_limit_exceeded")
        try:
            node=ast.parse(raw.decode("utf-8"),filename=p.relative_to(root).as_posix())
        except (SyntaxError,UnicodeError) as exc:
            raise ValueError("unparseable_source_file") from exc
        visitor=_Calls(p.relative_to(root).as_posix())
        visitor.visit(node)
        discovered.extend(visitor.found)
    # Identical calls in a scope carry a stable ordinal, rather than line
    # numbers that change under unrelated formatting/comments.
    counts={}
    entries=[]
    for item in sorted(discovered,key=lambda x:(
            x["path"],x["scope"],x["kind"],x["call_digest"],
            x["scope_digest"],x["mutation_kind"])):
        identity=(item["path"],item["scope"],item["kind"],
                  item["call_digest"],item["scope_digest"],item["mutation_kind"])
        number=counts.get(identity,0)
        counts[identity]=number+1
        entries.append({**item,"ordinal":number})
    entries.sort(key=lambda x:(
        x["path"],x["scope"],x["kind"],x["call_digest"],
        x["scope_digest"],x["mutation_kind"],x["ordinal"]
    ))
    return {
        "schema":1,
        "mode":"bounded_ast_mutation_surface_drift",
        "decision":"HOLD",
        "source_inventory_complete":False,
        "all_writers_generation_fenced":False,
        "production_dispatch_authorized":False,
        "static_task_writer_count":sum(x["kind"]=="static_task_writer" for x in entries),
        "unresolved_dynamic_query_count":sum(x["kind"]=="dynamic_unknown" for x in entries),
        "entries":entries,
    }


def validate(actual: dict, pinned: dict) -> dict:
    if not isinstance(pinned,dict) or set(pinned)!=set(actual):
        raise ValueError("invalid_baseline_schema")
    for name in ("schema","mode","decision","source_inventory_complete",
                 "all_writers_generation_fenced","production_dispatch_authorized"):
        if pinned.get(name)!=actual.get(name):
            raise ValueError("baseline_authority_claim_or_schema_changed")
    if pinned!=actual:
        raise ValueError("task_mutation_surface_drift")
    return {
        "decision":"HOLD",
        "surface_unchanged":True,
        "all_writers_generation_fenced":False,
        "source_inventory_complete":False,
        "production_dispatch_authorized":False,
        "static_task_writer_count":actual["static_task_writer_count"],
        "unresolved_dynamic_query_count":actual["unresolved_dynamic_query_count"],
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit-candidate",action="store_true",
                        help="Print current sanitized baseline for explicit review")
    parser.add_argument("--check",action="store_true",
                        help="Validate current source against the committed baseline")
    args=parser.parse_args(argv)
    if args.emit_candidate and args.check:
        parser.error("choose only one mode")
    try:
        candidate=surface()
        if args.emit_candidate:
            print(json.dumps(candidate,indent=2,sort_keys=True))
            return 0
        expected=json.loads(BASELINE.read_text(encoding="utf-8"))
        out=validate(candidate,expected)
        print("HERMES_TASK_MUTATION_SURFACE_UNCHANGED=1")
        print("candidate_static_writers="+str(out["static_task_writer_count"]))
        print("unresolved_dynamic_queries="+str(out["unresolved_dynamic_query_count"]))
        print("SOURCE_GENERATION_GLOBAL_COVERAGE=FALSE;DECISION=HOLD")
        return 0
    except Exception as exc:
        # Failure message never includes source SQL/Task data/secret contents.
        print("HERMES_TASK_MUTATION_SURFACE_REJECTED="+
              (str(exc) if isinstance(exc,ValueError) else "unreadable_candidate"),
              file=sys.stderr)
        print("SOURCE_GENERATION_GLOBAL_COVERAGE=FALSE;DECISION=HOLD",
              file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
