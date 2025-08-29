#!/usr/bin/env python3
"""
Approve and execute Tasks from Neo4j (no Docker/Ollama required).

Flow:
- list:    show tasks by status
- approve: set Task.status = READY
- execute: run READY tasks -> create artifacts -> run acceptance -> DONE/FAILED
- accept:  re-run acceptance for a task without executing

Data model expectations:
- (:Summary)-[:GENERATED_TASK]->(:Task {id, title, description, priority, status, acceptance})
- (:Task)-[:HAS_RUN]->(:Run {id, started_at, ended_at, status, success, manifest_json})

Acceptance types:
- file_exists  { "path": "artifacts/{TASK_ID}/output.txt" }
- contains     { "path": "...", "text": "..." }
- regex        { "path": "...", "pattern": "..." }
- http_ok      { "url": "https://..." }

Run manifest_json contains steps, acceptance_results, timings (as JSON string).
"""

import os, sys, json, re, time, argparse, uuid, pathlib, datetime
from typing import Any, Dict, List, Optional
import requests
from neo4j import GraphDatabase

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j")
ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", "./artifacts")

# ---------- Neo helpers ----------
def neo():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def q_list(session, status: str, limit: int = 25) -> List[Dict[str, Any]]:
    res = session.run(
        """
        MATCH (task:Task {status:$status})
        OPTIONAL MATCH (s:Summary)-[:GENERATED_TASK]->(task)
        OPTIONAL MATCH (t:Transcription)-[:HAS_SUMMARY]->(s)
        RETURN task.id AS id, task.title AS title, task.priority AS priority,
               t.key AS tkey, s.id AS sid
        ORDER BY coalesce(task.updated_at, datetime({epochMillis:0})) DESC, id
        LIMIT $limit
        """,
        status=status, limit=limit,
    )
    return res.data()

def q_get_task(session, task_id: str) -> Optional[Dict[str, Any]]:
    rec = session.run(
        """
        MATCH (task:Task {id:$id})
        OPTIONAL MATCH (s:Summary)-[:GENERATED_TASK]->(task)
        OPTIONAL MATCH (t:Transcription)-[:HAS_SUMMARY]->(s)
        RETURN task, s, t
        """,
        id=task_id
    ).single()
    if not rec:
        return None
    task = dict(rec["task"])
    s = dict(rec["s"]) if rec["s"] else None
    t = dict(rec["t"]) if rec["t"] else None
    return {"task": task, "summary": s, "transcription": t}

def q_approve(session, ids: List[str]) -> int:
    res = session.run(
        """
        MATCH (task:Task)
        WHERE task.id IN $ids AND task.status = 'REVIEW'
        SET task.status = 'READY', task.updated_at = datetime()
        RETURN count(task) AS n
        """, ids=ids
    ).single()
    return res["n"]

def q_approve_all_review(session, limit: int) -> int:
    res = session.run(
        """
        MATCH (task:Task {status:'REVIEW'})
        WITH task LIMIT $limit
        SET task.status = 'READY', task.updated_at = datetime()
        RETURN count(task) AS n
        """, limit=limit
    ).single()
    return res["n"]

def q_pick_ready(session, limit: int) -> List[Dict[str, Any]]:
    res = session.run(
        """
        MATCH (task:Task {status:'READY'})
        WITH task ORDER BY coalesce(task.updated_at, datetime()) ASC
        LIMIT $limit
        SET task.status = 'RUNNING', task.updated_at = datetime()
        RETURN task.id AS id
        """, limit=limit
    )
    return [r["id"] for r in res]

def q_attach_run(session, task_id: str, run: Dict[str, Any]):
    # Store complex structures as JSON strings
    res = session.run(
        """
        MATCH (task:Task {id:$tid})
        CREATE (r:Run {
          id: $rid,
          started_at: datetime($started_at),
          status: $status,
          manifest_json: $manifest_json
        })
        MERGE (task)-[:HAS_RUN]->(r)
        RETURN r.id AS rid
        """,
        tid=task_id,
        rid=run["id"],
        started_at=run["started_at"],
        status=run["status"],
        manifest_json=json.dumps(run.get("manifest", {}), ensure_ascii=False),
    ).single()
    return res["rid"]

