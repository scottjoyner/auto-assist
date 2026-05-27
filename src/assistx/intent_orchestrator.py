from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

import redis as redis_module
from rq import get_current_job

from .neo4j_client import Neo4jClient
from .paperclip_client import PaperclipClient
from .queue import get_q
from .intent_classifier import (
    CLASSIFICATION_TASK,
    CLASSIFICATION_CANCEL,
    CLASSIFICATION_QUERY,
    CLASSIFICATION_MEMORY,
    CLASSIFICATION_UNKNOWN,
)
from . import ollama_llm

logger = logging.getLogger(__name__)

ORCHESTRATOR_INTERVAL_SECONDS = int(os.getenv("ORCHESTRATOR_INTERVAL", "15"))
INTENTS_PER_CYCLE = int(os.getenv("ORCHESTRATOR_INTENTS_PER_CYCLE", "3"))
PAPERCLIP_AGENT_ID = os.getenv("PAPERCLIP_AGENT_ID", "Hermes Agent")


def _get_paperclip_client() -> Optional[PaperclipClient]:
    try:
        return PaperclipClient()
    except ValueError:
        return None

COMPLEXITY_GATE_PROMPT = """You are a task complexity analyzer. Given a user's intent, determine if it's SIMPLE or COMPLEX.

SIMPLE means: a single, well-defined action that can be completed as one task (e.g., "send an email", "check the weather", "remind me to buy milk", "find a file").

COMPLEX means: requires research, planning, multiple steps, or involves building something substantial (e.g., "build a dashboard", "create a project plan", "research and compare solutions", "design an architecture", "write a design doc", anything with multiple deliverables or subtasks).

Respond with JSON: {"complexity": "simple"|"complex", "reason": "short explanation"}

Intent: {text}"""

BREAKDOWN_PROMPT = """You are a project planner. Break down the following intent into a structured deliverable plan.

Output JSON with this exact structure:
{{
  "summary": "one-line summary of the deliverable",
  "requirements": [
    "requirement 1",
    "requirement 2"
  ],
  "epics": [
    {{
      "title": "epic title",
      "description": "epic description with what needs to be done",
      "stories": [
        {{
          "title": "story title",
          "description": "detailed description of the story",
          "tasks": [
            {{
              "title": "task title",
              "description": "detailed task description with all context needed to complete it in one session",
              "kind": "research|code|design|analysis|documentation",
              "required_capabilities": ["terminal", "research", "code"],
              "acceptance_criteria": ["criterion 1", "criterion 2"]
            }}
          ]
        }}
      ]
    }}
  ]
}}

Rules:
- Each task must be self-contained with enough context to complete within ~90 LLM turns
- Required capabilities should be realistic: use "terminal" for most, add "research" for investigation tasks, add "code" for implementation tasks
- Each epic represents a major feature area
- Each story represents a user-visible feature or milestone
- Each task is a single unit of work completable in one session

Intent: {text}"""


def schedule_intent_orchestrator() -> None:
    r = redis_module.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    lock_key = "assistx:intent_orchestrator:scheduled"
    if r.setnx(lock_key, "1"):
        r.expire(lock_key, 60)
        get_q().enqueue_in(timedelta(seconds=5), process_intents_job)
        logger.info("Intent orchestrator scheduled (first run in 5s)")
    else:
        logger.debug("Intent orchestrator already scheduled")


def process_intents_job() -> Dict[str, Any]:
    neo = Neo4jClient()
    processed = 0
    errors = 0

    try:
        intents = neo.get_unprocessed_intents(
            limit=INTENTS_PER_CYCLE,
            classifications=[
                CLASSIFICATION_TASK,
                CLASSIFICATION_CANCEL,
                CLASSIFICATION_QUERY,
                CLASSIFICATION_MEMORY,
                CLASSIFICATION_UNKNOWN,
            ],
        )

        for intent in intents:
            try:
                _process_intent(neo, intent)
                processed += 1
            except Exception as e:
                logger.exception("Failed to process intent %s: %s", intent.get("id"), e)
                errors += 1

        result = {
            "intents_fetched": len(intents),
            "intents_processed": processed,
            "errors": errors,
        }
        if intents:
            logger.info("Intent orchestrator: %s", result)
        return result
    except Exception as e:
        logger.exception("Intent orchestrator cycle failed: %s", e)
        raise
    finally:
        neo.close()
        _reschedule()


def _process_intent(neo: Neo4jClient, intent: Dict[str, Any]) -> None:
    intent_id = intent.get("id")
    text = intent.get("text", "").strip()
    classification = intent.get("classification") or CLASSIFICATION_UNKNOWN
    policy_action = _intent_policy_action(intent)

    if not text:
        neo.mark_intent_orchestrated(intent_id)
        return

    if classification == CLASSIFICATION_CANCEL:
        if policy_action == "review_cancel":
            _queue_intent_review(neo, intent, policy_action, "Cancellation intent below confidence threshold")
        else:
            _handle_cancel(neo, intent)
    elif classification == CLASSIFICATION_QUERY:
        _handle_query(neo, intent)
    elif classification == CLASSIFICATION_MEMORY:
        _handle_memory(intent)
    elif classification == CLASSIFICATION_TASK:
        if policy_action in {"review_dispatch", "needs_clarification"}:
            _queue_intent_review(neo, intent, policy_action, "Task intent requires operator triage")
        else:
            _handle_task(neo, intent)
    else:
        if policy_action == "auto_dispatch_eligible":
            _handle_task(neo, intent)
        else:
            _queue_intent_review(neo, intent, policy_action, "Unknown intent classification requires review")

    neo.mark_intent_orchestrated(intent_id)


