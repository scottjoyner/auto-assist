from __future__ import annotations
from typing import Any, Dict, Iterable, List, Optional
import os
import uuid
from neo4j import GraphDatabase, Driver, Session


class Neo4jClient:
    """
    Unified Neo4j client supporting:
      • v1: Conversation / Utterance / Summary / Task / AgentRun / ToolCall / Artifact
      • v2: Transcription / Segment

    Initialization order of precedence:
      1) Explicit uri/user/password/database args
      2) config.settings (either .config.settings or settings) if available
      3) Env vars: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
    ):
        # Try explicit args → config.settings → environment
        cfg = self._load_settings_fallback()

        self.uri = uri or cfg.get("uri")
        self.user = user or cfg.get("user")
        self.password = password or cfg.get("password")
        self.database = database or cfg.get("database")  # may be None (use default db)

        if not (self.uri and self.user and self.password):
            raise ValueError(
                "Neo4jClient requires uri, user, and password (via args, config.settings, or env)."
            )

        self.driver: Driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    # ---------- setup / teardown ----------

    def close(self) -> None:
        self.driver.close()

    def _session(self) -> Session:
        # database may be None → Neo4j routes to default
        return self.driver.session(database=self.database) if self.database else self.driver.session()

    @staticmethod
    def _load_settings_fallback() -> Dict[str, Optional[str]]:
        """
        Attempt to load from modules:
          1) .config.settings (package-local)
          2) config.settings (top-level)
        Fallback to env vars.
        """
        # Defaults from env
        cfg = {
            "uri": os.getenv("NEO4J_URI"),
            "user": os.getenv("NEO4J_USER"),
            "password": os.getenv("NEO4J_PASSWORD"),
            "database": os.getenv("NEO4J_DATABASE"),
        }

        # Try relative import first (package layout like assistx.config)
        for modpath in (".config", "config"):
            try:
                mod = __import__(modpath, fromlist=["settings"])
                settings = getattr(mod, "settings", None)
                if settings:
                    cfg["uri"] = getattr(settings, "neo4j_uri", cfg["uri"])
                    cfg["user"] = getattr(settings, "neo4j_user", cfg["user"])
                    cfg["password"] = getattr(settings, "neo4j_password", cfg["password"])
                    # Optional db name if you support it in settings
                    cfg["database"] = getattr(settings, "neo4j_database", cfg["database"])
                    break
            except Exception:
                # Ignore & continue to next source
                pass

        return cfg

    # ---------- schema ----------

    def ensure_schema(self):
        cypher = [
            # Uniqueness
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Conversation) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (u:Utterance)   REQUIRE u.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Summary)     REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Task)        REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (r:AgentRun)    REQUIRE r.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (k:ToolCall)    REQUIRE k.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Artifact)    REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (tr:Transcription) REQUIRE tr.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (sg:Segment)       REQUIRE sg.id IS UNIQUE",

            # Helpful indexes
            "CREATE INDEX IF NOT EXISTS FOR (t:Task)            ON (t.status)",
            "CREATE INDEX IF NOT EXISTS FOR (t:Task)            ON (t.kind)",
            "CREATE INDEX IF NOT EXISTS FOR (t:Task)            ON (t.created_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (tr:Transcription)  ON (tr.key)",
            "CREATE INDEX IF NOT EXISTS FOR (tr:Transcription)  ON (tr.created_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (r:AgentRun)        ON (r.started_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (k:ToolCall)        ON (k.started_at_ts)",
        ]
        with self.driver.session() as s:
            for q in cypher:
                s.run(q)

    # Back-compat with v2's method name
    def ensure_indexes(self) -> None:
        self.ensure_schema()

    # ---------- v1: conversations / tasks / runs / tools / artifacts ----------

    def upsert_conversation(self, title: str, source: str) -> str:
        """
        Merge conversation by (title, source). On first create, assigns a UUID id.
        Returns the conversation id.
        """
        q = (
            "MERGE (c:Conversation {title:$title, source:$source}) "
            "ON CREATE SET c.id = randomUUID(), "
            "              c.created_at = datetime(), "
            "              c.created_at_ts = timestamp() "
            "ON MATCH  SET c.updated_at = datetime() "
            "RETURN c.id as id"
        )
        with self._session() as s:
            rec = s.run(q, {"title": title, "source": source}).single()
            return rec["id"]

    def add_utterances(self, conversation_id: str, rows: Iterable[Dict[str, Any]]) -> None:
        """
        Add/merge utterances and attach them to the conversation.
        Each row may contain arbitrary properties; if id is missing, a UUID is generated.
        """
        q = (
            "MERGE (u:Utterance {id:$id}) "
            "SET u += $props "
            "FOREACH (_ IN CASE WHEN NOT EXISTS(u.created_at) THEN [1] ELSE [] END | "
            "  SET u.created_at = datetime(), u.created_at_ts = timestamp()) "
            "SET u.updated_at = datetime() "
            "WITH u "
            "MATCH (c:Conversation {id:$cid}) "
            "MERGE (c)-[:HAS_UTTERANCE]->(u)"
        )
        with self._session() as s:
            for r in rows:
                if not r.get("id"):
                    r["id"] = str(uuid.uuid4())
                props = {**r, "conversation_id": conversation_id}
                s.run(q, {"id": r["id"], "props": props, "cid": conversation_id})

    def add_summary_and_tasks(self, conversation_id: str, summary: Dict[str, Any], tasks: Iterable[Dict[str, Any]]):
        with self.driver.session() as s:
            sr = s.run(
                "CREATE (m:Summary {id:randomUUID()}) "
                "SET m += $sprops, m.created_at = timestamp(), m.created_at_ts = timestamp() "
                "WITH m MATCH (c:Conversation{id:$cid}) MERGE (c)-[:HAS_SUMMARY]->(m) RETURN m.id as id",
                {"sprops": {**summary, "conversation_id": conversation_id}, "cid": conversation_id},
            ).single()
            sid = sr["id"]
            for t in tasks:
                tprops = {**t, "conversation_id": conversation_id}
                s.run(
                    "CREATE (t:Task {id:randomUUID()}) "
                    "SET t += $tprops, t.created_at = timestamp(), t.created_at_ts = timestamp() "
                    "WITH t MATCH (m:Summary{id:$sid}) MERGE (m)-[:GENERATED_TASK]->(t)",
                    {"tprops": tprops, "sid": sid},
                )
            return sid

    def get_ready_tasks(self, limit: int = 10) -> List[Dict[str, Any]]:
        q = (
            "MATCH (t:Task {status:'READY'}) "
            "RETURN t ORDER BY t.created_at_ts ASC LIMIT $limit"
        )
        with self._session() as s:
            res = s.run(q, {"limit": limit})
            return [dict(r["t"]) for r in res]

    def get_review_tasks(self, limit: int = 25) -> List[Dict[str, Any]]:
        q = (
            "MATCH (t:Task {status:'REVIEW'}) "
            "RETURN t ORDER BY t.created_at_ts ASC LIMIT $limit"
        )
        with self._session() as s:
            res = s.run(q, {"limit": limit})
            return [dict(r["t"]) for r in res]

    def update_task_status(self, task_id: str, status: str):
        with self.driver.session() as s:
            s.run("MATCH (t:Task{id:$id}) SET t.status=$st, t.updated_at_ts = timestamp()", {"id": task_id, "st": status})

    def create_run(self, task_id: str, agent: str, model: str, manifest: Dict[str, Any]):
        with self.driver.session() as s:
            rec = s.run(
                "MATCH (t:Task{id:$tid}) "
                "CREATE (r:AgentRun {id:randomUUID(), task_id:$tid, agent:$agent, model:$model, status:'RUNNING', "
                " started_at:timestamp(), started_at_ts:timestamp(), manifest_json:$manifest}) "
                "MERGE (t)-[:EXECUTED_BY]->(r) RETURN r.id as id",
                {"tid": task_id, "agent": agent, "model": model, "manifest": manifest},
            ).single()
            return rec["id"]

    def complete_run(self, run_id: str, status: str):
        with self.driver.session() as s:
            s.run("MATCH (r:AgentRun{id:$id}) SET r.status=$st, r.ended_at=timestamp(), r.ended_at_ts=timestamp()", {"id": run_id, "st": status})

    def log_tool_call(self, run_id: str, tool: str, input_json: Dict[str, Any], output_json: Dict[str, Any] | None, ok: bool):
        with self.driver.session() as s:
            s.run(
                "MATCH (r:AgentRun{id:$rid}) "
                "CREATE (k:ToolCall {id:randomUUID(), run_id:$rid, tool:$tool, input_json:$in, output_json:$out, ok:$ok, "
                " started_at:timestamp(), started_at_ts:timestamp(), ended_at:timestamp(), ended_at_ts:timestamp()}) "
                "MERGE (r)-[:USED_TOOL]->(k)",
                {"rid": run_id, "tool": tool, "in": input_json, "out": output_json, "ok": ok},
            )

    def log_artifact(self, run_id: str, kind: str, path: str, sha256: str | None):
        with self.driver.session() as s:
            s.run(
                "MATCH (r:AgentRun{id:$rid}) "
                "CREATE (a:Artifact {id:randomUUID(), run_id:$rid, kind:$k, path:$p, sha256:$h, created_at:timestamp(), created_at_ts:timestamp()}) "
                "MERGE (r)-[:PRODUCED]->(a)",
                {"rid": run_id, "k": kind, "p": path, "h": sha256},
            )
    def add_evidence(self, summary_id: str, evidences: Iterable[Dict[str, Any]]) -> None:
        q = (
            "MATCH (s:Summary {id:$sid}), (u:Utterance {id:$uid}) "
            "MERGE (s)-[:EVIDENCE {bullet_index:$bi, quote:$q, char_start:$cs, char_end:$ce, rationale:$ra}] -> (u)"
        )
        with self._session() as s:
            for ev in evidences:
                s.run(
                    q,
                    {
                        "sid": summary_id,
                        "uid": ev.get("utterance_id"),
                        "bi": ev.get("bullet_index"),
                        "q": ev.get("quote"),
                        "cs": ev.get("char_start"),
                        "ce": ev.get("char_end"),
                        "ra": ev.get("rationale", ""),
                    },
                )

    # ---------- v2: transcription / segment ----------

    def ingest_transcription(self, t: Dict[str, Any], segments: List[Dict[str, Any]]) -> None:
        """
        Upsert a Transcription node and its Segment children.

        Expected 't' fields (flexible):
          - id (required)
          - key, text, source_json, source_rttm, embedding (optional)

        Each segment item expected:
          - id (required), idx, start, end, text, tokens_count (optional)
        """
        cypher = """
        MERGE (tr:Transcription {id:$tid})
          ON CREATE SET tr.key=$key, tr.text=$text, tr.source_json=$source_json, tr.source_rttm=$source_rttm,
                        tr.embedding=$embedding, tr.created_at=datetime(), tr.created_at_ts=timestamp()
          ON MATCH  SET tr.text=$text, tr.source_json=$source_json, tr.source_rttm=$source_rttm,
                        tr.updated_at=datetime()
        WITH tr
        UNWIND $segments AS seg
          MERGE (s:Segment {id: seg.id})
            ON CREATE SET s.idx=seg.idx, s.start=seg.start, s.end=seg.end, s.text=seg.text,
                          s.tokens_count=seg.tokens_count, s.created_at=datetime(), s.created_at_ts=timestamp()
            ON MATCH  SET s.idx=seg.idx, s.start=seg.start, s.end=seg.end, s.text=seg.text,
                          s.tokens_count=seg.tokens_count, s.updated_at=datetime()
          MERGE (tr)-[:HAS_SEGMENT]->(s)
        """
        with self._session() as s:
            s.run(
                cypher,
                tid=t["id"],
                key=t.get("key"),
                text=t.get("text", ""),
                source_json=t.get("source_json"),
                source_rttm=t.get("source_rttm"),
                embedding=t.get("embedding"),
                segments=segments or [],
            )

    # alias for back-compat / readability
    upsert_transcription = ingest_transcription


# Back-compat with code that used `Neo(...)`
Neo = Neo4jClient