def q_finish_run(session, task_id: str, run_id: str, success: bool, manifest: Dict[str, Any]):
    session.run(
        """
        MATCH (task:Task {id:$tid})-[:HAS_RUN]->(r:Run {id:$rid})
        SET r.status = $rstatus,
            r.ended_at = datetime($ended_at),
            r.success = $success,
            r.manifest_json = $manifest_json,
            task.status = $tstatus,
            task.updated_at = datetime()
        """,
        tid=task_id,
        rid=run_id,
        rstatus = "DONE" if success else "FAILED",
        tstatus = "DONE" if success else "FAILED",
        ended_at = datetime.datetime.utcnow().isoformat() + "Z",
        success = success,
        manifest_json = json.dumps(manifest, ensure_ascii=False),
    )

# ---------- Acceptance ----------
def replace_placeholders(s: str, task_id: str) -> str:
    return (s or "").replace("{TASK_ID}", task_id)

def acc_file_exists(args: Dict[str, Any], task_id: str) -> (bool, str):
    path = replace_placeholders(args.get("path",""), task_id)
    path = path.replace("artifacts/", f"{ARTIFACTS_DIR.rstrip('/')}/")
    ok = pathlib.Path(path).exists()
    return ok, f"path={path}"

def acc_contains(args: Dict[str, Any], task_id: str) -> (bool, str):
    path = replace_placeholders(args.get("path",""), task_id)
    path = path.replace("artifacts/", f"{ARTIFACTS_DIR.rstrip('/')}/")
    text = args.get("text","")
    try:
        data = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
        ok = text in data
        return ok, f"path={path} len={len(data)}"
    except Exception as e:
        return False, f"path={path} err={e}"

def acc_regex(args: Dict[str, Any], task_id: str) -> (bool, str):
    path = replace_placeholders(args.get("path",""), task_id)
    path = path.replace("artifacts/", f"{ARTIFACTS_DIR.rstrip('/')}/")
    pat = args.get("pattern","")
    try:
        data = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
        ok = re.search(pat, data) is not None
        return ok, f"path={path} pat={pat}"
    except Exception as e:
        return False, f"path={path} err={e}"

def acc_http_ok(args: Dict[str, Any], _task_id: str) -> (bool, str):
    url = args.get("url","")
    try:
        r = requests.get(url, timeout=10)
        ok = 200 <= r.status_code < 300
        return ok, f"url={url} code={r.status_code}"
    except Exception as e:
        return False, f"url={url} err={e}"

ACCEPTANCE_FUNCS = {
    "file_exists": acc_file_exists,
    "contains": acc_contains,
    "regex": acc_regex,
    "http_ok": acc_http_ok,
}

def run_acceptance(task: Dict[str, Any], task_id: str) -> List[Dict[str, Any]]:
    results = []
    acc_list = task.get("acceptance") or []
    for i, a in enumerate(acc_list):
        typ = (a.get("type") or "").strip()
        f = ACCEPTANCE_FUNCS.get(typ)
        if not f:
            results.append({"index": i, "type": typ, "passed": False, "detail": "unknown acceptance type"})
            continue
        ok, detail = f(a.get("args", {}), task_id)
        results.append({"index": i, "type": typ, "passed": bool(ok), "detail": detail})
    return results

# ---------- Executor ----------
def ensure_artifacts(task_id: str) -> pathlib.Path:
    p = pathlib.Path(ARTIFACTS_DIR) / task_id
    p.mkdir(parents=True, exist_ok=True)
    return p

