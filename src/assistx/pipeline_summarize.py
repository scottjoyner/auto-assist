
from __future__ import annotations
from .neo4j_client import Neo4jClient
from .ollama_llm import text_chat, json_chat
from .schemas import ExtractedTasks, TaskModel
from pathlib import Path
from typing import List
from .logging_utils import get_logger

logger = get_logger()

SUMMARIZE_PROMPT = Path(__file__).with_suffix("").parent / "prompts" / "summarize.md"
TASKS_PROMPT = Path(__file__).with_suffix("").parent / "prompts" / "tasks.md"

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

def summarize_conversation(neo: Neo4jClient, conversation_id: str):
    # Pull utterances
    with neo.driver.session() as s:
        res = s.run("MATCH (c:Conversation{id:$id})-[:HAS_UTTERANCE]->(u:Utterance) RETURN u.text ORDER BY u.started_at, u.id", {"id": conversation_id})
        texts = [r[0] for r in res]
    if not texts:
        return

    base_instr = SUMMARIZE_PROMPT.read_text()
    chunks = chunk_texts(texts)

    partials = []
    for i, ch in enumerate(chunks):
        prompt = f"{base_instr}\n\n[CHUNK {i+1}/{len(chunks)}]\n\n{ch[:100000]}"
        partials.append(text_chat(prompt))

    combined = "\n\n".join(partials)
    prompt = f"{base_instr}\n\nCombine these partial summaries into one authoritative summary and bullets.\n\n{combined}"
    final = text_chat(prompt)

    # Extract tasks JSON with validation & light retries (handled in llm wrapper)
    tprompt = TASKS_PROMPT.read_text() + f"\n\nSUMMARY_AND_BULLETS:\n{final}"
    extracted = json_chat(tprompt, schema_hint="ExtractedTasks")

    # Validate with Pydantic (raise if invalid -> outer retry already in llm wrappers)
    et = ExtractedTasks.model_validate(extracted)

    summary = {
        "text": et.summary,
        "bullets": et.bullets,
        "quality_score": 1.0,
    }
    tasks = []
    for t in et.tasks:
        tasks.append({
            "title": t.title,
            "description": t.description,
            "priority": t.priority,
            "due": t.due,
            "status": "REVIEW",  # REVIEW gate
            "confidence": t.confidence if t.confidence is not None else 0.5,
        })

    neo.add_summary_and_tasks(conversation_id, summary, tasks)
    logger.info(f"Summarized {conversation_id}: {len(tasks)} tasks (status=REVIEW)")

def summarize_since_days(neo: Neo4jClient, days: int = 7):
    with neo.driver.session() as s:
        res = s.run(
            "MATCH (c:Conversation) WHERE coalesce(c.created_at,0) > timestamp() - $ms RETURN c.id",
            {"ms": days * 86400000},
        )
        ids = [r[0] for r in res]
    for cid in ids:
        summarize_conversation(neo, cid)
