
from __future__ import annotations
from typing import Dict, Any, List, Tuple
from .neo4j_client import Neo4jClient
import os, re, requests

def _get_artifacts(neo: Neo4jClient, run_id: str) -> List[Dict[str, Any]]:
    with neo.driver.session() as s:
        res = s.run("MATCH (r:AgentRun{id:$rid})-[:PRODUCED]->(a:Artifact) RETURN a", {"rid": run_id})
        return [dict(r[0]) for r in res]

def _file_exists(task: Dict[str, Any], args: Dict[str, Any], artifacts: List[Dict[str, Any]]) -> Tuple[bool,str]:
    path = args.get("path")
    if path:
        path = path.replace("{task_id}", task.get("id",""))
        if os.path.exists(path):
            return True, f"file exists: {path}"
    if artifacts:
        return True, "artifact present"
    return False, "no artifact or file not found"

def _contains(task: Dict[str, Any], args: Dict[str, Any], artifacts: List[Dict[str, Any]]) -> Tuple[bool,str]:
    path = args.get("path")
    if path:
        path = path.replace("{task_id}", task.get("id",""))
    text = args.get("text","")
    if not path or not text:
        return False, "path or text missing"
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read()
        ok = (text in data)
        return ok, ("text found" if ok else "text not found")
    except Exception as e:
        return False, f"read error: {e}"

def _regex(task: Dict[str, Any], args: Dict[str, Any], artifacts: List[Dict[str, Any]]) -> Tuple[bool,str]:
    path = args.get("path")
    if path:
        path = path.replace("{task_id}", task.get("id",""))
    pat = args.get("pattern")
    if not path or not pat:
        return False, "path or pattern missing"
    try:
        rx = re.compile(pat, re.MULTILINE)
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read()
        ok = rx.search(data) is not None
        return ok, ("pattern matched" if ok else "no match")
    except Exception as e:
        return False, f"regex error: {e}"

def _http_ok(task: Dict[str, Any], args: Dict[str, Any], artifacts: List[Dict[str, Any]]) -> Tuple[bool,str]:
    url = args.get("url")
    if not url:
        return False, "url missing"
    try:
        r = requests.head(url, allow_redirects=True, timeout=8)
        if r.status_code >= 400:
            r = requests.get(url, timeout=12)
        ok = 200 <= r.status_code < 300
        return ok, f"status {r.status_code}"
    except Exception as e:
        return False, f"http error: {e}"

CHECKS = {"file_exists": _file_exists, "contains": _contains, "regex": _regex, "http_ok": _http_ok}

def evaluate_acceptance(neo: Neo4jClient, task: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    acc = task.get("acceptance") or []
    if not acc:
        return {"passed": True, "details": [{"check":"none","ok":True,"info":"no acceptance specified"}]}
    artifacts = _get_artifacts(neo, run_id)
    details = []
    all_ok = True
    for chk in acc:
        t = chk.get("type")
        args = chk.get("args", {})
        fn = CHECKS.get(t)
        if not fn:
            details.append({"check": t, "ok": False, "info": "unknown check"}); all_ok = False; continue
        ok, info = fn(task, args, artifacts); details.append({"check": t, "ok": ok, "info": info}); all_ok = all_ok and ok
    return {"passed": all_ok, "details": details}
