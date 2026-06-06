# src/assistx/pipeline/qa_pipeline.py

import os, json, hashlib
import time
import math
from typing import Dict, Any, Optional
from ..deps import load_redis_module
import requests

redis = load_redis_module()

from ..neo4j_client import Neo4jClient
from ..neo_schema import fetch_schema
from ..agents.executor import execute_with_repairs
from ..agents.analyst import generate_analysis_code, run_user_code
from ..agents.llm import chat
from ..llm_client import embed as _llm_embed
from ..metrics import QA_DURATION, QA_CYPHER_ATTEMPTS

# ---- cache config ----
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QA_CACHE_TTL_S = int(os.getenv("QA_CACHE_TTL_S", "3600"))  # 1h default
CACHE_VERSION = "v1"
QA_SIMILARITY_THRESHOLD = float(os.getenv("QA_SIMILARITY_THRESHOLD", "0.92"))
QA_SIMILAR_MAX_SCAN = int(os.getenv("QA_SIMILAR_MAX_SCAN", "200"))
SIM_INDEX_KEY = "assistx:qa:sim:index"

# create a single Redis client (binary-safe)
_rds = redis.from_url(REDIS_URL, decode_responses=False)


def _schema_fp(schema: Dict[str, Any]) -> str:
    """Stable short fingerprint of the live schema."""
    return hashlib.sha1(json.dumps(schema, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _cache_key(question: str, schema_fp: str) -> bytes:
    """Cache key derived from normalized question + schema fp."""
    qhash = hashlib.sha1(question.strip().lower().encode("utf-8")).hexdigest()
    return f"assistx:qa:{CACHE_VERSION}:{schema_fp}:{qhash}".encode()


def _sim_entry_id(question: str, schema_fp: str) -> str:
    qhash = hashlib.sha1(question.strip().lower().encode("utf-8")).hexdigest()
    return f"{schema_fp}:{qhash}"


def _sim_entry_key(entry_id: str) -> bytes:
    return f"assistx:qa:sim:{entry_id}".encode()


def _embed_text(text: str) -> Optional[list[float]]:
    return _llm_embed(text)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return -1.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _find_similar_cached(question: str, schema_fp: str) -> Optional[Dict[str, Any]]:
    qvec = _embed_text(question)
    if not qvec:
        return None
    try:
        ids = _rds.zrevrange(SIM_INDEX_KEY, 0, max(0, QA_SIMILAR_MAX_SCAN - 1))
    except Exception:
        return None
    best_obj = None
    best_score = -1.0
    for raw_id in ids:
        entry_id = raw_id.decode("utf-8") if isinstance(raw_id, bytes) else str(raw_id)
        if not entry_id.startswith(f"{schema_fp}:"):
            continue
        try:
            blob = _rds.get(_sim_entry_key(entry_id))
        except Exception:
            blob = None
        if not blob:
            continue
        try:
            entry = json.loads(blob.decode("utf-8"))
            evec = entry.get("embedding")
            if not isinstance(evec, list):
                continue
            score = _cosine(qvec, [float(x) for x in evec])
            if score > best_score:
                best_score = score
                best_obj = entry
        except Exception:
            continue
    if best_obj and best_score >= QA_SIMILARITY_THRESHOLD:
        out = dict(best_obj.get("answer") or {})
        out["cached"] = True
        out["similar_cached"] = True
        out["similarity"] = best_score
        out["source_question"] = best_obj.get("question")
        return out
    return None


def _store_similar_entry(question: str, schema_fp: str, answer_obj: Dict[str, Any]) -> None:
    vec = _embed_text(question)
    if not vec:
        return
    entry_id = _sim_entry_id(question, schema_fp)
    record = {
        "question": question,
        "schema_fp": schema_fp,
        "embedding": vec,
        "answer": answer_obj,
    }
    now = int(time.time() * 1000)
    try:
        _rds.setex(_sim_entry_key(entry_id), QA_CACHE_TTL_S, json.dumps(record).encode("utf-8"))
        _rds.zadd(SIM_INDEX_KEY, {entry_id: now})
        # light cleanup of old index members
        stale_cutoff = now - (QA_CACHE_TTL_S * 1000)
        _rds.zremrangebyscore(SIM_INDEX_KEY, 0, stale_cutoff)
    except Exception:
        pass


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
    started = time.perf_counter()
    try:
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
        similar = _find_similar_cached(question, fp)
        if similar:
            return similar

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
        attempt_count = len(attempts) if isinstance(attempts, list) else int(attempts or 0)
        QA_CYPHER_ATTEMPTS.inc(max(1, attempt_count))
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
        _store_similar_entry(question, fp, out)

        if run_id:
            neo.log_artifact(run_id, kind="qa.answer", path="inline", sha256=None)
            neo.complete_run(run_id, status="DONE")

        return out
    finally:
        QA_DURATION.observe(max(0.0, time.perf_counter() - started))
