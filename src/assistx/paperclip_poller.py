from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .deps import load_get_current_job, load_redis_module

redis_module = load_redis_module()
get_current_job = load_get_current_job()

_redis = None
def _get_redis():
    global _redis
    if _redis is None:
        _redis = redis_module.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    return _redis

from .queue import get_q

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = int(os.getenv("PAPERCLIP_POLL_INTERVAL", "30"))

STATUS_EVENT_MAP = {
    "backlog": "issue_created",
    "todo": "issue_created",
    "in_progress": "run_started",
    "done": "run_completed",
    "cancelled": "run_completed",
    "closed": "run_completed",
}


def paperclip_poll_job() -> Dict[str, Any]:
    from .neo4j_client import Neo4jClient
    neo = Neo4jClient()
    pc = _get_paperclip()
    if not pc:
        logger.warning("Paperclip not configured, skipping poll")
        return {"error": "Paperclip not configured"}

    try:
        last_poll_ts = _get_last_poll_ts(neo)
        issues = pc.list_issues(limit=100)

        updated = 0
        for issue in issues:
            if _sync_issue(neo, pc, issue, last_poll_ts):
                updated += 1

        _set_last_poll_ts(neo)

        result = {
            "issues_fetched": len(issues),
            "issues_updated": updated,
            "last_poll_ts": datetime.now(timezone.utc).isoformat(),
        }
        logger.info("Paperclip poll: %s", result)
        return result
    except Exception as e:
        logger.exception("Paperclip poll failed: %s", e)
        raise
    finally:
        neo.close()
        _reschedule()


def _get_paperclip() -> Optional["PaperclipClient"]:
    from .paperclip_client import PaperclipClient
    try:
        return PaperclipClient()
    except ValueError:
        return None


def _get_last_poll_ts(neo: Neo4jClient) -> Optional[float]:
    with neo._session() as s:
        rec = s.run(
            "MATCH (c:Config {key:'paperclip_last_poll_ts'}) RETURN c.value AS val"
        ).single()
        if rec and rec.get("val"):
            try:
                return float(rec["val"])
            except (ValueError, TypeError):
                return None
    return None


def _set_last_poll_ts(neo: Neo4jClient) -> None:
    ts = int(time.time() * 1000)
    with neo._session() as s:
        s.run(
            "MERGE (c:Config {key:'paperclip_last_poll_ts'}) "
            "SET c.value=$val, c.updated_at=datetime(), c.updated_at_ts=timestamp()",
            {"val": str(ts)},
        ).consume()


def _sync_issue(
    neo: Neo4jClient,
    pc: PaperclipClient,
    issue: Dict[str, Any],
    last_poll_ts: Optional[float],
) -> bool:
    issue_id = issue.get("id")
    if not issue_id:
        return False

    issue_status = issue.get("status", "backlog")
    issue_updated = issue.get("updatedAt") or issue.get("updated_at")

    if issue_updated and last_poll_ts:
        issue_ts = _parse_paperclip_timestamp(issue_updated)
        if issue_ts and issue_ts <= last_poll_ts:
            return False

    with neo._session() as s:
        existing = s.run(
            "MATCH (d:Dispatch {paperclip_issue_id:$pid}) "
            "RETURN d.status AS status, d.updated_at_ts AS updated_ts",
            {"pid": issue_id},
        ).single()

        if existing:
            existing_status = existing.get("status")
            if existing_status == issue_status.upper() and issue_updated:
                existing_ts = existing.get("updated_ts")
                issue_ts = _parse_paperclip_timestamp(issue_updated)
                if existing_ts and issue_ts and issue_ts <= existing_ts:
                    return False

    event_type = STATUS_EVENT_MAP.get(issue_status.lower(), "status_changed")
    agent_id = issue.get("assigneeAgentId") or issue.get("agentId")
    run_id = (
        issue.get("executionRunId")
        or issue.get("currentRunId")
        or issue.get("checkoutRunId")
    )
    historical_run: Dict[str, Any] = {}
    if not run_id:
        historical_run = _select_historical_run(pc, issue_id)
        run_id = historical_run.get("runId") or historical_run.get("id")
        if run_id and historical_run.get("agentId"):
            agent_id = historical_run["agentId"]

    event_id = f"{issue_id}-{run_id or 'no-run'}-{issue_updated or issue_status}"

    run_detail: Dict[str, Any] = historical_run
    if run_id:
        if not run_detail:
            try:
                run_detail = pc.get_run(run_id)
            except Exception as e:
                logger.warning("Failed to fetch run %s: %s", run_id, e)
        if not agent_id:
            agent_id = run_detail.get("agentId")
        if _is_unscoped_timer_run(run_detail, issue_id):
            historical_run = _select_historical_run(pc, issue_id)
            historical_run_id = historical_run.get("runId") or historical_run.get("id")
            if historical_run_id and historical_run_id != run_id:
                run_id = historical_run_id
                run_detail = historical_run
                agent_id = historical_run.get("agentId") or agent_id
                event_id = f"{issue_id}-{run_id}-{issue_updated or issue_status}"

    payload = {"issue": issue, "run": run_detail}

    neo.ingest_paperclip_event(
        event_type=event_type,
        paperclip_issue_id=issue_id,
        paperclip_agent_id=agent_id,
        paperclip_run_id=run_id,
        event_id=event_id,
        payload=payload,
    )

    if run_id:
        _sync_run_output(neo, pc, run_id, issue_id)

    return True