def _intent_policy_action(intent: Dict[str, Any]) -> str:
    direct = (intent.get("policy_action") or "").strip()
    if direct:
        return direct
    metadata_json = intent.get("metadata_json")
    if isinstance(metadata_json, str) and metadata_json:
        try:
            parsed = json.loads(metadata_json)
            policy = (parsed.get("policy_action") or "").strip()
            if policy:
                return policy
        except Exception:
            pass
    return "needs_clarification"


def _queue_intent_review(
    neo: Neo4jClient,
    intent: Dict[str, Any],
    policy_action: str,
    reason: str,
) -> None:
    intent_id = intent["id"]
    text = intent.get("text", "")
    title = f"Review intent: {text[:96]}" if text else "Review intent"
    ticket_id = neo.upsert_ticket(
        title=title,
        ticket_type="chore",
        status="REVIEW",
        kind="intent_review",
        payload={
            "source_intent": intent_id,
            "source_text": text,
            "classification": intent.get("classification"),
            "policy_action": policy_action,
            "reason": reason,
        },
        idempotency_key=f"intent-review:{intent_id}",
    )
    with neo._session() as s:
        s.run(
            "MATCH (i:Intent {id:$iid}), (t:Task {id:$tid}) "
            "MERGE (i)-[:CREATED_TASK]->(t)",
            {"iid": intent_id, "tid": ticket_id},
        ).consume()
    logger.info("Queued intent %s for review as ticket %s (%s)", intent_id, ticket_id, policy_action)


def _handle_cancel(neo: Neo4jClient, intent: Dict[str, Any]) -> None:
    intent_id = intent["id"]
    text = intent.get("text", "")
    with neo._session() as s:
        pending = s.run(
            """
            MATCH (i:Intent {id:$id})-[:CREATED_TASK]->(t:Task)
            WHERE t.status IN ['READY','CLAIMED','RUNNING']
            SET t.status='CANCELLED',
                t.cancelled_reason=$reason,
                t.updated_at=datetime(),
                t.updated_at_ts=timestamp()
            RETURN t.id AS task_id, t.status AS old_status
            """,
            {"id": intent_id, "reason": f"Cancelled by intent: {text[:200]}"},
        ).data()
        if pending:
            logger.info("Cancelled %d tasks for intent %s", len(pending), intent_id)


def _handle_query(neo: Neo4jClient, intent: Dict[str, Any]) -> None:
    from .jobs import ask_question_job
    text = intent.get("text", "")
    answer_id = uuid.uuid4().hex
    get_q().enqueue(ask_question_job, answer_id, text)
    logger.info("Enqueued query job %s for intent %s", answer_id, intent["id"])


def _handle_memory(intent: Dict[str, Any]) -> None:
    logger.debug("Memory intent %s requires no orchestration task", intent.get("id"))


def _handle_task(neo: Neo4jClient, intent: Dict[str, Any]) -> None:
    text = intent.get("text", "")
    intent_id = intent["id"]

    with neo._session() as s:
        existing = s.run(
            "MATCH (i:Intent {id:$id})-[:CREATED_TASK]->(t:Task) RETURN t.id AS id, t.status AS status",
            {"id": intent_id},
        ).data()

    if existing:
        logger.debug("Intent %s already has %d linked task(s), skipping creation", intent_id, len(existing))
        for task in existing:
            logger.info("  Task %s status=%s", task["id"], task["status"])
        return

    complexity = _complexity_gate(text)

    if complexity == "simple":
        _create_simple_task(neo, intent)
    else:
        _create_complex_deliverable(neo, intent)


def _complexity_gate(text: str) -> str:
    try:
        prompt = COMPLEXITY_GATE_PROMPT.format(text=text)
        result = ollama_llm.json_chat(prompt, temperature=0.1)
        complexity = result.get("complexity", "complex")
        logger.debug("Complexity gate: %s (reason: %s)", complexity, result.get("reason", ""))
        return complexity
    except Exception as e:
        logger.warning("Complexity gate failed, defaulting to simple: %s", e)
        return "simple"


