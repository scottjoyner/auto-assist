"""DISPOSABLE / research-only graph broker for one AssistX get_task read slice.

Runs as a separate temporary Python container with two internal Docker bridges:
worker-facing and Neo4j-private. There is NO arbitrary Cypher/URL/route/role
selection, no write endpoint, no production import and NO Raft admission yet.
This is a network + least-privilege functionality canary, not a fence.
"""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

PLAN = "get_task_read"
CLAIM_PLAN = "get_task_claim_guard"
CYPHER = "MATCH (t:Task {id: $id}) RETURN t { .id, .status } AS task"
CLAIM_CYPHER = (
    "MATCH (t:Task {id: $id}) "
    "RETURN t { .id, .status, .payload, .payload_json, "
    ".queue_class, .required_capabilities } AS task"
)
STATIC_PLANS = {PLAN: CYPHER, CLAIM_PLAN: CLAIM_CYPHER}
TASK_ID = re.compile(r"^fixture-task-[A-Za-z0-9_-]{4,48}$")
MAX_REQUEST_BYTES = 2048
GRAPH_ENDPOINT = "http://neo4j:7474/db/neo4j/tx/commit"
CONFIG_PATH = os.getenv("ASSISTX_RESEARCH_BROKER_CONFIG", "/tmp/broker-config.json")
RAFT_KEYS = {
    "raft_endpoint", "raft_key", "raft_cluster", "raft_genesis",
    "raft_owner", "raft_term",
}


class JoinedRaftAdmission:
    """Disposable broker owns the Raft CAS, never trusts worker permits."""
    def __init__(self, config: dict):
        from trace_etcd_quorum_fence_research import EtcdQuorumFence
        from trace_disposable_raft_http import ResearchHTTP
        endpoint = config["raft_endpoint"]
        self.quorum = EtcdQuorumFence(
            ResearchHTTP(endpoint), config["raft_key"],
            pinned_cluster_id=config["raft_cluster"])
        self.genesis = config["raft_genesis"]
        self.owner = config["raft_owner"]
        self.term = config["raft_term"]
        if type(self.term) is not int or self.term != 1:
            raise ValueError("RESEARCH_RAFT_TERM_INVALID")

    def admit(self, key: str | None, plan: str, task_id: str):
        from trace_graph_stable_identity_research import (
            derive_stable_operation, InvalidStableRequest)
        from trace_etcd_quorum_fence_research import FenceRefused
        try:
            stable = derive_stable_operation(
                "fixture-authenticated-broker-principal", key, plan,
                {"id": task_id})
        except InvalidStableRequest:
            raise RequestDenied("INVALID_IDEMPOTENCY_KEY") from None
        view = self.quorum.snapshot()
        if (view.document["genesis"] != self.genesis
                or view.document["owner"] != self.owner
                or view.document["term"] != self.term):
            raise FenceRefused("STALE_RAFT_EPOCH_OR_OWNER")
        return self.quorum.admit(
            self.owner, self.term, stable.operation,
            request_digest=stable.request_digest)



class RequestDenied(Exception):
    pass


def validate(payload: object) -> str:
    if not isinstance(payload, dict) or set(payload) != {"plan_id", "parameters"}:
        raise RequestDenied("BAD_REQUEST_SHAPE")
    if not isinstance(payload["plan_id"], str) or (
            payload["plan_id"] not in STATIC_PLANS):
        raise RequestDenied("UNKNOWN_STATIC_PLAN")
    params = payload["parameters"]
    if not isinstance(params, dict) or set(params) != {"id"}:
        raise RequestDenied("INVALID_PARAMETERS")
    task_id = params["id"]
    if type(task_id) is not str or not TASK_ID.fullmatch(task_id):
        raise RequestDenied("INVALID_TASK_ID")
    return task_id


def decode_task_response(
    document: object, plan_id: str = PLAN,
) -> dict | None:
    # Fail closed: no streaming, no arbitrary metadata, no unexpected columns.
    if not isinstance(document, dict) or document.get("errors"):
        raise RequestDenied("GRAPH_QUERY_UNAVAILABLE")
    rows = document.get("results")
    if not isinstance(rows, list) or len(rows) != 1:
        raise RequestDenied("GRAPH_RESPONSE_INVALID")
    item = rows[0]
    if item.get("columns") != ["task"]:
        raise RequestDenied("GRAPH_RESPONSE_INVALID")
    data = item.get("data")
    if not isinstance(data, list) or len(data) > 1:
        raise RequestDenied("GRAPH_RESPONSE_INVALID")
    if not data:
        return None
    row = data[0].get("row")
    if not isinstance(row, list) or len(row) != 1:
        raise RequestDenied("GRAPH_RESPONSE_INVALID")
    task = row[0]
    if not isinstance(task, dict):
        raise RequestDenied("GRAPH_RESPONSE_INVALID")
    if plan_id == PLAN:
        allowed = {"id", "status"}
    elif plan_id == CLAIM_PLAN:
        allowed = {
            "id", "status", "payload", "payload_json", "queue_class",
            "required_capabilities",
        }
    else:
        raise RequestDenied("GRAPH_RESPONSE_UNKNOWN_PLAN")
    if (set(task) - allowed or
            type(task.get("id")) is not str or
            type(task.get("status")) is not str):
        raise RequestDenied("GRAPH_RESPONSE_UNAPPROVED_FIELDS")
    if plan_id == CLAIM_PLAN:
        # Never interpret a truncated policy response as ordinary work:
        # all policy keys must be present, even when source values are null.
        if set(task) != allowed:
            raise RequestDenied("GRAPH_RESPONSE_POLICY_FIELDS_MISSING")
        if (task.get("payload") is not None and
                type(task["payload"]) is not str):
            raise RequestDenied("GRAPH_RESPONSE_UNAPPROVED_FIELDS")
        if (task.get("payload_json") is not None and
                type(task["payload_json"]) is not str):
            raise RequestDenied("GRAPH_RESPONSE_UNAPPROVED_FIELDS")
        if (task.get("queue_class") is not None and
                type(task["queue_class"]) is not str):
            raise RequestDenied("GRAPH_RESPONSE_UNAPPROVED_FIELDS")
        capabilities = task.get("required_capabilities")
        if capabilities is not None and (
                type(capabilities) is not list or
                len(capabilities) > 32 or
                any(type(value) is not str or len(value) > 128
                    for value in capabilities)):
            raise RequestDenied("GRAPH_RESPONSE_UNAPPROVED_FIELDS")
    return task