def execute_task_minimal(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Minimal, safe "execution":
      - make artifacts/{TASK_ID}
      - write output.txt with task info + timestamp
      - if acceptance includes http_ok, fetch and store short response for transparency
    """
    task_id = task["id"]
    steps = []
    t0 = time.time()

    artdir = ensure_artifacts(task_id)
    steps.append({"op":"ensure_artifacts","dir":str(artdir)})

    # Write a simple output file
    out_path = artdir / "output.txt"
    content = f"""TASK {task_id}
TITLE: {task.get('title','')}
DESC: {task.get('description','')}
TIME: {datetime.datetime.utcnow().isoformat()}Z
"""
    out_path.write_text(content, encoding="utf-8")
    steps.append({"op":"write_file","path":str(out_path),"bytes":len(content)})

    # Optional: if acceptance has http_ok, fetch once and save headers/body snippet
    for a in (task.get("acceptance") or []):
        if a.get("type") == "http_ok" and a.get("args",{}).get("url"):
            url = a["args"]["url"]
            try:
                r = requests.get(url, timeout=10)
                (artdir / "http.status").write_text(str(r.status_code), encoding="utf-8")
                body_snip = (r.text or "")[:4096]
                (artdir / "http.body.txt").write_text(body_snip, encoding="utf-8")
                steps.append({"op":"http_get","url":url,"status":r.status_code,"bytes":len(r.content)})
            except Exception as e:
                steps.append({"op":"http_get","url":url,"error":str(e)})

    # Return manifest
    return {
        "steps": steps,
        "duration_sec": round(time.time() - t0, 3),
        "artifacts_dir": str(artdir),
    }

# ---------- CLI ----------
def cmd_list(args):
    with neo().session() as s:
        rows = q_list(s, status=args.status, limit=args.limit)
    if not rows:
        print("(none)")
        return
    for r in rows:
        print(f"[{r.get('tkey') or ''}] {r['id']}  {r['priority']:>6}  {r['title']}")

def cmd_approve(args):
    with neo().session() as s:
        if args.all:
            n = q_approve_all_review(s, limit=args.limit)
        else:
            ids = [x.strip() for x in args.task_ids.split(",") if x.strip()]
            n = q_approve(s, ids)
    print(f"Approved {n} task(s).")

def cmd_accept(args):
    with neo().session() as s:
        row = q_get_task(s, args.task_id)
    if not row:
        print("Task not found"); return
    task = row["task"]
    results = run_acceptance(task, task["id"])
    print(json.dumps({"task_id": task["id"], "results": results}, indent=2))
    passed = all(r["passed"] for r in results) if results else False
    print("PASS" if passed else "FAIL")

def cmd_execute(args):
    picked = []
    with neo().session() as s:
        picked = q_pick_ready(s, limit=args.limit)
    if not picked:
        print("No READY tasks."); return

    for tid in picked:
        with neo().session() as s:
            row = q_get_task(s, tid)
            if not row:
                print(f"{tid}: missing"); continue
            task = row["task"]

            run = {
                "id": str(uuid.uuid4()),
                "started_at": datetime.datetime.utcnow().isoformat()+"Z",
                "status": "RUNNING",
                "manifest": {"task_id": tid, "steps": [], "acceptance_results": []},
            }
            q_attach_run(s, tid, run)

        # Execute outside transaction
        manifest_exec = execute_task_minimal(task)
        results = run_acceptance(task, tid)
        success = all(r["passed"] for r in results) if results else True  # if no acceptance, treat as success

        # finalize
        run["manifest"].update(manifest_exec)
        run["manifest"]["acceptance_results"] = results

        with neo().session() as s:
            q_finish_run(s, tid, run["id"], success, run["manifest"])

        print(f"{tid}: {'DONE' if success else 'FAILED'}  artifacts={manifest_exec['artifacts_dir']}")

def build_cli():
    ap = argparse.ArgumentParser(description="Approve and execute tasks using local FS + Neo4j.")
    sub = ap.add_subparsers()

    p_list = argparse.ArgumentParser(add_help=False)
    sp = sub.add_parser("list", parents=[p_list], help="List tasks by status")
    sp.add_argument("--status", default="REVIEW", choices=["REVIEW","READY","RUNNING","DONE","FAILED"])
    sp.add_argument("--limit", type=int, default=25)
    sp.set_defaults(func=cmd_list)

    p_app = argparse.ArgumentParser(add_help=False)
    sp = sub.add_parser("approve", parents=[p_app], help="Approve tasks (REVIEW -> READY)")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--task-ids", help="Comma-separated Task.id list")
    g.add_argument("--all", action="store_true", help="Approve many REVIEW tasks")
    sp.add_argument("--limit", type=int, default=100, help="Max to approve when using --all")
    sp.set_defaults(func=cmd_approve)

    sp = sub.add_parser("execute", help="Execute READY tasks -> DONE/FAILED")
    sp.add_argument("--limit", type=int, default=5)
    sp.set_defaults(func=cmd_execute)

    sp = sub.add_parser("accept", help="Re-run acceptance for a single task (no execution)")
    sp.add_argument("--task-id", required=True)
    sp.set_defaults(func=cmd_accept)

    return ap

def main():
    ap = build_cli()
    if len(sys.argv) == 1:
        ap.print_help(); sys.exit(0)
    args = ap.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
