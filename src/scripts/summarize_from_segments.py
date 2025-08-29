#!/usr/bin/env python3
"""
Summarize from SEGMENTs (UTTERANCE optional) → Summary + Tasks in Neo4j,
with model-based routing, approve & execute, and local-first Ollama.

Routing
- Summarizer model chosen by transcript length (small/medium/large).
- Reasoner/coder model chosen by task type:
  * requirements  → tiny/fast reasoning
  * mvp_design    → coder-small/medium
  * coding_build  → coder-medium+
  * ops_deploy    → coder-medium+
  * research/other→ general small/medium

You can override any choice via CLI/env.

Env
  NEO4J_URI=bolt://localhost:7687
  NEO4J_USER=neo4j
  NEO4J_PASSWORD=<pwd>
  OLLAMA_HOST=http://localhost:11434
  ARTIFACTS_DIR=./artifacts
  SUMMARIZER_MODEL (optional explicit override)
  REASONER_MODEL   (optional explicit override)

Examples
  # Dry-run on latest (no writes)
  python summarize_from_segments.py --latest --dry-run

  # Batch, then approve & execute end-to-end
  python summarize_from_segments.py --all-missing --limit 100 --write-notes \
    --approve-all 1000 --execute-ready 10

  # Force models explicitly (router still runs but respects your explicit picks)
  python summarize_from_segments.py --latest \
    --model-summarizer gemma2:2b --model-reasoner qwen2.5-coder:0.5b
"""
import os, json, argparse, time, re, uuid, datetime, pathlib
from typing import List, Dict, Any, Optional, Iterable
import requests
from neo4j import GraphDatabase

# ============== Env & Defaults ==============
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", "./artifacts")

# Preferred model lists (ordered). These are *names* as shown by /api/tags.
DEFAULT_SUMMARIZER_PREFS: list[str] = [
    os.getenv("SUMMARIZER_MODEL"),
    "gemma2:2b",         # small, fast, good for short summaries
    "llama3:latest",     # 8B general
    "qwen3-coder:30b",   # if you have it and want bigger summaries
]

DEFAULT_REASONER_PREFS_GENERAL: list[str] = [
    os.getenv("REASONER_MODEL"),
    "qwen2.5-coder:0.5b",   # tiny logic
    "gemma2:2b",
    "llama3:latest",
    "qwen3-coder:latest",
]

DEFAULT_REASONER_PREFS_CODER_SMALL: list[str] = [
    os.getenv("REASONER_MODEL"),
    "qwen2.5-coder:0.5b",
    "qwen2.5-coder:3b",
    "qwen3-coder:latest",
    "llama3:latest",
]

DEFAULT_REASONER_PREFS_CODER_MEDIUM: list[str] = [
    os.getenv("REASONER_MODEL"),
    "qwen2.5-coder:3b",
    "qwen3-coder:latest",
    "llama3:latest",
]

DEFAULT_REASONER_PREFS_CODER_LARGE: list[str] = [
    os.getenv("REASONER_MODEL"),
    "qwen3-coder:latest",
    "qwen2.5-coder:3b",
    "llama3:latest",
]

# Length routing thresholds (characters)
DEFAULT_LEN_MEDIUM = int(os.getenv("LEN_THRESHOLD_MEDIUM", "8000"))
DEFAULT_LEN_LARGE  = int(os.getenv("LEN_THRESHOLD_LARGE",  "30000"))

# ============== Prompts ==============
SUMMARIZE_INSTR = """You are given a conversation transcript composed of time-ordered items.
Rules:
- Treat SEGMENT items as authoritative.
- Items marked LOW_CONF (from UTTERANCE) are lower confidence; only use them if consistent with SEGMENT content.
- Output must be faithful, concise, and specific. Avoid speculation.

Return ONLY a JSON object with:
{
  "summary": string,
  "bullets": string[]
}"""

TASKS_INSTR = """From the provided summary+bullets, extract ACTION ITEMS strictly as JSON:

{
  "summary": string,
  "bullets": string[],
  "tasks": [
    {
      "title": string,
      "description": string,
      "priority": "LOW"|"MEDIUM"|"HIGH",
      "due": string|null,
      "confidence": number,
      "acceptance": [
        {
          "type": "file_exists" | "contains" | "regex" | "http_ok",
          "args": { "path"?: string, "text"?: string, "pattern"?: string, "url"?: string }
        }
      ]
    }
  ]
}

Guidelines:
- Prefer MEDIUM unless urgency suggests HIGH.
- Include a due date only if stated or clearly implied.
- Confidence in [0,1].
- IMPORTANT: ACCEPTANCE IS REQUIRED WHENEVER POSSIBLE. Prefer outputs under artifacts/{TASK_ID}/output.txt to enable verification.
Return ONLY JSON (no prose)."""

