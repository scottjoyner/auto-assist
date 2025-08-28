
from __future__ import annotations
from .neo4j_client import Neo4jClient
from .ollama_llm import text_chat, json_chat
from .schemas import ExtractedTasks
from pathlib import Path
from typing import List, Dict, Any, Tuple
from .logging_utils import get_logger
import json, difflib

logger = get_logger()

SUMMARIZE_PROMPT = Path(__file__).with_suffix("").parent / "prompts" / "summarize.md"
TASKS_PROMPT = Path(__file__).with_suffix("").parent / "prompts" / "tasks.md"
CRITIC_PROMPT = Path(__file__).with_suffix("").parent / "prompts" / "critic.md"

def chunk_texts(texts: List[str], max_chars: int = 6000) -> List[str]:
    chunks, buf = [], ""
    for t in texts:
        if len(buf) + len(t) + 1 > max_chars:
            if buf: chunks.append(buf)
            buf = t
        else:
            buf = (buf + "\n" + t) if buf else t
    if buf: chunks.append(buf)
    return chunks

def infer_acceptance(task: dict) -> list[dict] | None:
    title = (task.get("title") or "").lower()
    desc = (task.get("description") or "").lower()
    checks = []
    text = title + " " + desc
    if any(w in text for w in ["write","generate","draft","report","summary","export","save","file"]):
        checks.append({"type": "file_exists", "args": {"path": "artifacts/{task_id}/output.txt"}})
    if any(w in text for w in ["summary","report","analysis"]):
        checks.append({"type": "contains", "args": {"path": "artifacts/{task_id}/output.txt", "text": "Summary"}})
    if any(w in text for w in ["publish","post","url","endpoint","api","site","http"]):
        checks.append({"type": "http_ok", "args": {"url": "https://example.com/replace-with-real"}})
    return checks or None

def locate_quote(source: str, quote: str) -> Tuple[int,int] | None:
    if not quote: return None
    q = quote.strip()
    i = source.find(q)
    if i != -1:
        return i, i+len(q)
    anchor = q[: min(40, len(q))]
    i = source.find(anchor)
    if i != -1:
        return i, i+len(anchor)
    for size in (60, 80, 120):
        for start in range(0, max(1, len(source)-size), max(1, size//2)):
            window = source[start:start+size]
            if difflib.SequenceMatcher(None, q, window).ratio() > 0.7:
                return start, start+len(window)
    return None

def ground_bullets(bullets: list[str], source_text: str) -> list[dict]:
    prompt = "Given these bullets and the transcript text, return JSON as {\"bullets\":[{\"index\":n,\"evidence\":[{\"quote\":string,\"rationale\":string}]}]} with exact quotes under 160 chars that best support each bullet. Do not invent."
    payload = {"bullets": bullets, "transcript_hint": source_text[:8000]}
    j = json_chat(prompt + "\n\n" + json.dumps(payload, ensure_ascii=False), schema_hint="Grounding")
    out = []
    for b in j.get("bullets", []):
        idx = b.get("index", 0)
        for ev in b.get("evidence", []):
            q = ev.get("quote","")
            loc = locate_quote(source_text, q)
            if not loc:
                continue
            out.append({"bullet_index": idx, "quote": q, "rationale": ev.get("rationale",""), "char_start": loc[0], "char_end": loc[1]})
    return out

def summarize_conversation(neo: Neo4jClient, conversation_id: str):
    # Pull utterances (ids + text) in chronological order
    with neo.driver.session() as s:
        res = s.run("MATCH (c:Conversation{id:$id})-[:HAS_UTTERANCE]->(u:Utterance) RETURN u.id, u.text ORDER BY u.started_at, u.id", {"id": conversation_id})
        utts = [(r[0], r[1]) for r in res]
    if not utts:
        return

    texts = [u[1] for u in utts]
    base_instr = SUMMARIZE_PROMPT.read_text()
    chunks = chunk_texts(texts)

    partials = []
    for i, ch in enumerate(chunks):
        prompt = f"{base_instr}\n\n[CHUNK {i+1}/{len(chunks)}]\n\n{ch[:100000]}"
        partials.append(text_chat(prompt))

    combined = "\n\n".join(partials)
    prompt = f"{base_instr}\n\nCombine these partial summaries into one authoritative summary and bullets.\n\n{combined}"
    final = text_chat(prompt)

    # Critic pass
    critic_prompt = CRITIC_PROMPT.read_text() + f"\n\nSUMMARY:\n{final}\n\nPARTIALS:\n{combined}"
    critic = json_chat(critic_prompt, schema_hint="CriticReport")
    quality_score = float(critic.get("quality_score", 0.8))
    flags = critic.get("flags", [])
    issues = critic.get("issues", [])

    # Extract tasks JSON
    tprompt = TASKS_PROMPT.read_text() + f"\n\nSUMMARY_AND_BULLETS:\n{final}"
    extracted = json_chat(tprompt, schema_hint="ExtractedTasks")
    et = ExtractedTasks.model_validate(extracted)

    summary = {"text": et.summary, "bullets": et.bullets, "quality_score": quality_score, "flags": flags, "issues": issues}
    tasks = []
    for t in et.tasks:
        tasks.append({
            "title": t.title,
            "description": t.description,
            "priority": t.priority,
            "due": t.due,
            "status": "REVIEW",
            "confidence": t.confidence if t.confidence is not None else 0.5,
            "acceptance": [a.model_dump() for a in (t.acceptance or [])],
        })

    # Add acceptance checks if missing
    for t in tasks:
        if not t.get("acceptance"):
            inferred = infer_acceptance(t)
            if inferred:
                t["acceptance"] = inferred

    # Persist summary + tasks and get summary id
    sid = neo.add_summary_and_tasks(conversation_id, summary, tasks)

    # Ground bullets to transcript spans and link as EVIDENCE
    joined = "".join(texts)
    offsets = []
    total = 0
    for uid, txt in utts:
        offsets.append((uid, total, total+len(txt)))
        total += len(txt)
    evidences = []
    grounded = ground_bullets(summary.get("bullets", []), joined)
    for ev in grounded:
        usel = None
        for uid, a, b in offsets:
            if a <= ev["char_start"] < b:
                usel = uid
                break
        if usel:
            ev_rec = {**ev, "utterance_id": usel}
            evidences.append(ev_rec)
    if evidences:
        neo.add_evidence(sid, evidences)
    logger.info(f"Summarized {conversation_id}: {len(tasks)} tasks (status=REVIEW), grounded {len(evidences)} evidences")

def summarize_since_days(neo: Neo4jClient, days: int = 7):
    with neo.driver.session() as s:
        res = s.run("MATCH (c:Conversation) WHERE coalesce(c.created_at,0) > timestamp() - $ms RETURN c.id", {"ms": days * 86400000})
        ids = [r[0] for r in res]
    for cid in ids:
        summarize_conversation(neo, cid)