def fetch_task(
    task_id: str, reader_user: str, reader_password: str,
    plan_id: str = PLAN,
) -> dict | None:
    if plan_id not in STATIC_PLANS:
        raise RequestDenied("UNKNOWN_STATIC_PLAN")
    body = json.dumps(
        {"statements": [{"statement": STATIC_PLANS[plan_id],
                         "parameters": {"id": task_id},
                         "resultDataContents": ["row"]}]},
        separators=(",", ":"),
    ).encode()
    token = base64.b64encode(
        (reader_user + ":" + reader_password).encode()).decode()
    request = urllib.request.Request(
        GRAPH_ENDPOINT, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "Authorization": "Basic " + token},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return decode_task_response(
                json.loads(response.read(32768)), plan_id)
    except RequestDenied:
        raise
    except (urllib.error.URLError, TimeoutError, OSError,
            ValueError, UnicodeError, json.JSONDecodeError):
        raise RequestDenied("GRAPH_QUERY_UNAVAILABLE") from None


class Handler(BaseHTTPRequestHandler):
    server_version = "ResearchStaticGraphBroker/1"

    def log_message(self, _fmt, *_args):
        # Never log bearer tokens, graph credential, request IDs or Cypher.
        pass

    def reply(self, status: int, value: dict) -> None:
        encoded = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "ready_research_only",
                             "production_authority": False})
            return
        self.reply(404, {"error": "NOT_FOUND"})

    def do_POST(self):
        if self.path != "/v1/plan":
            self.reply(404, {"error": "NOT_FOUND"})
            return
        # Caller cannot supply role, identity, Cypher, db URI, or auth headers
        # for the downstream graph. Separate fixture bearer required.
        from hmac import compare_digest
        actual = self.headers.get("Authorization", "")
        if not compare_digest(actual, "Bearer " + self.server.bearer):
            self.reply(401, {"error": "UNAUTHORIZED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            self.reply(400, {"error": "BAD_REQUEST"})
            return
        if not (0 < length <= MAX_REQUEST_BYTES):
            self.reply(413, {"error": "REQUEST_TOO_LARGE"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            task_id = validate(payload)
            if self.server.raft_admission is not None:
                # Crucial ordering: exact Raft CAS committed BEFORE graph
                # HTTP. No worker-provided grant/token/owner is accepted.
                try:
                    self.server.raft_admission.admit(
                        self.headers.get("Idempotency-Key"),
                        payload["plan_id"], task_id)
                except RequestDenied:
                    self.reply(422, {"error": "INVALID_IDEMPOTENCY_KEY"})
                    return
                except Exception as exc:
                    safe = {
                        "EXISTING_OPERATION_REQUIRES_RECONCILIATION",
                        "IDEMPOTENCY_KEY_PAYLOAD_CONFLICT",
                        "CLOSED_OPERATION_REPLAY_DENIED",
                    }
                    if str(exc) in safe:
                        self.reply(409, {"error": "ALREADY_SUBMITTED_OR_CONFLICT"})
                    else:
                        self.reply(503, {"error": "ADMISSION_UNCERTAIN"})
                    return
            task = fetch_task(
                task_id, self.server.reader_user,
                self.server.reader_password,
                payload["plan_id"],
            )
        except (RequestDenied, ValueError, UnicodeError) as exc:
            if isinstance(exc, RequestDenied) and str(exc).startswith("GRAPH_"):
                self.reply(503, {"error": "GRAPH_UNAVAILABLE"})
            else:
                self.reply(422, {"error": "INVALID_PLAN_OR_PARAMETERS"})
            return
        self.reply(200, {"task": task})


def main():
    config = json.loads(Path(CONFIG_PATH).read_text())
    primary = {"bearer", "reader_user", "reader_password"}
    if set(config) not in (primary, primary | RAFT_KEYS):
        raise RuntimeError("BAD_DISPOSABLE_CREDENTIAL_CONFIG")
    if not all(isinstance(config[k], str) and len(config[k]) >= 10
               for k in primary):
        raise RuntimeError("BAD_DISPOSABLE_CREDENTIAL_CONFIG")
    server = ThreadingHTTPServer(("0.0.0.0", 8808), Handler)
    server.raft_admission = (
        JoinedRaftAdmission(config) if RAFT_KEYS <= set(config) else None)
    server.bearer = config["bearer"]
    server.reader_user = config["reader_user"]
    server.reader_password = config["reader_password"]
    server.serve_forever(poll_interval=0.1)


if __name__ == "__main__":
    main()