# ============== Neo4j helpers ==============
def neo_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def fetch_one(session, trans_id: Optional[str], latest: bool) -> Optional[Dict[str, Any]]:
    if trans_id:
        q = """
        MATCH (t:Transcription {id:$id})
        OPTIONAL MATCH (t)-[:HAS_SEGMENT]->(s:Segment)
        WITH t, s ORDER BY coalesce(s.start, s.idx, 0)
        WITH t, collect({id:s.id, start:coalesce(s.start,0.0), end:coalesce(s.end,0.0), text:s.text, type:'SEGMENT', low_conf:false}) AS segs
        OPTIONAL MATCH (t)-[:HAS_UTTERANCE]->(u)
        WITH t, segs, u ORDER BY coalesce(u.start, u.idx, 0)
        RETURN t, segs, collect({id:u.id, start:coalesce(u.start,0.0), end:coalesce(u.end,0.0), text:u.text, type:'UTTERANCE', low_conf:true}) AS utts
        """
        rec = session.run(q, id=trans_id).single()
    else:
        q = """
        MATCH (t:Transcription)
        WITH t ORDER BY coalesce(t.created_at, datetime()) DESC
        LIMIT 1
        OPTIONAL MATCH (t)-[:HAS_SEGMENT]->(s:Segment)
        WITH t, s ORDER BY coalesce(s.start, s.idx, 0)
        WITH t, collect({id:s.id, start:coalesce(s.start,0.0), end:coalesce(s.end,0.0), text:s.text, type:'SEGMENT', low_conf:false}) AS segs
        OPTIONAL MATCH (t)-[:HAS_UTTERANCE]->(u)
        WITH t, segs, u ORDER BY coalesce(u.start, u.idx, 0)
        RETURN t, segs, collect({id:u.id, start:coalesce(u.start,0.0), end:coalesce(u.end,0.0), text:u.text, type:'UTTERANCE', low_conf:true}) AS utts
        """
        rec = session.run(q).single()

    if not rec:
        return None
    t = dict(rec["t"])
    segs = [x for x in (rec["segs"] or []) if x and x.get("text")]
    utts = [x for x in (rec["utts"] or []) if x and x.get("text")]
    return {"t": t, "segments": segs, "utterances": utts}

def fetch_missing_transcriptions(session, limit: int) -> List[Dict[str, Any]]:
    q = """
    MATCH (t:Transcription)
    WHERE NOT (t)-[:HAS_SUMMARY]->(:Summary)
       OR trim(coalesce(t.notes, '')) = ''
    RETURN t
    ORDER BY coalesce(t.created_at, datetime()) DESC
    LIMIT $limit
    """
    return [dict(r["t"]) for r in session.run(q, limit=limit)]

# ============== Transcript assembly ==============
def build_transcript(segments: List[Dict[str,Any]], utterances: List[Dict[str,Any]], include_utterances: bool=False) -> str:
    items = list(segments)
    if include_utterances:
        items += utterances
    items.sort(key=lambda r: (float(r.get("start",0.0)), float(r.get("end",0.0))))
    lines = []
    for it in items:
        txt = (it.get("text") or "").strip()
        if not txt:
            continue
        if it["type"] == "UTTERANCE":
            lines.append(f"[LOW_CONF] {txt}")
        else:
            lines.append(txt)
    return "\n".join(lines)

# ============== Model discovery & routing ==============
def _post_ollama(endpoint: str, payload: dict, timeout: int = 180):
    return requests.post(f"{OLLAMA_HOST}{endpoint}", json=payload, timeout=timeout)

def installed_models() -> set[str]:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=10)
        r.raise_for_status()
        data = r.json()
        names = {m.get("name") or m.get("model") for m in data.get("models", [])}
        return {n for n in names if n}
    except Exception:
        return set()

def pick_model(prefs: Iterable[Optional[str]], installed: set[str]) -> str:
    for tag in [p for p in prefs if p]:
        if tag in installed:
            return tag
    for tag in prefs:
        if tag:
            return tag
    return "llama3:latest"

def estimate_len(text: str) -> int:
    return len(text or "")