def _select_historical_run(pc: PaperclipClient, issue_id: str) -> Dict[str, Any]:
    """Retain terminal run evidence after Paperclip clears live issue pointers."""
    try:
        runs = pc.list_runs(issue_id=issue_id, limit=20)
    except Exception as e:
        logger.debug("Could not fetch run history for issue %s: %s", issue_id, e)
        return {}
    if not runs:
        return {}

    issue_scoped = [run for run in runs if _is_issue_scoped_run(run, issue_id)]
    if issue_scoped:
        runs = issue_scoped

    # Paperclip can follow a successful worker run with a failed recovery or
    # disposition handoff. Preserve the successful work output; issue status
    # still records that the overall workflow remains blocked.
    for run in runs:
        if str(run.get("status", "")).lower() == "succeeded":
            return run
    return runs[0]


def _is_issue_scoped_run(run: Dict[str, Any], issue_id: str) -> bool:
    context = run.get("contextSnapshot") or {}
    return context.get("issueId") == issue_id or context.get("taskId") == issue_id


def _is_unscoped_timer_run(run: Dict[str, Any], issue_id: str) -> bool:
    context = run.get("contextSnapshot") or {}
    return context.get("wakeReason") == "heartbeat_timer" and not _is_issue_scoped_run(run, issue_id)


def _sync_run_output(neo: Neo4jClient, pc: PaperclipClient, run_id: str, issue_id: str) -> None:
    try:
        output = pc.get_run_output(run_id)
        if output:
            with neo._session() as s:
                s.run(
                    "MATCH (d:Dispatch {paperclip_issue_id:$pid}) "
                    "SET d.last_run_output=$out, d.updated_at=datetime(), d.updated_at_ts=timestamp()",
                    {"pid": issue_id, "out": output},
                ).consume()
    except Exception as e:
        logger.debug("Could not fetch output for run %s: %s", run_id, e)


def _parse_paperclip_timestamp(ts_str: str) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp() * 1000
    except (ValueError, TypeError):
        try:
            return float(ts_str)
        except (ValueError, TypeError):
            return None


def _reschedule() -> None:
    job = get_current_job()
    if job is not None:
        try:
            get_q().enqueue_in(timedelta(seconds=POLL_INTERVAL_SECONDS), paperclip_poll_job)
        except Exception as e:
            logger.error("Failed to reschedule paperclip poller: %s", e)


def schedule_paperclip_poller() -> None:
    r = _get_redis()
    lock_key = "assistx:paperclip_poller:scheduled"
    if r.setnx(lock_key, "1"):
        r.expire(lock_key, 60)
        get_q().enqueue_in(timedelta(seconds=5), paperclip_poll_job)
        logger.info("Paperclip poller scheduled (first run in 5s)")
    else:
        logger.debug("Paperclip poller already scheduled")
