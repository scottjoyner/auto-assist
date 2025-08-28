
from __future__ import annotations
from neo4j import GraphDatabase
from typing import Iterable, Dict, Any
from .config import settings
import uuid

class Neo4jClient:
    def __init__(self):
        self.driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))

    def close(self):
        self.driver.close()

    def ensure_schema(self):
        cypher = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Conversation) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (u:Utterance) REQUIRE u.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Summary) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Task) REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (r:AgentRun) REQUIRE r.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (k:ToolCall) REQUIRE k.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Artifact) REQUIRE a.id IS UNIQUE",
        ]
        with self.driver.session() as s:
            for q in cypher:
                s.run(q)

    def upsert_conversation(self, title: str, source: str) -> str:
        with self.driver.session() as s:
            rec = s.run(
                "MERGE (c:Conversation {title:$title, source:$source}) "
                "ON CREATE SET c.id=coalesce(c.id, randomUUID()), c.created_at=timestamp() "
                "RETURN c.id as id",
                {"title": title, "source": source},
            ).single()
            return rec["id"]

    def add_utterances(self, conversation_id: str, rows: Iterable[Dict[str, Any]]):
        with self.driver.session() as s:
            for r in rows:
                s.run(
                    "MERGE (u:Utterance {id:$id}) SET u += $props "
                    "WITH u MATCH (c:Conversation{id:$cid}) MERGE (c)-[:HAS_UTTERANCE]->(u)",
                    {"id": r.get("id") or str(uuid.uuid4()), "props": {**r, "conversation_id": conversation_id}, "cid": conversation_id},
                )

    def add_summary_and_tasks(self, conversation_id: str, summary: Dict[str, Any], tasks: Iterable[Dict[str, Any]]):
        with self.driver.session() as s:
            sr = s.run(
                "CREATE (m:Summary {id:randomUUID()}) SET m += $sprops "
                "WITH m MATCH (c:Conversation{id:$cid}) MERGE (c)-[:HAS_SUMMARY]->(m) RETURN m.id as id",
                {"sprops": {**summary, "conversation_id": conversation_id, "created_at": "timestamp()"}, "cid": conversation_id},
            ).single()
            sid = sr["id"]
            for t in tasks:
                # default status REVIEW
                tprops = {**t, "conversation_id": conversation_id, "status": t.get("status") or "REVIEW", "created_at": "timestamp()"}
                s.run(
                    "CREATE (t:Task {id:randomUUID()}) SET t += $tprops "
                    "WITH t MATCH (m:Summary{id:$sid}) MERGE (m)-[:GENERATED_TASK]->(t) RETURN t.id as id",
                    {"tprops": tprops, "sid": sid},
                )

    def get_ready_tasks(self, limit: int = 10):
        with self.driver.session() as s:
            res = s.run("MATCH (t:Task {status:'READY'}) RETURN t ORDER BY t.created_at LIMIT $limit", {"limit": limit})
            return [dict(r[0]) for r in res]

    def get_review_tasks(self, limit: int = 25):
        with self.driver.session() as s:
            res = s.run("MATCH (t:Task {status:'REVIEW'}) RETURN t ORDER BY t.created_at LIMIT $limit", {"limit": limit})
            return [dict(r[0]) for r in res]

    def update_task_status(self, task_id: str, status: str):
        with self.driver.session() as s:
            s.run("MATCH (t:Task{id:$id}) SET t.status=$st", {"id": task_id, "st": status})

    def create_run(self, task_id: str, agent: str, model: str, manifest: Dict[str, Any]):
        with self.driver.session() as s:
            rec = s.run(
                "MATCH (t:Task{id:$tid}) "
                "CREATE (r:AgentRun {id:randomUUID(), task_id:$tid, agent:$agent, model:$model, status:'RUNNING', started_at:timestamp(), manifest_json:$manifest}) "
                "MERGE (t)-[:EXECUTED_BY]->(r) RETURN r.id as id",
                {"tid": task_id, "agent": agent, "model": model, "manifest": manifest},
            ).single()
            return rec["id"]

    def complete_run(self, run_id: str, status: str):
        with self.driver.session() as s:
            s.run("MATCH (r:AgentRun{id:$id}) SET r.status=$st, r.ended_at=timestamp()", {"id": run_id, "st": status})

    def log_tool_call(self, run_id: str, tool: str, input_json: Dict[str, Any], output_json: Dict[str, Any] | None, ok: bool):
        with self.driver.session() as s:
            s.run(
                "MATCH (r:AgentRun{id:$rid}) "
                "CREATE (k:ToolCall {id:randomUUID(), run_id:$rid, tool:$tool, input_json:$in, output_json:$out, ok:$ok, started_at:timestamp(), ended_at:timestamp()}) "
                "MERGE (r)-[:USED_TOOL]->(k)",
                {"rid": run_id, "tool": tool, "in": input_json, "out": output_json, "ok": ok},
            )

    def log_artifact(self, run_id: str, kind: str, path: str, sha256: str | None):
        with self.driver.session() as s:
            s.run(
                "MATCH (r:AgentRun{id:$rid}) "
                "CREATE (a:Artifact {id:randomUUID(), run_id:$rid, kind:$k, path:$p, sha256:$h, created_at:timestamp()}) "
                "MERGE (r)-[:PRODUCED]->(a)",
                {"rid": run_id, "k": kind, "p": path, "h": sha256},
            )