def route_summarizer(text_len: int, installed: set[str], explicit: Optional[str],
                     len_medium: int, len_large: int) -> str:
    if explicit:
        return explicit
    if text_len < len_medium:
        prefs = [None, "gemma2:2b", "llama3:latest", "qwen3-coder:30b"]
    elif text_len < len_large:
        prefs = [None, "llama3:latest", "gemma2:2b", "qwen3-coder:30b"]
    else:
        prefs = [None, "qwen3-coder:30b", "llama3:latest", "gemma2:2b"]
    return pick_model(prefs, installed)

def classify_task_category(summary_obj: Dict[str,Any]) -> str:
    """
    Heuristic task-type classifier from summary+bullets.
    Returns one of: requirements, mvp_design, coding_build, ops_deploy, research
    """
    text = (summary_obj.get("summary","") + " " + " ".join(summary_obj.get("bullets",[]))).lower()

    # requirements
    if re.search(r"\b(requirements?|spec|acceptance criteria|user stories|story points|backlog)\b", text):
        return "requirements"

    # mvp/design
    if re.search(r"\b(mvp|prototype|design doc|architecture|uml|erd|schema|interface design|api design)\b", text):
        return "mvp_design"

    # coding/build
    if re.search(r"\b(implement|build|code|refactor|unit tests?|integration tests?|library|sdk|cli|service|microservice|module)\b", text):
        return "coding_build"

    # deploy/ops
    if re.search(r"\b(deploy|docker|compose|kubernetes|helm|ci/cd|pipeline|release|prod|staging|infra|observability|monitoring|prometheus|grafana)\b", text):
        return "ops_deploy"

    # research / misc
    return "research"

def route_reasoner(category: str, installed: set[str], explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    if category == "requirements":
        prefs = DEFAULT_REASONER_PREFS_CODER_SMALL
    elif category == "mvp_design":
        prefs = DEFAULT_REASONER_PREFS_CODER_SMALL
    elif category == "coding_build":
        prefs = DEFAULT_REASONER_PREFS_CODER_MEDIUM
    elif category == "ops_deploy":
        prefs = DEFAULT_REASONER_PREFS_CODER_MEDIUM
    else:
        prefs = DEFAULT_REASONER_PREFS_GENERAL
    return pick_model(prefs, installed)

# ============== Robust Ollama JSON ==============
def ollama_chat_json(model: str, system: str, user: str, temperature: float = 0.2) -> Dict[str, Any]:
    """
    1) Try /api/chat (non-streaming, format=json). If 404, use /api/generate.
    2) Handle NDJSON or code-fenced JSON.
    """
    chat_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "options": {"temperature": temperature, "num_ctx": 8192},
        "format": "json",
        "stream": False,
    }
    try:
        r = _post_ollama("/api/chat", chat_payload)
        if r.status_code == 404:
            raise RuntimeError("chat endpoint not found (404)")
        r.raise_for_status()
        try:
            body = r.json()
            content = body.get("message", {}).get("content", "")
            obj = _coerce_json_dict(content)
            if obj:
                return obj
        except Exception:
            pass
        return _coerce_json_dict(_stitch_ndjson(r.text))
    except Exception:
        gen_payload = {
            "model": model,
            "prompt": f"{system}\n\n{user}",
            "options": {"temperature": temperature, "num_ctx": 8192},
            "format": "json",
            "stream": False,
        }
        r = _post_ollama("/api/generate", gen_payload)
        r.raise_for_status()
        try:
            body = r.json()
            content = body.get("response", "")
            obj = _coerce_json_dict(content)
            if obj:
                return obj
        except Exception:
            pass
        return _coerce_json_dict(_stitch_ndjson(r.text))

def _stitch_ndjson(text: str) -> str:
    if not text:
        return ""
    pieces = []
    for ln in [ln for ln in text.splitlines() if ln.strip()]:
        try:
            o = json.loads(ln)
            if "message" in o and "content" in o["message"]:
                pieces.append(o["message"]["content"])
            elif "response" in o:
                pieces.append(o["response"])
        except Exception:
            return text
    return "".join(pieces) if pieces else text

def _coerce_json_dict(s: str) -> Dict[str, Any]:
    if not s:
        return {}
    s = re.sub(r"^\s*```(?:json)?\s*", "", s.strip(), flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s, flags=re.IGNORECASE)
    try:
        o = json.loads(s)
        if isinstance(o, dict):
            return o
    except Exception:
        pass
    start = s.find("{"); end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            o = json.loads(s[start:end+1])
            if isinstance(o, dict):
                return o
        except Exception:
            pass
    return {}

