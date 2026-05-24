from __future__ import annotations
from typing import Any, Dict, Iterable, List, Optional
import hashlib
import json
import os
import uuid
from neo4j import GraphDatabase, Driver, Session

EXECUTABLE_TASK_STATUSES = {"READY", "CLAIMED", "RUNNING", "DONE", "FAILED", "CANCELLED"}
TERMINAL_TASK_STATUSES = {"DONE", "FAILED", "CANCELLED"}


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
            "CREATE CONSTRAINT IF NOT EXISTS FOR (i:Intent)       REQUIRE i.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (p:ContextPacket) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (d:Dispatch)     REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:AgentSession)  REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (v:AgentDevice)   REQUIRE v.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (m:MemoryItem)    REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:SignalEvent)   REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:MediaCapture)  REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:MediaAsset)    REQUIRE a.path IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (r:Requirement)  REQUIRE r.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:EvaluationRun) REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:EvaluationSuite) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (f:DataFeedConnector) REQUIRE f.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (b:WorkflowBudget) REQUIRE b.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (w:WorkflowIncident) REQUIRE w.id IS UNIQUE",

            # Helpful indexes
            "CREATE INDEX IF NOT EXISTS FOR (t:Task)            ON (t.status)",
            "CREATE INDEX IF NOT EXISTS FOR (t:Task)            ON (t.kind)",
            "CREATE INDEX IF NOT EXISTS FOR (t:Task)            ON (t.ticket_type)",
            "CREATE INDEX IF NOT EXISTS FOR (t:Task)            ON (t.claimed_by)",
            "CREATE INDEX IF NOT EXISTS FOR (t:Task)            ON (t.created_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (tr:Transcription)  ON (tr.key)",
            "CREATE INDEX IF NOT EXISTS FOR (tr:Transcription)  ON (tr.created_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (r:AgentRun)        ON (r.started_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (k:ToolCall)        ON (k.started_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (i:Intent)          ON (i.source)",
            "CREATE INDEX IF NOT EXISTS FOR (i:Intent)          ON (i.created_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (i:Intent)          ON (i.idempotency_key)",
            "CREATE INDEX IF NOT EXISTS FOR (d:Dispatch)        ON (d.status)",
            "CREATE INDEX IF NOT EXISTS FOR (d:Dispatch)        ON (d.paperclip_issue_id)",
            "CREATE INDEX IF NOT EXISTS FOR (d:Dispatch)        ON (d.created_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (d:Dispatch)        ON (d.target_device_id)",
            "CREATE INDEX IF NOT EXISTS FOR (s:AgentSession)    ON (s.hermes_session_id)",
            "CREATE INDEX IF NOT EXISTS FOR (s:AgentSession)    ON (s.paperclip_agent_id)",
            "CREATE INDEX IF NOT EXISTS FOR (v:AgentDevice)     ON (v.hostname)",
            "CREATE INDEX IF NOT EXISTS FOR (v:AgentDevice)     ON (v.last_seen_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (m:MemoryItem)      ON (m.kind)",
            "CREATE INDEX IF NOT EXISTS FOR (m:MemoryItem)      ON (m.source)",
            "CREATE INDEX IF NOT EXISTS FOR (m:MemoryItem)      ON (m.updated_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (c:MediaCapture)    ON (c.session_id)",
            "CREATE INDEX IF NOT EXISTS FOR (c:MediaCapture)    ON (c.media_kind)",
            "CREATE INDEX IF NOT EXISTS FOR (p:ContextPacket)   ON (p.created_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (p:ContextPacket)   ON (p.query_hash)",
            "CREATE INDEX IF NOT EXISTS FOR (r:Requirement)    ON (r.source_intent_id)",
            "CREATE INDEX IF NOT EXISTS FOR (e:EvaluationRun)   ON (e.created_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (e:EvaluationRun)   ON (e.status)",
            "CREATE INDEX IF NOT EXISTS FOR (s:EvaluationSuite) ON (s.name)",
            "CREATE INDEX IF NOT EXISTS FOR (f:DataFeedConnector) ON (f.name)",
            "CREATE INDEX IF NOT EXISTS FOR (f:DataFeedConnector) ON (f.health_status)",
            "CREATE INDEX IF NOT EXISTS FOR (f:DataFeedConnector) ON (f.updated_at_ts)",
            "CREATE INDEX IF NOT EXISTS FOR (b:WorkflowBudget) ON (b.workflow_id)",
            "CREATE INDEX IF NOT EXISTS FOR (w:WorkflowIncident) ON (w.workflow_id)",
            "CREATE INDEX IF NOT EXISTS FOR (w:WorkflowIncident) ON (w.created_at_ts)",
        ]
        with self._session() as s:
            for q in cypher:
                s.run(q)

    # Back-compat with v2's method name
    def ensure_indexes(self) -> None:
        self.ensure_schema()

    def upsert_intent(
        self,
        source: str,
        text: str,
        idempotency_key: Optional[str] = None,
        client_ts: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        classification: Optional[str] = None,
        intent_outcome: Optional[str] = None,
        intent_confidence: Optional[float] = None,
    ) -> str:
        props = {
            "source": source,
            "text": text,
            "client_ts": client_ts,
            "metadata_json": json.dumps(metadata or {}),
            "classification": classification,
            "intent_outcome": intent_outcome,
            "intent_confidence": intent_confidence,
        }
        intent_id = uuid.uuid4().hex
        if idempotency_key:
            q = (
                "MERGE (i:Intent {idempotency_key:$idempotency_key}) "
                "ON CREATE SET i.id=$id, i.created_at=datetime(), i.created_at_ts=timestamp() "
                "SET i += $props, i.updated_at=datetime(), i.updated_at_ts=timestamp() "
                "RETURN i.id AS id"
            )
            with self._session() as s:
                rec = s.run(q, {"idempotency_key": idempotency_key, "id": intent_id, "props": props}).single()
                return rec["id"]

        q = (
            "CREATE (i:Intent {id:$id}) "
            "SET i += $props, i.created_at=datetime(), i.created_at_ts=timestamp(), "
            "    i.updated_at=datetime(), i.updated_at_ts=timestamp() "
            "RETURN i.id AS id"
        )
        with self._session() as s:
            rec = s.run(q, {"id": intent_id, "props": props}).single()
            return rec["id"]

    def create_context_packet(
        self,
        query: str,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        max_items: int = 20,
        include_sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        packet_id = uuid.uuid4().hex
        payload = {
            "id": packet_id,
            "query": query,
            "query_hash": hashlib.sha1(query.encode("utf-8")).hexdigest(),
            "max_items": max_items,
            "include_sources": include_sources or [],
        }
        with self._session() as s:
            q = (
                "CREATE (p:ContextPacket {id:$id}) "
                "SET p += $props, p.created_at = datetime(), p.created_at_ts = timestamp(), "
                "    p.updated_at = datetime(), p.updated_at_ts = timestamp() "
                "RETURN p.id AS id"
            )
            s.run(q, {"id": packet_id, "props": payload})
            if task_id:
                s.run(
                    "MATCH (t:Task {id:$tid}), (p:ContextPacket {id:$pid}) "
                    "MERGE (t)-[:USES_CONTEXT]->(p) "
                    "MERGE (p)-[r:REFERENCES {source:'orchestration'}]->(t) "
                    "SET r.source_type='Task', r.rank_source='current_task'",
                    {"tid": task_id, "pid": packet_id},
                )
            if session_id:
                s.run(
                    "MATCH (s:AgentSession {id:$sid}), (p:ContextPacket {id:$pid}) "
                    "MERGE (s)-[:REFERENCES]->(p)",
                    {"sid": session_id, "pid": packet_id},
                )
            if query and include_sources:
                q_parts = []
                if "orchestration" in include_sources:
                    q_parts.append(
                        "MATCH (n:Task) WHERE n.id=$task_id OR toLower(coalesce(n.title,'') ) CONTAINS toLower($q) "
                        "OR toLower(coalesce(n.kind,'')) CONTAINS toLower($q) "
                        "OR toLower(coalesce(toString(n.payload),'')) CONTAINS toLower($q) "
                        "WITH n, 'Task' AS source_type "
                        "ORDER BY CASE WHEN n.id=$task_id THEN 0 ELSE 1 END, "
                        "coalesce(n.last_heartbeat_at_ts, n.updated_at_ts, n.created_at_ts, 0) DESC LIMIT $max_items "
                        "MATCH (p:ContextPacket {id:$pid}) MERGE (p)-[r:REFERENCES {source:'orchestration'}]->(n) "
                        "SET r.source_type = source_type, r.rank_source='task'"
                    )
                    q_parts.append(
                        "MATCH (t:Task {id:$task_id})-[:EXECUTED_BY]->(n:AgentRun) "
                        "WITH n, 'AgentRun' AS source_type ORDER BY coalesce(n.ended_at_ts, n.started_at_ts, 0) DESC LIMIT $max_items "
                        "MATCH (p:ContextPacket {id:$pid}) MERGE (p)-[r:REFERENCES {source:'orchestration'}]->(n) "
                        "SET r.source_type = source_type, r.rank_source='recent_run'"
                    )
                if "knowledge" in include_sources:
                    q_parts.append(
                        "MATCH (tr:Transcription) WHERE toLower(coalesce(tr.text,'')) CONTAINS toLower($q) "
                        "WITH tr, 'Transcription' AS source_type ORDER BY coalesce(tr.created_at_ts,0) DESC LIMIT $max_items "
                        "MATCH (p:ContextPacket {id:$pid}) MERGE (p)-[r:REFERENCES {source:'knowledge'}]->(tr) "
                        "SET r.source_type = source_type, r.rank_source='transcription'"
                    )
                    q_parts.append(
                        "MATCH (sg:Segment) WHERE toLower(coalesce(sg.text,'')) CONTAINS toLower($q) "
                        "WITH sg, 'Segment' AS source_type ORDER BY coalesce(sg.created_at_ts,0) DESC LIMIT $max_items "
                        "MATCH (p:ContextPacket {id:$pid}) MERGE (p)-[r:REFERENCES {source:'knowledge'}]->(sg) "
                        "SET r.source_type = source_type, r.rank_source='segment'"
                    )
                if "memory" in include_sources:
                    q_parts.append(
                        "MATCH (m:MemoryItem) "
                        "OPTIONAL MATCH (:Task {id:$task_id})-[:RELATED_MEMORY]->(m) "
                        "WITH m, count(*) AS related_count "
                        "WHERE related_count > 0 OR toLower(coalesce(m.text,'')) CONTAINS toLower($q) "
                        "OR toLower(coalesce(m.kind,'')) CONTAINS toLower($q) "
                        "WITH m, 'MemoryItem' AS source_type, related_count "
                        "ORDER BY CASE WHEN related_count > 0 THEN 0 ELSE 1 END, "
                        "coalesce(m.updated_at_ts, m.created_at_ts, 0) DESC LIMIT $max_items "
                        "MATCH (p:ContextPacket {id:$pid}) MERGE (p)-[r:REFERENCES {source:'memory'}]->(m) "
                        "SET r.source_type = source_type, r.rank_source='memory'"
                    )
                for part in q_parts:
                    s.run(part, {"q": query, "pid": packet_id, "task_id": task_id, "max_items": max_items})
        packet = self.get_context_packet(packet_id)
        return packet or {"id": packet_id, "query": query, "max_items": max_items, "include_sources": include_sources or [], "references": []}

    def create_dispatch_with_paperclip(
        self,
        task_id: str,
        target: Dict[str, Any],
        priority: str = "MEDIUM",
        idempotency_key: Optional[str] = None,
        paperclip_client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        paperclip_issue_id = target.get("paperclip_issue_id")
        context_packet_id = None
        paperclip_error = None

        if paperclip_client and not paperclip_issue_id:
            packet = self.create_context_packet(
                query=task.get("title") or task.get("kind") or f"Task {task_id}",
                task_id=task_id,
                max_items=20,
                include_sources=["memory", "knowledge", "orchestration"],
            )
            context_packet_id = packet.get("id")
            try:
                paperclip_issue_id = paperclip_client.create_issue(
                    title=task.get("title") or "AssistX Task",
                    description=str(task.get("description") or task.get("payload_json") or task.get("payload") or ""),
                    task_id=task_id,
                    context_packet_id=context_packet_id or "",
                    capabilities=target.get("capabilities") or task.get("required_capabilities") or [],
                    priority=priority.lower(),
                    assignee_id=target.get("paperclip_agent_id"),
                )
                target = {**target, "paperclip_issue_id": paperclip_issue_id}
            except Exception as e:
                paperclip_error = str(e)

        dispatch_id = self.create_dispatch(
            task_id=task_id,
            target=target,
            priority=priority,
            idempotency_key=idempotency_key,
        )

        if context_packet_id:
            with self._session() as s:
                s.run(
                    "MATCH (d:Dispatch {id:$did}), (p:ContextPacket {id:$pid}) "
                    "MERGE (d)-[:USES_CONTEXT]->(p)",
                    {"did": dispatch_id, "pid": context_packet_id},
                )

        dispatch = self.get_dispatch(dispatch_id)
        target_device_id = (dispatch or {}).get("target_device_id")

        return {
            "dispatch_id": dispatch_id,
            "paperclip_issue_id": paperclip_issue_id,
            "context_packet_id": context_packet_id,
            "target_device_id": target_device_id,
            "paperclip_error": paperclip_error,
        }

    def create_dispatch(
        self,
        task_id: str,
        target: Dict[str, Any],
        priority: str = "MEDIUM",
        idempotency_key: Optional[str] = None,
    ) -> str:
        target_device_id = target.get("target_device_id")
        if not target_device_id:
            caps = target.get("capabilities", [])
            devices = self.select_device_for_task(required_capabilities=caps, limit=1)
            if devices:
                target_device_id = devices[0].get("id")
        props = {
            "status": "OPEN",
            "priority": priority,
            "paperclip_issue_id": target.get("paperclip_issue_id"),
            "paperclip_agent_id": target.get("paperclip_agent_id"),
            "target_device_id": target_device_id,
            "capabilities": target.get("capabilities", []),
        }
        dispatch_id = uuid.uuid4().hex
        with self._session() as s:
            if idempotency_key:
                rec = s.run(
                    "MERGE (d:Dispatch {idempotency_key:$idempotency_key}) "
                    "ON CREATE SET d.id=$did, d.created_at=datetime(), d.created_at_ts=timestamp(), d.status='OPEN' "
                    "SET d += $props "
                    "RETURN d.id AS id",
                    {"idempotency_key": idempotency_key, "props": props, "did": dispatch_id},
                ).single()
                dispatch_id = rec["id"]
            else:
                s.run(
                    "CREATE (d:Dispatch {id:$did}) "
                    "SET d += $props, d.created_at=datetime(), d.created_at_ts=timestamp() "
                    "RETURN d.id AS id",
                    {"did": dispatch_id, "props": props},
                ).single()
            s.run(
                "MATCH (t:Task {id:$tid}), (d:Dispatch {id:$did}) "
                "MERGE (t)-[:DISPATCHED_AS]->(d)",
                {"tid": task_id, "did": dispatch_id},
            ).consume()
            if target.get("paperclip_agent_id"):
                session_id = uuid.uuid4().hex
                s.run(
                    "MATCH (d:Dispatch {id:$did}) "
                    "MERGE (a:AgentSession {paperclip_agent_id:$aid}) "
                    "ON CREATE SET a.id=$sid, a.created_at=datetime(), a.created_at_ts=timestamp() "
                    "SET a.paperclip_agent_id=$aid, a.updated_at=datetime(), a.updated_at_ts=timestamp() "
                    "MERGE (d)-[:ASSIGNED_TO]->(a)",
                    {"aid": target["paperclip_agent_id"], "did": dispatch_id, "sid": session_id},
                ).consume()
        return dispatch_id

    def upsert_ticket(
        self,
        title: str,
        ticket_type: str = "task",
        status: str = "READY",
        kind: Optional[str] = None,
        parent_id: Optional[str] = None,
        required_capabilities: Optional[List[str]] = None,
        target_agent_id: Optional[str] = None,
        priority: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        props = {
            "title": title,
            "ticket_type": ticket_type,
            "status": status,
            "kind": kind or ticket_type,
            "required_capabilities": required_capabilities or [],
            "target_agent_id": target_agent_id,
            "priority": priority,
            "payload_json": json.dumps(payload or {}),
        }
        task_id = uuid.uuid4().hex
        with self._session() as s:
            if idempotency_key:
                rec = s.run(
                    "MERGE (t:Task {idempotency_key:$idempotency_key}) "
                    "ON CREATE SET t.id=$id, t.created_at=datetime(), t.created_at_ts=timestamp() "
                    "SET t += $props, t.updated_at=datetime(), t.updated_at_ts=timestamp() "
                    "RETURN t.id AS id",
                    {"idempotency_key": idempotency_key, "id": task_id, "props": props},
                ).single()
            else:
                rec = s.run(
                    "CREATE (t:Task {id:$id}) "
                    "SET t += $props, t.created_at=datetime(), t.created_at_ts=timestamp(), "
                    "    t.updated_at=datetime(), t.updated_at_ts=timestamp() "
                    "RETURN t.id AS id",
                    {"id": task_id, "props": props},
                ).single()
            ticket_id = rec["id"]
            if parent_id:
                s.run(
                    "MATCH (parent:Task {id:$parent_id}), (child:Task {id:$ticket_id}) "
                    "MERGE (parent)-[:HAS_CHILD]->(child) "
                    "MERGE (child)-[:PART_OF]->(parent)",
                    {"parent_id": parent_id, "ticket_id": ticket_id},
                )
            return ticket_id

    def create_deliverable_from_ask(
        self,
        question: str,
        answer_id: Optional[str] = None,
        mode: str = "auto",
        user: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, str]:
        intent_id = self.upsert_intent(
            source="ask",
            text=question,
            idempotency_key=f"ask-intent:{idempotency_key}" if idempotency_key else None,
            metadata={"answer_id": answer_id, "mode": mode, "user": user},
        )
        deliverable_id = self.upsert_ticket(
            title=question,
            ticket_type="deliverable",
            status="READY",
            kind="ask_deliverable",
            payload={"answer_id": answer_id, "mode": mode, "user": user},
            idempotency_key=f"deliverable:{idempotency_key}" if idempotency_key else None,
        )
        epic_id = self.upsert_ticket(
            title=f"Answer: {question[:120]}",
            ticket_type="epic",
            status="READY",
            kind="answer_request",
            parent_id=deliverable_id,
            payload={"answer_id": answer_id, "question": question},
            idempotency_key=f"epic:{idempotency_key}" if idempotency_key else None,
        )
        story_id = self.upsert_ticket(
            title="Gather graph context and compose response",
            ticket_type="story",
            status="READY",
            kind="qa_response_story",
            parent_id=epic_id,
            required_capabilities=["graph_query", "analysis"],
            payload={"answer_id": answer_id, "question": question},
            idempotency_key=f"story:{idempotency_key}" if idempotency_key else None,
        )
        task_id = self.upsert_ticket(
            title="Execute QA pipeline and publish answer",
            ticket_type="task",
            status="READY",
            kind="qa_pipeline_task",
            parent_id=story_id,
            required_capabilities=["graph_query", "analysis"],
            payload={"answer_id": answer_id, "question": question},
            idempotency_key=f"task:{idempotency_key}" if idempotency_key else None,
        )
        with self._session() as s:
            s.run(
                "MATCH (i:Intent {id:$intent_id}), (d:Task {id:$deliverable_id}) "
                "MERGE (i)-[:CREATED_TASK]->(d)",
                {"intent_id": intent_id, "deliverable_id": deliverable_id},
            )
        return {
            "intent_id": intent_id,
            "deliverable_id": deliverable_id,
            "epic_id": epic_id,
            "story_id": story_id,
            "task_id": task_id,
        }

    def complete_deliverable(
        self,
        deliverable_id: str,
        answer_id: Optional[str] = None,
        status: str = "DONE",
        summary: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if status not in TERMINAL_TASK_STATUSES:
            raise ValueError(f"Deliverable completion status must be one of: {sorted(TERMINAL_TASK_STATUSES)}")
        with self._session() as s:
            event_id = uuid.uuid4().hex
            rec = s.run(
                """
                MATCH (d:Task {id:$deliverable_id})
                SET d.status=$status,
                    d.answer_id=$answer_id,
                    d.result_summary=$summary,
                    d.result_json=$result_json,
                    d.completed_at=datetime(),
                    d.completed_at_ts=timestamp(),
                    d.updated_at=datetime(),
                    d.updated_at_ts=timestamp()
                CREATE (e:SignalEvent {id:$event_id})
                SET e.event_type='deliverable_completed',
                    e.answer_id=$answer_id,
                    e.payload_json=$payload_json,
                    e.created_at=datetime(),
                    e.created_at_ts=timestamp(),
                    e.updated_at=datetime(),
                    e.updated_at_ts=timestamp()
                MERGE (d)-[:HAS_EVENT]->(e)
                RETURN d, e.id AS event_id
                """,
                {
                    "deliverable_id": deliverable_id,
                    "event_id": event_id,
                    "answer_id": answer_id,
                    "status": status,
                    "summary": summary,
                    "result_json": json.dumps(result or {}),
                    "payload_json": json.dumps({"answer_id": answer_id, "status": status, "summary": summary}),
                },
            ).single()
            if not rec:
                return None
            s.run(
                """
                MATCH (:Task {id:$deliverable_id})-[:HAS_CHILD*1..3]->(child:Task)
                WHERE NOT child.status IN ['DONE','FAILED','CANCELLED']
                SET child.status=$status,
                    child.updated_at=datetime(),
                    child.updated_at_ts=timestamp()
                """,
                {"deliverable_id": deliverable_id, "status": status},
            )
            deliverable = dict(rec["d"])
            deliverable["event_id"] = rec["event_id"]
            return deliverable

    def get_ticket_tree(self, ticket_id: str, depth: int = 3) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            rec = s.run(
                """
                MATCH (root:Task {id:$ticket_id})
                OPTIONAL MATCH path=(root)-[:HAS_CHILD*1..3]->(child:Task)
                RETURN root, collect(DISTINCT child) AS children
                """,
                {"ticket_id": ticket_id, "depth": depth},
            ).single()
            if not rec:
                return None
            return {
                "ticket": dict(rec["root"]),
                "children": [dict(child) for child in rec["children"] if child],
            }

    def list_agent_tasks(
        self,
        status: str = "READY",
        capabilities: Optional[List[str]] = None,
        agent_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Return graph-trigger tasks an agent can work on.

        Tasks with no required_capabilities are considered generally eligible.
        Tasks can target a specific agent with target_agent_id.
        """
        caps = capabilities or []
        q = (
            "MATCH (t:Task {status:$status}) "
            "WITH t, coalesce(t.required_capabilities, t.capabilities, []) AS required "
            "WHERE (size(required)=0 OR size($caps)=0 OR all(cap IN required WHERE cap IN $caps)) "
            "  AND ($agent_id IS NULL OR t.target_agent_id IS NULL OR t.target_agent_id=$agent_id) "
            "RETURN t, required "
            "ORDER BY coalesce(t.priority_rank, 999), coalesce(t.created_at_ts, 0) ASC "
            "LIMIT $limit"
        )
        with self._session() as s:
            res = s.run(
                q,
                {
                    "status": status,
                    "caps": caps,
                    "agent_id": agent_id,
                    "limit": limit,
                },
            )
            items = []
            for r in res:
                item = dict(r["t"])
                item["required_capabilities"] = r["required"] or []
                items.append(item)
            return items

    def claim_task(
        self,
        task_id: str,
        agent_id: str,
        capabilities: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically claim a READY task for an agent if capabilities match."""
        caps = capabilities or []
        claim_id = idempotency_key or uuid.uuid4().hex
        with self._session() as s:
            if idempotency_key:
                prior = s.run(
                    "MATCH (t:Task {id:$task_id, claim_id:$claim_id}) RETURN t",
                    {"task_id": task_id, "claim_id": idempotency_key},
                ).single()
                if prior:
                    return {"claimed": True, "idempotent": True, "task": dict(prior["t"])}

            rec = s.run(
                """
                MATCH (t:Task {id:$task_id})
                WHERE t.status='READY'
                SET t.status='CLAIMED',
                    t.claimed_by=$agent_id,
                    t.agent_session_id=$session_id,
                    t.claim_id=$claim_id,
                    t.claimed_at=datetime(),
                    t.claimed_at_ts=timestamp(),
                    t.updated_at=datetime(),
                    t.updated_at_ts=timestamp()
                RETURN t
                """,
                {
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "claim_id": claim_id,
                },
            ).single()

            if rec:
                task = dict(rec["t"])
                if session_id:
                    s.run(
                        "MERGE (a:AgentSession {id:$sid}) "
                        "ON CREATE SET a.created_at=datetime(), a.created_at_ts=timestamp() "
                        "SET a.paperclip_agent_id=coalesce(a.paperclip_agent_id, $agent_id), "
                        "    a.updated_at=datetime(), a.updated_at_ts=timestamp() "
                        "WITH a MATCH (t:Task {id:$task_id}) MERGE (a)-[:CLAIMED]->(t)",
                        {"sid": session_id, "agent_id": agent_id, "task_id": task_id},
                    )
                return {"claimed": True, "task": task}

            existing = s.run(
                "MATCH (t:Task {id:$task_id}) "
                "RETURN t.status AS status, t.target_agent_id AS target_agent_id, "
                "t.required_capabilities AS required_capabilities, "
                "t.capabilities AS capabilities",
                {"task_id": task_id},
            ).single()
            if not existing:
                return {"claimed": False, "reason": "not_found"}
            if existing["status"] != "READY":
                return {"claimed": False, "reason": "not_ready", "status": existing["status"]}
            required = existing.get("required_capabilities") or existing.get("capabilities") or []
            if caps and required and not all(cap in caps for cap in required):
                return {"claimed": False, "reason": "capability_mismatch", "required_capabilities": required}
            if existing["target_agent_id"] and existing["target_agent_id"] != agent_id:
                return {"claimed": False, "reason": "target_agent_mismatch"}
            return {"claimed": False, "reason": "not_claimed"}

    def heartbeat_task(
        self,
        task_id: str,
        agent_id: str,
        status: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if status and status not in EXECUTABLE_TASK_STATUSES:
            raise ValueError(f"Unsupported task status: {status}")
        props = {
            "heartbeat_by": agent_id,
            "agent_session_id": session_id,
            "heartbeat_metadata_json": json.dumps(metadata or {}),
        }
        with self._session() as s:
            rec = s.run(
                """
                MATCH (t:Task {id:$task_id})
                SET t += $props,
                    t.status = coalesce($status, t.status),
                    t.last_heartbeat_at=datetime(),
                    t.last_heartbeat_at_ts=timestamp(),
                    t.updated_at=datetime(),
                    t.updated_at_ts=timestamp()
                RETURN t
                """,
                {"task_id": task_id, "props": props, "status": status},
            ).single()
            return dict(rec["t"]) if rec else None

    def complete_task(
        self,
        task_id: str,
        agent_id: str,
        status: str,
        summary: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if status not in TERMINAL_TASK_STATUSES:
            raise ValueError(f"Completion status must be one of: {sorted(TERMINAL_TASK_STATUSES)}")
        with self._session() as s:
            if idempotency_key:
                prior = s.run(
                    "MATCH (t:Task {id:$task_id})-[:EXECUTED_BY]->(r:AgentRun {completion_id:$completion_id}) "
                    "RETURN t, r.id AS run_id",
                    {"task_id": task_id, "completion_id": idempotency_key},
                ).single()
                if prior:
                    task = dict(prior["t"])
                    task["run_id"] = prior["run_id"]
                    task["idempotent"] = True
                    return task
            run_id = uuid.uuid4().hex
            rec = s.run(
                """
                MATCH (t:Task {id:$task_id})
                SET t.status=$status,
                    t.completed_by=$agent_id,
                    t.agent_session_id=coalesce($session_id, t.agent_session_id),
                    t.result_summary=$summary,
                    t.result_json=$result_json,
                    t.completed_at=datetime(),
                    t.completed_at_ts=timestamp(),
                    t.updated_at=datetime(),
                    t.updated_at_ts=timestamp()
                CREATE (r:AgentRun {id:$run_id, task_id:$task_id, agent:$agent_id,
                    completion_id:$completion_id,
                    status:$status, summary:$summary, result_json:$result_json,
                    started_at_ts:coalesce(t.claimed_at_ts, timestamp()),
                    ended_at:datetime(), ended_at_ts:timestamp()})
                MERGE (t)-[:EXECUTED_BY]->(r)
                RETURN t, r.id AS run_id
                """,
                {
                    "task_id": task_id,
                    "run_id": run_id,
                    "agent_id": agent_id,
                    "status": status,
                    "summary": summary,
                    "result_json": json.dumps(result or {}),
                    "session_id": session_id,
                    "completion_id": idempotency_key,
                },
            ).single()
            if not rec:
                return None
            task = dict(rec["t"])
            task["run_id"] = rec["run_id"]
            if summary:
                memory_id = uuid.uuid4().hex
                memory_rec = s.run(
                    """
                    CREATE (m:MemoryItem {id:$memory_id})
                    SET m.kind='outcome',
                        m.text=$summary,
                        m.source=$agent_id,
                        m.metadata_json=$metadata_json,
                        m.created_at=datetime(),
                        m.created_at_ts=timestamp(),
                        m.updated_at=datetime(),
                        m.updated_at_ts=timestamp()
                    WITH m MATCH (t:Task {id:$task_id}) MERGE (t)-[:RELATED_MEMORY]->(m)
                    RETURN m.id AS id
                    """,
                    {
                        "task_id": task_id,
                        "memory_id": memory_id,
                        "summary": summary,
                        "agent_id": agent_id,
                        "metadata_json": json.dumps({"result": result or {}, "status": status}),
                    },
                ).single()
                task["memory_item_id"] = memory_rec["id"] if memory_rec else None
            return task

    def ingest_paperclip_event(
        self,
        event_type: str,
        paperclip_issue_id: str,
        paperclip_agent_id: Optional[str],
        paperclip_run_id: Optional[str],
        event_id: str,
        payload: Dict[str, Any],
    ) -> str:
        status_map = {
            "issue_created": "OPEN",
            "assigned": "ASSIGNED",
            "run_started": "RUNNING",
            "run_completed": "COMPLETED",
        }
        status = status_map.get(event_type, payload.get("status", "OPEN"))
        with self._session() as s:
            s.run(
                "MERGE (d:Dispatch {paperclip_issue_id:$issue}) "
                "ON CREATE SET d.id=$did, d.created_at=datetime(), d.created_at_ts=timestamp() "
                "SET d.status=$status, d.paperclip_issue_id=$issue, d.paperclip_agent_id=$agent_id, "
                "    d.paperclip_run_id=$run_id, d.updated_at=datetime(), d.updated_at_ts=timestamp() ",
                {
                    "did": uuid.uuid4().hex,
                    "issue": paperclip_issue_id,
                    "status": status,
                    "agent_id": paperclip_agent_id,
                    "run_id": paperclip_run_id,
                },
            )
            if paperclip_agent_id:
                s.run(
                    "MERGE (a:AgentSession {paperclip_agent_id:$aid}) "
                    "ON CREATE SET a.id=$sid, a.created_at=datetime(), a.created_at_ts=timestamp() "
                    "SET a.paperclip_agent_id=$aid, a.updated_at=datetime(), a.updated_at_ts=timestamp() "
                    "MERGE (d:Dispatch {paperclip_issue_id:$issue})-[:ASSIGNED_TO]->(a)",
                    {"aid": paperclip_agent_id, "sid": uuid.uuid4().hex, "issue": paperclip_issue_id},
                )
            s.run(
                "MERGE (e:SignalEvent {id:$eid}) "
                "ON CREATE SET e.created_at=datetime(), e.created_at_ts=timestamp() "
                "SET e.event_type=$event_type, e.payload_json=$payload_json, "
                "    e.paperclip_issue_id=$issue, e.paperclip_agent_id=$agent_id, e.paperclip_run_id=$run_id, "
                "    e.updated_at=datetime(), e.updated_at_ts=timestamp() "
                "WITH e MATCH (d:Dispatch {paperclip_issue_id:$issue}) MERGE (d)-[:HAS_EVENT]->(e)",
                {
                    "eid": event_id,
                    "event_type": event_type,
                    "payload_json": json.dumps(payload or {}),
                    "issue": paperclip_issue_id,
                    "agent_id": paperclip_agent_id,
                    "run_id": paperclip_run_id,
                },
            )
        return paperclip_issue_id

    def get_context_packet(self, packet_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            rec = s.run(
                "MATCH (p:ContextPacket {id:$id}) OPTIONAL MATCH (p)-[r]->(x) "
                "WITH p, r, x, properties(x) AS props "
                "WITH p, collect({type: type(r), source: coalesce(r.source, 'unknown'), "
                "source_type: coalesce(r.source_type, CASE WHEN x IS NULL THEN 'unknown' ELSE labels(x)[0] END), "
                "node_id: coalesce(x.id, props.id), "
                "timestamp: coalesce(props.updated_at_ts, props.created_at_ts, props.last_heartbeat_at_ts, props.ended_at_ts), "
                "snippet: coalesce(props.title, props.text, props.summary, props.result_summary, props.kind, ''), "
                "provenance: {rank_source: r.rank_source, relationship: type(r)}, "
                "node: props}) AS refs "
                "RETURN p, [ref IN refs WHERE ref.type IS NOT NULL] AS refs",
                {"id": packet_id},
            ).single()
            if not rec:
                return None
            packet = dict(rec["p"])
            packet["references"] = rec["refs"]
            return packet

    def list_dispatches(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        q = "MATCH (d:Dispatch)"
        if status:
            q += " WHERE d.status=$status"
        q += " RETURN d ORDER BY d.created_at_ts DESC LIMIT $limit"
        with self._session() as s:
            res = s.run(q, {"status": status, "limit": limit})
            return [dict(r["d"]) for r in res]

    def get_dispatch(self, dispatch_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            rec = s.run("MATCH (d:Dispatch {id:$id}) RETURN d", {"id": dispatch_id}).single()
            return dict(rec["d"]) if rec else None

    def list_agent_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._session() as s:
            res = s.run("MATCH (s:AgentSession) RETURN s ORDER BY s.updated_at_ts DESC LIMIT $limit", {"limit": limit})
            return [dict(r["s"]) for r in res]

    def upsert_memory_item(
        self,
        kind: str,
        text: str,
        source: str,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        props = {
            "kind": kind,
            "text": text,
            "source": source,
            "metadata_json": json.dumps(metadata or {}),
        }
        memory_id = uuid.uuid4().hex
        with self._session() as s:
            rec = s.run(
                "CREATE (m:MemoryItem {id:$id}) "
                "SET m += $props, m.created_at=datetime(), m.created_at_ts=timestamp(), "
                "    m.updated_at=datetime(), m.updated_at_ts=timestamp() "
                "RETURN m.id AS id",
                {"id": memory_id, "props": props},
            ).single()
            if session_id:
                s.run(
                    "MATCH (s:AgentSession {id:$sid}), (m:MemoryItem {id:$mid}) "
                    "MERGE (s)-[:WROTE_MEMORY]->(m)",
                    {"sid": session_id, "mid": memory_id},
                )
            if task_id:
                s.run(
                    "MATCH (t:Task {id:$tid}), (m:MemoryItem {id:$mid}) "
                    "MERGE (t)-[:RELATED_MEMORY]->(m)",
                    {"tid": task_id, "mid": memory_id},
                )
        return memory_id

    def ingest_media_capture(
        self,
        *,
        capture_id: str,
        user_id: str,
        session_id: str,
        transcript: str = "",
        media_path: str = "",
        filename: str = "",
        content_type: str = "",
        media_kind: str = "media",
        duration_ms: int = 0,
        byte_count: int = 0,
        device_id: str = "",
        device_fingerprint: str = "",
        activity_context: str = "",
        client_context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        intent_classification: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a mobile audio/video capture and its graph intake records."""
        transcript_text = " ".join((transcript or "").strip().split())
        context = client_context or {}
        metadata = metadata or {}
        transcription_id = f"{capture_id}:transcription" if transcript_text else None
        memory_id = f"{capture_id}:memory" if transcript_text else None
        intent_id = f"{capture_id}:intent" if transcript_text else None
        task_id: Optional[str] = None
        event_id = f"{capture_id}:capture_created"
        props = {
            "user_id": user_id,
            "session_id": session_id,
            "transcript": transcript_text,
            "media_path": media_path,
            "filename": filename,
            "content_type": content_type,
            "media_kind": media_kind,
            "duration_ms": duration_ms,
            "byte_count": byte_count,
            "device_id": device_id,
            "device_fingerprint": device_fingerprint,
            "activity_context": activity_context,
            "context_json": json.dumps(context, ensure_ascii=False),
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "source": "assistx_capture",
        }
        with self._session() as s:
            s.run(
                """
                MERGE (c:MediaCapture {id:$capture_id})
                ON CREATE SET c.created_at=datetime(), c.created_at_ts=timestamp()
                SET c += $props,
                    c.updated_at=datetime(),
                    c.updated_at_ts=timestamp()
                """,
                {"capture_id": capture_id, "props": props},
            )
            if session_id:
                s.run(
                    """
                    MERGE (sess:AgentSession {id:$session_id})
                      ON CREATE SET sess.created_at=datetime(), sess.created_at_ts=timestamp()
                    SET sess.updated_at=datetime(), sess.updated_at_ts=timestamp()
                    WITH sess
                    MATCH (c:MediaCapture {id:$capture_id})
                    MERGE (sess)-[:HAS_CAPTURE]->(c)
                    """,
                    {"session_id": session_id, "capture_id": capture_id},
                )
            if device_id:
                s.run(
                    """
                    MERGE (d:AgentDevice {id:$device_id})
                      ON CREATE SET d.created_at=datetime(), d.created_at_ts=timestamp(),
                                    d.device_id=$device_id
                    SET d.fingerprint=$device_fingerprint,
                        d.user_agent=$user_agent,
                        d.platform=$platform,
                        d.language=$language,
                        d.timezone=$timezone,
                        d.last_seen_at=datetime(),
                        d.last_seen_at_ts=timestamp()
                    WITH d
                    MATCH (c:MediaCapture {id:$capture_id})
                    MERGE (d)-[:RECORDED]->(c)
                    """,
                    {
                        "device_id": device_id,
                        "device_fingerprint": device_fingerprint,
                        "user_agent": str(context.get("user_agent") or ""),
                        "platform": str(context.get("platform") or ""),
                        "language": str(context.get("language") or ""),
                        "timezone": str(context.get("timezone") or ""),
                        "capture_id": capture_id,
                    },
                )
            if media_path:
                s.run(
                    """
                    MERGE (a:MediaAsset {path:$media_path})
                      ON CREATE SET a.created_at=datetime(), a.created_at_ts=timestamp()
                    SET a.filename=$filename,
                        a.content_type=$content_type,
                        a.media_kind=$media_kind,
                        a.byte_count=$byte_count,
                        a.source='assistx_capture',
                        a.updated_at=datetime(),
                        a.updated_at_ts=timestamp()
                    WITH a
                    MATCH (c:MediaCapture {id:$capture_id})
                    MERGE (c)-[:HAS_MEDIA]->(a)
                    """,
                    {
                        "media_path": media_path,
                        "filename": filename,
                        "content_type": content_type,
                        "media_kind": media_kind,
                        "byte_count": byte_count,
                        "capture_id": capture_id,
                    },
                )
            if transcript_text:
                s.run(
                    """
                    MERGE (tr:Transcription {id:$transcription_id})
                      ON CREATE SET tr.created_at=datetime(), tr.created_at_ts=timestamp()
                    SET tr.key=$capture_id,
                        tr.text=$text,
                        tr.source_json=$media_path,
                        tr.source='assistx_capture',
                        tr.updated_at=datetime(),
                        tr.updated_at_ts=timestamp()
                    WITH tr
                    MATCH (c:MediaCapture {id:$capture_id})
                    MERGE (c)-[:HAS_TRANSCRIPTION]->(tr)
                    """,
                    {
                        "transcription_id": transcription_id,
                        "capture_id": capture_id,
                        "text": transcript_text,
                        "media_path": media_path,
                    },
                )
                s.run(
                    """
                    MERGE (m:MemoryItem {id:$memory_id})
                      ON CREATE SET m.created_at=datetime(), m.created_at_ts=timestamp()
                    SET m.kind='capture',
                        m.text=$text,
                        m.source='assistx_capture',
                        m.metadata_json=$metadata_json,
                        m.updated_at=datetime(),
                        m.updated_at_ts=timestamp()
                    WITH m
                    MATCH (c:MediaCapture {id:$capture_id})
                    MERGE (c)-[:RELATED_MEMORY]->(m)
                    """,
                    {
                        "memory_id": memory_id,
                        "text": transcript_text,
                        "metadata_json": json.dumps(
                            {
                                "capture_id": capture_id,
                                "session_id": session_id,
                                "media_kind": media_kind,
                                "activity_context": activity_context,
                            },
                            ensure_ascii=False,
                        ),
                        "capture_id": capture_id,
                    },
                )
                classification = intent_classification or "unknown"
                s.run(
                    """
                    MERGE (i:Intent {id:$intent_id})
                      ON CREATE SET i.created_at=datetime(), i.created_at_ts=timestamp()
                    SET i.source='capture',
                        i.text=$text,
                        i.client_ts=$client_ts,
                        i.classification=$classification,
                        i.metadata_json=$metadata_json,
                        i.updated_at=datetime(),
                        i.updated_at_ts=timestamp()
                    WITH i
                    MATCH (c:MediaCapture {id:$capture_id})
                    MERGE (i)-[:CREATED_FROM]->(c)
                    """,
                    {
                        "intent_id": intent_id,
                        "text": transcript_text,
                        "classification": classification,
                        "client_ts": str(context.get("captured_at") or ""),
                        "metadata_json": json.dumps(
                            {"capture_id": capture_id, "session_id": session_id, "media_kind": media_kind},
                            ensure_ascii=False,
                        ),
                        "capture_id": capture_id,
                    },
                )
                if classification == "task":
                    task_id = uuid.uuid4().hex
                    s.run(
                        """
                        MATCH (i:Intent {id:$intent_id})
                        CREATE (t:Task {id:$task_id})
                        SET t.title=$title,
                            t.description=$text,
                            t.status='READY',
                            t.kind='capture',
                            t.source='capture',
                            t.ticket_type='task',
                            t.created_at=datetime(),
                            t.created_at_ts=timestamp(),
                            t.updated_at=datetime(),
                            t.updated_at_ts=timestamp()
                        MERGE (i)-[:CREATED_TASK]->(t)
                        """,
                        {
                            "intent_id": intent_id,
                            "task_id": task_id,
                            "title": transcript_text[:120],
                            "text": transcript_text,
                        },
                    )
            self.create_signal_event(
                event_id=event_id,
                event_type="media_capture_created",
                payload={
                    "capture_id": capture_id,
                    "session_id": session_id,
                    "media_kind": media_kind,
                    "has_transcript": bool(transcript_text),
                    "byte_count": byte_count,
                },
                session_id=session_id,
            )
        result = {
            "capture_id": capture_id,
            "transcription_id": transcription_id,
            "memory_item_id": memory_id,
            "intent_id": intent_id,
            "signal_event_id": event_id,
            "intent_classification": intent_classification,
        }
        if task_id:
            result["task_id"] = task_id
        return result

    def create_signal_event(
        self,
        event_id: str,
        event_type: str,
        payload: Dict[str, Any],
        session_id: Optional[str] = None,
        paperclip_issue_id: Optional[str] = None,
        paperclip_run_id: Optional[str] = None,
    ) -> str:
        props = {
            "event_type": event_type,
            "payload_json": json.dumps(payload or {}),
            "paperclip_issue_id": paperclip_issue_id,
            "paperclip_run_id": paperclip_run_id,
        }
        with self._session() as s:
            s.run(
                "MERGE (e:SignalEvent {id:$eid}) "
                "ON CREATE SET e.created_at=datetime(), e.created_at_ts=timestamp() "
                "SET e += $props, e.updated_at=datetime(), e.updated_at_ts=timestamp() ",
                {"eid": event_id, "props": props},
            )
            if session_id:
                s.run(
                    "MATCH (s:AgentSession {id:$sid}), (e:SignalEvent {id:$eid}) "
                    "MERGE (s)-[:EMITTED]->(e)",
                    {"sid": session_id, "eid": event_id},
                )
            if paperclip_issue_id:
                s.run(
                    "MATCH (d:Dispatch {paperclip_issue_id:$issue}), (e:SignalEvent {id:$eid}) "
                    "MERGE (d)-[:HAS_EVENT]->(e)",
                    {"issue": paperclip_issue_id, "eid": event_id},
                )
        return event_id

    def upsert_agent_session(
        self,
        session_id: str,
        paperclip_agent_id: Optional[str] = None,
        hermes_session_id: Optional[str] = None,
        agent_identity: Optional[str] = None,
        device_id: Optional[str] = None,
        platform: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        props = {
            "paperclip_agent_id": paperclip_agent_id,
            "hermes_session_id": hermes_session_id,
            "agent_identity": agent_identity,
            "device_id": device_id,
            "platform": platform,
            "metadata_json": json.dumps(metadata or {}),
        }
        with self._session() as s:
            rec = s.run(
                "MERGE (s:AgentSession {id:$id}) "
                "ON CREATE SET s.created_at=datetime(), s.created_at_ts=timestamp() "
                "SET s += $props, s.updated_at=datetime(), s.updated_at_ts=timestamp() "
                "RETURN s.id AS id",
                {"id": session_id, "props": props},
            ).single()
            return rec["id"]

    # ---------- v1: conversations / tasks / runs / tools / artifacts ----------

    def upsert_conversation(self, title: str, source: str) -> str:
        """
        Merge conversation by (title, source). On first create, assigns a UUID id.
        Returns the conversation id.
        """
        conv_id = uuid.uuid4().hex
        q = (
            "MERGE (c:Conversation {title:$title, source:$source}) "
            "ON CREATE SET c.id = $id, "
            "              c.created_at = datetime(), "
            "              c.created_at_ts = timestamp() "
            "ON MATCH  SET c.updated_at = datetime() "
            "RETURN c.id as id"
        )
        with self._session() as s:
            rec = s.run(q, {"title": title, "source": source, "id": conv_id}).single()
            return rec["id"]

    def add_utterances(self, conversation_id: str, rows: Iterable[Dict[str, Any]]) -> None:
        """
        Add/merge utterances and attach them to the conversation.
        Each row may contain arbitrary properties; if id is missing, a UUID is generated.
        """
        q = (
            "MERGE (u:Utterance {id:$id}) "
            "SET u += $props "
            "SET u.created_at = coalesce(u.created_at, datetime()), "
            "    u.created_at_ts = coalesce(u.created_at_ts, timestamp()) "
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
        summary_id = uuid.uuid4().hex
        with self.driver.session() as s:
            sr = s.run(
                "CREATE (m:Summary {id:$id}) "
                "SET m += $sprops, m.created_at = timestamp(), m.created_at_ts = timestamp() "
                "WITH m MATCH (c:Conversation{id:$cid}) MERGE (c)-[:HAS_SUMMARY]->(m) RETURN m.id as id",
                {"id": summary_id, "sprops": {**summary, "conversation_id": conversation_id}, "cid": conversation_id},
            ).single()
            sid = sr["id"]
            for t in tasks:
                task_id = uuid.uuid4().hex
                tprops = {**t, "conversation_id": conversation_id}
                s.run(
                    "CREATE (t:Task {id:$task_id}) "
                    "SET t += $tprops, t.created_at = timestamp(), t.created_at_ts = timestamp() "
                    "WITH t MATCH (m:Summary{id:$sid}) MERGE (m)-[:GENERATED_TASK]->(t)",
                    {"tprops": tprops, "sid": sid, "task_id": task_id},
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
        run_id = uuid.uuid4().hex
        with self.driver.session() as s:
            rec = s.run(
                "MATCH (t:Task{id:$tid}) "
                "CREATE (r:AgentRun {id:$run_id, task_id:$tid, agent:$agent, model:$model, status:'RUNNING', "
                " started_at:timestamp(), started_at_ts:timestamp(), manifest_json:$manifest}) "
                "MERGE (t)-[:EXECUTED_BY]->(r) RETURN r.id as id",
                {"tid": task_id, "run_id": run_id, "agent": agent, "model": model, "manifest": json.dumps(manifest)},
            ).single()
            return rec["id"]

    def complete_run(self, run_id: str, status: str):
        with self.driver.session() as s:
            s.run("MATCH (r:AgentRun{id:$id}) SET r.status=$st, r.ended_at=timestamp(), r.ended_at_ts=timestamp()", {"id": run_id, "st": status})

    def log_tool_call(self, run_id: str, tool: str, input_json: Dict[str, Any], output_json: Dict[str, Any] | None, ok: bool):
        call_id = uuid.uuid4().hex
        with self.driver.session() as s:
            s.run(
                "MATCH (r:AgentRun{id:$rid}) "
                "CREATE (k:ToolCall {id:$call_id, run_id:$rid, tool:$tool, input_json:$in, output_json:$out, ok:$ok, "
                " started_at:timestamp(), started_at_ts:timestamp(), ended_at:timestamp(), ended_at_ts:timestamp()}) "
                "MERGE (r)-[:USED_TOOL]->(k)",
                {"rid": run_id, "call_id": call_id, "tool": tool, "in": input_json, "out": output_json, "ok": ok},
            )

    def log_artifact(self, run_id: str, kind: str, path: str, sha256: str | None):
        artifact_id = uuid.uuid4().hex
        with self.driver.session() as s:
            s.run(
                "MATCH (r:AgentRun{id:$rid}) "
                "CREATE (a:Artifact {id:$artifact_id, run_id:$rid, kind:$k, path:$p, sha256:$h, created_at:timestamp(), created_at_ts:timestamp()}) "
                "MERGE (r)-[:PRODUCED]->(a)",
                {"rid": run_id, "artifact_id": artifact_id, "k": kind, "p": path, "h": sha256},
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

    # ---------- Phase 3: Agent devices and capabilities ----------

    def upsert_agent_device(
        self,
        device_id: str,
        hostname: Optional[str] = None,
        platform: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        props = {
            "hostname": hostname,
            "platform": platform,
            "capabilities": capabilities or [],
            "metadata_json": json.dumps(metadata or {}),
        }
        with self._session() as s:
            rec = s.run(
                "MERGE (d:AgentDevice {id:$id}) "
                "ON CREATE SET d.created_at=datetime(), d.created_at_ts=timestamp() "
                "SET d += $props, d.last_seen_at=datetime(), d.last_seen_at_ts=timestamp() "
                "RETURN d.id AS id",
                {"id": device_id, "props": props},
            ).single()
            return rec["id"]

    def register_device(
        self,
        device_id: str,
        hostname: str,
        platform: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        resources: Optional[Dict[str, Any]] = None,
        max_concurrent_tasks: int = 1,
        available_agents: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ) -> str:
        props = {
            "hostname": hostname,
            "platform": platform or "",
            "capabilities": capabilities or [],
            "resources_json": json.dumps(resources or {}),
            "max_concurrent_tasks": max_concurrent_tasks,
            "current_load": 0,
            "queue_depth": 0,
            "available_agents": available_agents or [],
            "tags": tags or [],
        }
        with self._session() as s:
            rec = s.run(
                """
                MERGE (d:AgentDevice {id:$id})
                ON CREATE SET d.created_at=datetime(), d.created_at_ts=timestamp()
                SET d += $props,
                    d.last_seen_at=datetime(),
                    d.last_seen_at_ts=timestamp(),
                    d.updated_at=datetime(),
                    d.updated_at_ts=timestamp()
                RETURN d.id AS id
                """,
                {"id": device_id, "props": props},
            ).single()
            return rec["id"]

    def heartbeat_device(
        self,
        device_id: str,
        current_load: int = 0,
        queue_depth: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            rec = s.run(
                """
                MATCH (d:AgentDevice {id:$id})
                SET d.current_load=$load,
                    d.queue_depth=$queue,
                    d.last_seen_at=datetime(),
                    d.last_seen_at_ts=timestamp(),
                    d.updated_at=datetime(),
                    d.updated_at_ts=timestamp()
                RETURN d
                """,
                {"id": device_id, "load": current_load, "queue": queue_depth, "props": metadata or {}},
            ).single()
            return dict(rec["d"]) if rec else None

    def select_device_for_task(
        self,
        required_capabilities: Optional[List[str]] = None,
        exclude_device_id: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        caps = required_capabilities or []
        with self._session() as s:
            q = """
                MATCH (d:AgentDevice)
                WHERE d.last_seen_at_ts > (timestamp() - 300000)
                  AND d.current_load < d.max_concurrent_tasks
                """
            params: Dict[str, Any] = {"limit": limit, "caps": caps}
            if caps:
                q += " AND all(cap IN $caps WHERE cap IN d.capabilities)"
            if exclude_device_id:
                q += " AND d.id <> $exclude"
                params["exclude"] = exclude_device_id
            q += """
                RETURN d
                ORDER BY
                  CASE WHEN size($caps) > 0 AND all(cap IN $caps WHERE cap IN d.capabilities)
                    THEN 0 ELSE 1 END,
                  toFloat(d.current_load) / toFloat(d.max_concurrent_tasks) ASC
                LIMIT $limit
                """
            res = s.run(q, params)
            return [dict(r["d"]) for r in res]

    def list_agent_devices(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._session() as s:
            res = s.run(
                "MATCH (d:AgentDevice) RETURN d ORDER BY d.last_seen_at_ts DESC LIMIT $limit",
                {"limit": limit},
            )
            return [dict(r["d"]) for r in res]

    def get_agent_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            rec = s.run("MATCH (d:AgentDevice {id:$id}) RETURN d", {"id": device_id}).single()
            return dict(rec["d"]) if rec else None

    def get_tasks_by_status(self, status: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._session() as s:
            res = s.run(
                "MATCH (t:Task {status:$st}) RETURN t ORDER BY t.created_at_ts DESC LIMIT $limit",
                {"st": status, "limit": limit},
            )
            return [dict(r["t"]) for r in res]

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            rec = s.run("MATCH (t:Task {id:$id}) RETURN t", {"id": task_id}).single()
            return dict(rec["t"]) if rec else None

    # ---------- Phase 9: feeds + evaluations ----------

    def upsert_data_feed_connector(
        self,
        connector_id: str,
        name: str,
        category: str,
        endpoint: str,
        enabled: bool,
        health_status: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        with self._session() as s:
            rec = s.run(
                """
                MERGE (f:DataFeedConnector {id:$id})
                ON CREATE SET f.created_at=datetime(), f.created_at_ts=timestamp()
                SET f.name=$name,
                    f.category=$category,
                    f.endpoint=$endpoint,
                    f.enabled=$enabled,
                    f.health_status=$health_status,
                    f.metadata_json=$metadata_json,
                    f.updated_at=datetime(),
                    f.updated_at_ts=timestamp()
                RETURN f.id AS id
                """,
                {
                    "id": connector_id,
                    "name": name,
                    "category": category,
                    "endpoint": endpoint,
                    "enabled": enabled,
                    "health_status": health_status,
                    "metadata_json": json.dumps(metadata or {}),
                },
            ).single()
            return str(rec["id"])

    def list_data_feed_connectors(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._session() as s:
            res = s.run(
                """
                MATCH (f:DataFeedConnector)
                RETURN f
                ORDER BY coalesce(f.updated_at_ts, f.created_at_ts, 0) DESC
                LIMIT $limit
                """,
                {"limit": limit},
            )
            return [dict(r["f"]) for r in res]

    def create_evaluation_run(
        self,
        suite_name: str,
        agent_class: str,
        status: str,
        score: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        run_id = uuid.uuid4().hex
        suite_id = hashlib.sha1(suite_name.strip().lower().encode("utf-8")).hexdigest()[:16]
        with self._session() as s:
            rec = s.run(
                """
                MERGE (suite:EvaluationSuite {id:$suite_id})
                ON CREATE SET suite.created_at=datetime(), suite.created_at_ts=timestamp()
                SET suite.name=$suite_name,
                    suite.updated_at=datetime(),
                    suite.updated_at_ts=timestamp()
                WITH suite
                CREATE (run:EvaluationRun {id:$run_id})
                SET run.status=$status,
                    run.agent_class=$agent_class,
                    run.score=$score,
                    run.metadata_json=$metadata_json,
                    run.created_at=datetime(),
                    run.created_at_ts=timestamp(),
                    run.updated_at=datetime(),
                    run.updated_at_ts=timestamp()
                MERGE (suite)-[:HAS_RUN]->(run)
                RETURN run.id AS id
                """,
                {
                    "suite_id": suite_id,
                    "suite_name": suite_name,
                    "run_id": run_id,
                    "status": status,
                    "agent_class": agent_class,
                    "score": score,
                    "metadata_json": json.dumps(metadata or {}),
                },
            ).single()
            return str(rec["id"])

    def upsert_evaluation_suite(
        self,
        name: str,
        agent_class: str,
        enabled: bool,
        cadence: str,
        threshold: float,
        description: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        suite_id = hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()[:16]
        with self._session() as s:
            rec = s.run(
                """
                MERGE (suite:EvaluationSuite {id:$id})
                ON CREATE SET suite.created_at=datetime(), suite.created_at_ts=timestamp()
                SET suite.name=$name,
                    suite.agent_class=$agent_class,
                    suite.enabled=$enabled,
                    suite.cadence=$cadence,
                    suite.threshold=$threshold,
                    suite.description=$description,
                    suite.metadata_json=$metadata_json,
                    suite.updated_at=datetime(),
                    suite.updated_at_ts=timestamp()
                RETURN suite.id AS id
                """,
                {
                    "id": suite_id,
                    "name": name,
                    "agent_class": agent_class,
                    "enabled": enabled,
                    "cadence": cadence,
                    "threshold": threshold,
                    "description": description,
                    "metadata_json": json.dumps(metadata or {}),
                },
            ).single()
            return str(rec["id"])

    def list_evaluation_suites(self, limit: int = 200, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        with self._session() as s:
            q = "MATCH (suite:EvaluationSuite) "
            params: Dict[str, Any] = {"limit": limit}
            if enabled is not None:
                q += "WHERE suite.enabled=$enabled "
                params["enabled"] = enabled
            q += (
                "RETURN suite "
                "ORDER BY coalesce(suite.updated_at_ts, suite.created_at_ts, 0) DESC "
                "LIMIT $limit"
            )
            res = s.run(q, params)
            return [dict(r["suite"]) for r in res]

    def list_evaluation_runs(self, limit: int = 100, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._session() as s:
            q = (
                "MATCH (suite:EvaluationSuite)-[:HAS_RUN]->(run:EvaluationRun) "
            )
            params: Dict[str, Any] = {"limit": limit}
            if status:
                q += "WHERE run.status=$status "
                params["status"] = status
            q += (
                "RETURN run, suite "
                "ORDER BY coalesce(run.created_at_ts, run.updated_at_ts, 0) DESC "
                "LIMIT $limit"
            )
            res = s.run(q, params)
            items: List[Dict[str, Any]] = []
            for row in res:
                run = dict(row["run"])
                suite = dict(row["suite"])
                run["suite_id"] = suite.get("id")
                run["suite_name"] = suite.get("name")
                items.append(run)
            return items

    def upsert_workflow_budget(
        self,
        workflow_id: str,
        token_budget: Optional[int] = None,
        time_budget_s: Optional[int] = None,
        retry_budget: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        budget_id = f"budget:{workflow_id}"
        with self._session() as s:
            rec = s.run(
                """
                MERGE (b:WorkflowBudget {id:$id})
                ON CREATE SET b.created_at=datetime(), b.created_at_ts=timestamp()
                SET b.workflow_id=$workflow_id,
                    b.token_budget=coalesce($token_budget, b.token_budget),
                    b.time_budget_s=coalesce($time_budget_s, b.time_budget_s),
                    b.retry_budget=coalesce($retry_budget, b.retry_budget),
                    b.metadata_json=$metadata_json,
                    b.updated_at=datetime(),
                    b.updated_at_ts=timestamp()
                WITH b
                OPTIONAL MATCH (t:Task {id:$workflow_id})
                FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END |
                    MERGE (t)-[:HAS_BUDGET]->(b)
                )
                RETURN b.id AS id
                """,
                {
                    "id": budget_id,
                    "workflow_id": workflow_id,
                    "token_budget": token_budget,
                    "time_budget_s": time_budget_s,
                    "retry_budget": retry_budget,
                    "metadata_json": json.dumps(metadata or {}),
                },
            ).single()
            return str(rec["id"])

    def create_workflow_incident(
        self,
        workflow_id: str,
        incident_type: str,
        severity: str = "warning",
        detail: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        incident_id = uuid.uuid4().hex
        with self._session() as s:
            rec = s.run(
                """
                CREATE (w:WorkflowIncident {id:$id})
                SET w.workflow_id=$workflow_id,
                    w.incident_type=$incident_type,
                    w.severity=$severity,
                    w.detail=$detail,
                    w.metadata_json=$metadata_json,
                    w.created_at=datetime(),
                    w.created_at_ts=timestamp(),
                    w.updated_at=datetime(),
                    w.updated_at_ts=timestamp()
                WITH w
                OPTIONAL MATCH (t:Task {id:$workflow_id})
                FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END |
                    MERGE (t)-[:HAS_INCIDENT]->(w)
                )
                RETURN w.id AS id
                """,
                {
                    "id": incident_id,
                    "workflow_id": workflow_id,
                    "incident_type": incident_type,
                    "severity": severity,
                    "detail": detail,
                    "metadata_json": json.dumps(metadata or {}),
                },
            ).single()
            return str(rec["id"])

    def list_workflow_incidents(self, workflow_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._session() as s:
            res = s.run(
                """
                MATCH (w:WorkflowIncident {workflow_id:$workflow_id})
                RETURN w
                ORDER BY coalesce(w.created_at_ts, w.updated_at_ts, 0) DESC
                LIMIT $limit
                """,
                {"workflow_id": workflow_id, "limit": limit},
            )
            return [dict(r["w"]) for r in res]

    # ---------- Intent Orchestrator ----------

    def get_unprocessed_intents(
        self,
        limit: int = 5,
        classifications: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        q = "MATCH (i:Intent) WHERE i.orchestrated_at IS NULL"
        params: Dict[str, Any] = {"limit": limit}
        if classifications:
            q += " AND coalesce(i.classification, 'unknown') IN $classifications"
            params["classifications"] = classifications
        q += " RETURN i ORDER BY i.created_at_ts ASC LIMIT $limit"
        with self._session() as s:
            res = s.run(q, params)
            return [dict(r["i"]) for r in res]

    def mark_intent_orchestrated(self, intent_id: str) -> None:
        with self._session() as s:
            s.run(
                "MATCH (i:Intent {id:$id}) "
                "SET i.orchestrated_at=datetime(), i.orchestrated_at_ts=timestamp(), "
                "    i.updated_at=datetime(), i.updated_at_ts=timestamp()",
                {"id": intent_id},
            ).consume()

    def upsert_requirement(
        self,
        text: str,
        epic_id: str,
        intent_id: Optional[str] = None,
    ) -> str:
        req_id = uuid.uuid4().hex
        with self._session() as s:
            s.run(
                """
                CREATE (r:Requirement {id:$id})
                SET r.text=$text,
                    r.source_intent_id=$intent_id,
                    r.created_at=datetime(),
                    r.created_at_ts=timestamp()
                WITH r
                MATCH (e:Task {id:$epic_id})
                MERGE (e)-[:HAS_REQUIREMENT]->(r)
                """,
                {"id": req_id, "text": text, "epic_id": epic_id, "intent_id": intent_id},
            ).consume()
        return req_id

    def get_epic_progress(self, epic_id: str) -> Dict[str, Any]:
        with self._session() as s:
            rec = s.run(
                """
                MATCH (e:Task {id:$epic_id})
                OPTIONAL MATCH (e)-[:HAS_CHILD*1..3]->(child:Task)
                WITH e, child
                RETURN
                    e.id AS epic_id,
                    e.status AS epic_status,
                    count(child) AS total_children,
                    sum(CASE WHEN child.status IN ['DONE','FAILED','CANCELLED'] THEN 1 ELSE 0 END) AS completed_children,
                    collect(DISTINCT child.id) AS child_ids
                """,
                {"epic_id": epic_id},
            ).single()
            if not rec:
                return {"epic_id": epic_id, "found": False}
            return dict(rec)

    def create_task_with_context(
        self,
        title: str,
        task_type: str = "task",
        status: str = "READY",
        kind: Optional[str] = None,
        parent_id: Optional[str] = None,
        required_capabilities: Optional[List[str]] = None,
        target_agent_id: Optional[str] = None,
        priority: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        context_query: Optional[str] = None,
        context_sources: Optional[List[str]] = None,
        auto_dispatch: bool = False,
    ) -> Dict[str, Any]:
        task_id = self.upsert_ticket(
            title=title,
            ticket_type=task_type,
            status=status,
            kind=kind or task_type,
            parent_id=parent_id,
            required_capabilities=required_capabilities,
            target_agent_id=target_agent_id,
            priority=priority,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        result: Dict[str, Any] = {"task_id": task_id, "context_packet_id": None, "dispatch_id": None}

        if context_query:
            packet = self.create_context_packet(
                query=context_query,
                task_id=task_id,
                max_items=30,
                include_sources=context_sources or ["memory", "knowledge", "orchestration"],
            )
            result["context_packet_id"] = packet.get("id")

        if auto_dispatch:
            dispatch = self.create_dispatch_with_paperclip(
                task_id=task_id,
                target={"capabilities": required_capabilities or ["terminal"]},
                priority=priority or "MEDIUM",
            )
            result["dispatch_id"] = dispatch.get("dispatch_id")
            result["target_device_id"] = dispatch.get("target_device_id")

        return result


# Back-compat with code that used `Neo(...)`
Neo = Neo4jClient
