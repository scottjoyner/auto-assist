# src/assistx/pipeline/qa_pipeline.py

import os, json, hashlib
from typing import Dict, Any, Optional
import redis

from ..neo4j_client import Neo4jClient
from ..neo_schema import fetch_schema
from ..agents.executor import execute_with_repairs
from ..agents.analyst import generate_analysis_code, run_user_code
from ..agents.llm import chat

# ---- cache config ----
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QA_CACHE_TTL_S = int(os.getenv("QA_CACHE_TTL_S", "3600"))  # 1h default
CACHE_VERSION = "v1"

# create a single Redis client (binary-safe)
_rds = redis.from_url(REDIS_URL, decode_responses=False)


def _schema_fp(schema: Dict[str, Any]) -> str:
    """Stable short fingerprint of the live schema."""
    return hashlib.sha1(json.dumps(schema, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _cache_key(question: str, schema_fp: str) -> bytes:
    """Cache key derived from normalized question + schema fp."""
    qhash = hashlib.sha1(question.strip().lower().encode("utf-8")).hexdigest()
    return f"assistx:qa:{CACHE_VERSION}:{schema_fp}:{qhash}".encode()


def answer_question(
    neo: Neo4jClient,
    question: str,
    model: Optional[str] = None,
    max_repairs: int = 3,
    log_to_neo: bool = True,
) -> Dict[str, Any]:
    """
    End-to-end QA pipeline:
      1) Introspect live Neo4j schema
      2) LLM drafts Cypher; executor retries with repairs on errors
      3) LLM emits Python analysis code; we run it in a tiny sandbox
      4) Final LLM writes the user-facing answer
      5) Whole chain logged to Neo4j; result cached in Redis

    Returns:
      {
        "answer": str,
        "data_preview": [ ...first rows... ],
        "cypher": str,
        "analysis_code": str,
        "computed": dict,     # result from analysis
        "stdout": str,        # prints from analysis
        "cached": bool,
        "run_id": str | None,
      }
    """
    # ---- 1) schema & cache check ----
    schema = fetch_schema(neo)
    fp = _schema_fp(schema)
    ckey = _cache_key(question, fp)

    try:
        cached = _rds.get(ckey)
    except Exception:
        cached = None  # tolerate Redis outages

    if cached:
        obj = json.loads(cached.decode("utf-8"))
        obj["cached"] = True
        return obj

    # ---- 2) create an AgentRun for full chain logging ----
    run_id = None
    if log_to_neo:
        run_id = neo.create_run(
            task_id="qa_ad_hoc",
            agent="QAOrchestrator",
            model=model or os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            manifest={"question": question, "schema_fp": fp, "cache_version": CACHE_VERSION},
        )

    def log(tool: str, input_json: Dict[str, Any] | None = None, output_json: Dict[str, Any] | None = None, ok: bool = True):
        if not run_id:
            return
        neo.log_tool_call(run_id, tool=tool, input_json=input_json or {}, output_json=output_json, ok=ok)

    # ---- 3) generate/repair/execute Cypher ----
    cypher, rows, attempts = execute_with_repairs(
        neo, question=question, schema=schema, max_attempts=max_repairs, log_cb=lambda **e: log("cypher.step", e, None, True)
    )
    log("cypher.final", {"attempts": attempts}, {"rows_count": len(rows)}, True)

    # ---- 4) produce + run analysis code ----
    plan = generate_analysis_code(question, rows)
    code = plan.get("code", "") or ""
    log("analysis.plan", {"notes": plan.get("notes", ""), "code_len": len(code)}, None, True)

    computed, stdout = run_user_code(code, rows)
    log("analysis.exec", {"code": code}, {"stdout": stdout, "result": computed}, True)

    # ---- 5) final LLM answer (concise; uses computed result) ----
    final_msgs = [
        {"role": "system", "content": "You are a precise assistant. Use the provided computed results to answer clearly and concisely. If there are caveats, note them."},
        {"role": "user", "content": json.dumps({"question": question, "computed": computed, "sample_rows": rows[:10]}, ensure_ascii=False)},
    ]
    answer = chat(final_msgs, model=model)
    log("answer.compose", {"question": question}, {"chars": len(answer or "")}, True)

    # ---- 6) assemble, cache, and finish ----
    out = {
        "answer": answer,
        "data_preview": rows[:10],
        "cypher": cypher,
        "analysis_code": code,
        "computed": computed,
        "stdout": stdout,
        "cached": False,
        "run_id": run_id,
    }

    try:
        _rds.setex(ckey, QA_CACHE_TTL_S, json.dumps(out).encode("utf-8"))
    except Exception:
        pass  # tolerate Redis being down

    if run_id:
        neo.log_artifact(run_id, kind="qa.answer", path="inline", sha256=None)
        neo.complete_run(run_id, status="DONE")

    return out