# ============== Map-Reduce Summary & Tasks ==============
def chunk(text: str, max_chars: int = 7000) -> List[str]:
    chunks, buf = [], ""
    for line in text.splitlines():
        if len(buf) + len(line) + 1 > max_chars:
            if buf: chunks.append(buf); buf = line
        else:
            buf = (buf+"\n"+line) if buf else line
    if buf: chunks.append(buf)
    return chunks

def summarize_as_json(model: str, full_text: str) -> Dict[str, Any]:
    parts = chunk(full_text)
    if len(parts) == 1:
        obj = ollama_chat_json(model, SUMMARIZE_INSTR, parts[0])
        return _normalize_summary_obj(obj)

    partials = []
    for i, ch in enumerate(parts, 1):
        pj = ollama_chat_json(model, SUMMARIZE_INSTR, f"[CHUNK {i}/{len(parts)}]\n\n{ch}")
        partials.append(_normalize_summary_obj(pj))

    combined = json.dumps(partials, ensure_ascii=False)
    final = ollama_chat_json(
        model, SUMMARIZE_INSTR,
        "Combine these partial results into one final JSON with shape {summary, bullets}:\n" + combined
    )
    return _normalize_summary_obj(final)

def _normalize_summary_obj(obj: Any) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return {"summary": str(obj), "bullets": []}
    summary = str(obj.get("summary",""))
    bullets = obj.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = [str(bullets)]
    bullets = [str(b).strip() for b in bullets if str(b).strip()]
    return {"summary": summary, "bullets": bullets}