def _create_simple_task(neo: Neo4jClient, intent: Dict[str, Any]) -> None:
    text = intent.get("text", "")
    intent_id = intent["id"]
    title = (text[:120] + "...") if len(text) > 120 else text

    result = neo.create_task_with_context(
        title=title,
        task_type="task",
        kind="capture",
        required_capabilities=["terminal"],
        payload={"source_intent": intent_id, "source_text": text},
        context_query=text,
        context_sources=["memory", "knowledge"],
        auto_dispatch=True,
        paperclip_client=_get_paperclip_client(),
        paperclip_agent_id=PAPERCLIP_AGENT_ID,
    )

    with neo._session() as s:
        s.run(
            "MATCH (i:Intent {id:$iid}), (t:Task {id:$tid}) "
            "MERGE (i)-[:CREATED_TASK]->(t)",
            {"iid": intent_id, "tid": result["task_id"]},
        ).consume()

    logger.info(
        "Simple task %s created for intent %s, dispatch %s, device %s",
        result["task_id"], intent_id, result.get("dispatch_id"), result.get("target_device_id"),
    )


def _create_complex_deliverable(neo: Neo4jClient, intent: Dict[str, Any]) -> None:
    text = intent.get("text", "")
    intent_id = intent["id"]

    breakdown = _breakdown_intent(text)
    if not breakdown:
        logger.warning("Breakdown failed for intent %s, falling back to simple task", intent_id)
        _create_simple_task(neo, intent)
        return

    summary = breakdown.get("summary", text[:120])
    requirements = breakdown.get("requirements", [])
    epics = breakdown.get("epics", [])

    deliverable_id = neo.upsert_ticket(
        title=summary[:120],
        ticket_type="deliverable",
        status="READY",
        kind="orchestrated",
        payload={"source_intent": intent_id, "source_text": text},
    )

    with neo._session() as s:
        s.run(
            "MATCH (i:Intent {id:$iid}), (d:Task {id:$did}) "
            "MERGE (i)-[:CREATED_TASK]->(d)",
            {"iid": intent_id, "did": deliverable_id},
        ).consume()

    for req_text in requirements:
        neo.upsert_requirement(text=req_text, epic_id=deliverable_id, intent_id=intent_id)

    for epic_spec in epics:
        epic_title = epic_spec.get("title", "Epic")[:120]
        epic_desc = epic_spec.get("description", "")
        stories = epic_spec.get("stories", [])

        epic_id = neo.upsert_ticket(
            title=epic_title,
            ticket_type="epic",
            status="READY",
            kind="orchestrated",
            parent_id=deliverable_id,
            payload={"description": epic_desc, "source_intent": intent_id},
        )

        for req_text in epic_spec.get("requirements", []):
            neo.upsert_requirement(text=req_text, epic_id=epic_id, intent_id=intent_id)

        for story_spec in stories:
            story_title = story_spec.get("title", "Story")[:120]
            story_desc = story_spec.get("description", "")
            tasks = story_spec.get("tasks", [])

            story_id = neo.upsert_ticket(
                title=story_title,
                ticket_type="story",
                status="READY",
                kind="orchestrated",
                parent_id=epic_id,
                payload={"description": story_desc, "source_intent": intent_id, "epic_title": epic_title},
            )

            for task_spec in tasks:
                task_title = task_spec.get("title", "Task")[:120]
                task_desc = task_spec.get("description", "")
                task_kind = task_spec.get("kind", "task")
                caps = task_spec.get("required_capabilities", ["terminal"])
                acceptance = task_spec.get("acceptance_criteria", [])

                context_text = (
                    f"Epic: {epic_title}\n"
                    f"Story: {story_title}\n"
                    f"Task: {task_title}\n\n"
                    f"Description: {task_desc}\n\n"
                    f"Acceptance criteria:\n" + "\n".join(f"- {a}" for a in acceptance) + "\n\n"
                    f"Original request: {text[:500]}"
                )

                result = neo.create_task_with_context(
                    title=task_title,
                    task_type="task",
                    kind=task_kind,
                    parent_id=story_id,
                    required_capabilities=caps,
                    payload={
                        "description": task_desc,
                        "acceptance_criteria": acceptance,
                        "source_intent": intent_id,
                        "epic_title": epic_title,
                        "story_title": story_title,
                    },
                    context_query=context_text,
                    context_sources=["memory", "knowledge", "orchestration"],
                    auto_dispatch=True,
                    paperclip_client=_get_paperclip_client(),
                    paperclip_agent_id=PAPERCLIP_AGENT_ID,
                )

                logger.debug(
                    "Task %s created under story %s, dispatch %s -> device %s",
                    result["task_id"], story_id,
                    result.get("dispatch_id"), result.get("target_device_id"),
                )

    logger.info(
        "Complex deliverable created for intent %s: deliverable=%s, epics=%d",
        intent_id, deliverable_id, len(epics),
    )


def _breakdown_intent(text: str) -> Optional[Dict[str, Any]]:
    try:
        prompt = BREAKDOWN_PROMPT.format(text=text)
        result = ollama_llm.json_chat(prompt, temperature=0.3)
        if not result.get("epics"):
            logger.warning("Breakdown returned no epics")
            return None
        return result
    except Exception as e:
        logger.error("Breakdown LLM call failed: %s", e)
        return None


def _reschedule() -> None:
    job = get_current_job()
    if job is not None:
        try:
            get_q().enqueue_in(timedelta(seconds=ORCHESTRATOR_INTERVAL_SECONDS), process_intents_job)
        except Exception as e:
            logger.error("Failed to reschedule intent orchestrator: %s", e)