def extract_tasks_json(model: str, summary_obj: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps(
        {"summary": summary_obj.get("summary",""), "bullets": summary_obj.get("bullets",[])},
        ensure_ascii=False
    )
    obj: Dict[str, Any] | None = None
    for _ in range(3):
        try:
            obj = ollama_chat_json(model, "Return STRICT JSON only.", TASKS_INSTR + "\n\n" + payload)
            if isinstance(obj, dict) and "tasks" in obj:
                break
        except Exception:
            time.sleep(0.5)
    if not isinstance(obj, dict):
        obj = {"summary": summary_obj.get("summary",""), "bullets": summary_obj.get("bullets",[]), "tasks": []}

    for t in obj.get("tasks", []):
        if not t.get("acceptance"):
            t["acceptance"] = [{"type":"file_exists","args":{"path":"artifacts/{TASK_ID}/output.txt"}}]
        t["title"] = str(t.get("title","")).strip() or "Untitled Task"
        t["description"] = str(t.get("description","")).strip()
        pri = str(t.get("priority","MEDIUM")).upper()
        if pri not in {"LOW","MEDIUM","HIGH"}: pri = "MEDIUM"
        t["priority"] = pri
        try:
            t["confidence"] = float(t.get("confidence", 0.5))
        except Exception:
            t["confidence"] = 0.5
    return obj

# ============== Write-back ==============
def write_back(session, trans_id: str, final_obj: Dict[str, Any], write_notes: bool=False) -> str:
    q = """
    MATCH (t:Transcription {id:$tid})
    CREATE (s:Summary {id: randomUUID(), text:$text, bullets:$bullets, created_at: datetime()})
    MERGE (t)-[:HAS_SUMMARY]->(s)
    WITH t, s
    FOREACH (_ IN (CASE WHEN $write_notes THEN [1] ELSE [] END) |
        SET t.notes = $text, t.updated_at = datetime()
    )
    WITH s
    UNWIND $tasks AS tsk
      CREATE (tk:Task {
        id: randomUUID(),
        title: tsk.title,
        description: coalesce(tsk.description,""),
        priority: coalesce(tsk.priority,"MEDIUM"),
        due: tsk.due,
        status: "REVIEW",
        confidence: coalesce(tsk.confidence,0.5),
        acceptance: coalesce(tsk.acceptance, [])
      })
      MERGE (s)-[:GENERATED_TASK]->(tk)
    RETURN s.id as sid
    """
    bullets = final_obj.get("bullets") or []
    sid = session.run(
        q,
        tid=trans_id,
        text=final_obj.get("summary",""),
        bullets=bullets,
        tasks=final_obj.get("tasks",[]),
        write_notes=bool(write_notes)
    ).single()["sid"]
    return sid

# ============== Per-transcription driver (with routing) ==============
def process_one(session, tnode: Dict[str,Any], include_utterances: bool, dry_run: bool, write_notes: bool,
                explicit_summarizer: Optional[str], explicit_reasoner: Optional[str],
                installed: set[str], len_medium: int, len_large: int) -> Optional[str]:
    row = fetch_one(session, tnode.get("id"), latest=False)
    if not row:
        print(f"[{tnode.get('key') or tnode.get('id')}] not found or empty.")
        return None
    t = row["t"]; segments, utterances = row["segments"], row["utterances"]
    key = t.get("key") or t.get("id")
    if not segments and not utterances:
        print(f"[{key}] No text found in Segments/Utterances.")
        return None

    text = build_transcript(segments, utterances, include_utterances=include_utterances)
    text_len = estimate_len(text)

    # Route summarizer by length (unless explicitly overridden)
    summarizer_model = route_summarizer(text_len, installed, explicit_summarizer, len_medium, len_large)

    print(f"[{key}] Items: {len(segments)} segments + {len(utterances)} utterances (included={include_utterances})")
    print(f"   routing: text_len={text_len} → summarizer={summarizer_model}")

    summary_obj = summarize_as_json(summarizer_model, text)

    # Route reasoner by task category
    category = classify_task_category(summary_obj)
    reasoner_model = route_reasoner(category, installed, explicit_reasoner)
    print(f"   routing: task_category={category} → reasoner={reasoner_model}")

    final_obj = extract_tasks_json(reasoner_model, summary_obj)

    if dry_run:
        print(json.dumps({"transcription": key,
                          "routing": {"summarizer": summarizer_model, "reasoner": reasoner_model, "category": category},
                          **final_obj}, indent=2))
        return None

    sid = write_back(session, t.get("id"), final_obj, write_notes=write_notes)
    print(f"[{key}] Wrote Summary {sid} and {len(final_obj.get('tasks',[]))} Task(s).")
    return sid

# ============== Approve & Execute ==============
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
        """, id=task_id
    ).single()
    if not rec:
        return None
    task = dict(rec["task"])
    s = dict(rec["s"]) if rec["s"] else None
    t = dict(rec["t"]) if rec["t"] else None
    return {"task": task, "summary": s, "transcription": t}

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

def q_pick_ready(session, limit: int) -> List[str]:
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

def ensure_artifacts(task_id: str) -> pathlib.Path:
    p = pathlib.Path(ARTIFACTS_DIR) / task_id
    p.mkdir(parents=True, exist_ok=True)
    return p

def execute_task_minimal(task: Dict[str, Any]) -> Dict[str, Any]:
    steps = []
    t0 = time.time()
    tid = task["id"]

    artdir = ensure_artifacts(tid)
    steps.append({"op":"ensure_artifacts","dir":str(artdir)})

    out_path = artdir / "output.txt"
    content = f"""TASK {tid}
TITLE: {task.get('title','')}
DESC: {task.get('description','')}
TIME: {datetime.datetime.utcnow().isoformat()}Z
"""
    out_path.write_text(content, encoding="utf-8")
    steps.append({"op":"write_file","path":str(out_path),"bytes":len(content)})

    for a in (task.get("acceptance") or []):
        if a.get("type") == "http_ok" and a.get("args",{}).get("url"):
            url = a["args"]["url"]
            try:
                r = requests.get(url, timeout=10)
                (artdir / "http.status").write_text(str(r.status_code), encoding="utf-8")
                (artdir / "http.body.txt").write_text((r.text or "")[:4096], encoding="utf-8")
                steps.append({"op":"http_get","url":url,"status":r.status_code,"bytes":len(r.content)})
            except Exception as e:
                steps.append({"op":"http_get","url":url,"error":str(e)})

    return {"steps": steps, "duration_sec": round(time.time() - t0, 3), "artifacts_dir": str(artdir)}

def approve_all(session, limit: int) -> int:
    n = q_approve_all_review(session, limit=limit)
    print(f"Approved {n} task(s) from REVIEW → READY.")
    return n

def execute_ready(limit: int):
    with neo_driver().session() as s:
        picked = q_pick_ready(s, limit=limit)
    if not picked:
        print("No READY tasks."); return

    for tid in picked:
        with neo_driver().session() as s:
            row = q_get_task(s, tid)
            if not row:
                print(f"{tid}: not found"); continue
            task = row["task"]
            run = {
                "id": str(uuid.uuid4()),
                "started_at": datetime.datetime.utcnow().isoformat()+"Z",
                "status": "RUNNING",
                "manifest": {"task_id": tid, "steps": [], "acceptance_results": []},
            }
            q_attach_run(s, tid, run)

        manifest_exec = execute_task_minimal(task)
        results = run_acceptance(task, tid)
        success = all(r["passed"] for r in results) if results else True

        run["manifest"].update(manifest_exec)
        run["manifest"]["acceptance_results"] = results

        with neo_driver().session() as s:
            q_finish_run(s, tid, run["id"], success, run["manifest"])

        print(f"{tid}: {'DONE' if success else 'FAILED'}  artifacts={manifest_exec['artifacts_dir']}")

# ============== CLI ==============
def main():
    ap = argparse.ArgumentParser(description="Segments → Summary → Tasks in Neo4j, with model routing + approve/execute.")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--id", dest="trans_id", help="Transcription.id to process")
    sel.add_argument("--latest", action="store_true", help="Pick the latest Transcription")
    sel.add_argument("--all-missing", action="store_true", help="Process transcriptions missing notes (no Summary OR empty t.notes)")

    ap.add_argument("--limit", type=int, default=50, help="Max items when using --all-missing")
    ap.add_argument("--include-utterances", action="store_true", help="Include UTTERANCE as LOW_CONF lines")
    ap.add_argument("--write-notes", action="store_true", help="Also write summary text into t.notes")
    ap.add_argument("--dry-run", action="store_true", help="Print JSON only (no writes)")

    # Approve & execute
    ap.add_argument("--approve-all", nargs="?", const=100, type=int,
                    help="Approve up to N tasks in REVIEW (default 100 if no value provided)")
    ap.add_argument("--execute-ready", type=int, default=0,
                    help="Execute up to N tasks in READY")
    ap.add_argument("--list", dest="list_status", choices=["REVIEW","READY","RUNNING","DONE","FAILED"],
                    help="List tasks by status (no writes)")

    # Model overrides
    ap.add_argument("--model-summarizer", help="Override summarizer model tag")
    ap.add_argument("--model-reasoner", help="Override reasoner/coder model tag")

    # Length thresholds for routing
    ap.add_argument("--len-threshold-medium", type=int, default=DEFAULT_LEN_MEDIUM)
    ap.add_argument("--len-threshold-large",  type=int, default=DEFAULT_LEN_LARGE)

    # Artifacts dir
    ap.add_argument("--artifacts-dir", help="Override ARTIFACTS_DIR (default ./artifacts)")

    args = ap.parse_args()
    global ARTIFACTS_DIR
    if args.artifacts_dir:
        ARTIFACTS_DIR = args.artifacts_dir

    # Discovery and listing
    installed = installed_models()

    if args.list_status:
        with neo_driver().session() as s:
            rows = q_list(s, status=args.list_status, limit=50)
        if not rows:
            print("(none)")
        else:
            for r in rows:
                print(f"[{r.get('tkey') or ''}] {r['id']}  {r['priority']:>6}  {r['title']}")
        return

    # Summarize targets if specified
    did_summarize = False
    with neo_driver().session() as sess:
        if args.trans_id or args.latest or args.all_missing:
            did_summarize = True
            if args.trans_id or args.latest:
                if args.latest and not args.trans_id:
                    row = fetch_one(sess, None, latest=True)
                    if not row:
                        print("No Transcription found."); return
                    tnode = row["t"]
                else:
                    tnode = {"id": args.trans_id}
                process_one(sess, tnode, args.include_utterances, args.dry_run, args.write_notes,
                            args.model_summarizer, args.model_reasoner, installed,
                            args.len_threshold_medium, args.len_threshold_large)
            else:
                cands = fetch_missing_transcriptions(sess, limit=args.limit)
                if not cands:
                    print("No candidates found (all have notes/summaries).")
                else:
                    for tnode in cands:
                        process_one(sess, tnode, args.include_utterances, args.dry_run, args.write_notes,
                                    args.model_summarizer, args.model_reasoner, installed,
                                    args.len_threshold_medium, args.len_threshold_large)

    # Approve & Execute phases (independent)
    if args.approve_all is not None:
        with neo_driver().session() as s:
            approve_all(s, limit=args.approve_all)
    if args.execute_ready and args.execute_ready > 0:
        execute_ready(args.execute_ready)

    if not did_summarize and args.approve_all is None and args.execute_ready == 0 and not args.list_status:
        print("Nothing to do. Provide --id/--latest/--all-missing to summarize, or --approve-all / --execute-ready, or --list STATUS.")

if __name__ == "__main__":
    main()
