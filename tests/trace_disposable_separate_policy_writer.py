"""DISPOSABLE ONLY — fixed-plan, separately networked Raft policy writer.

The worker and graph broker NEVER receive a raw etcd endpoint or KV client.
Only this separate writer container attaches to the disposable Raft bridge.
Internal cleartext HTTP/Bearer is *not* production-grade service identity,
RBAC, multi-host quorum, or protected OS credential custody.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from hmac import compare_digest
import json
from pathlib import Path
import re

from trace_disposable_raft_http import ResearchHTTP
from trace_etcd_quorum_fence_research import EtcdQuorumFence, FenceRefused

ALLOWED_PLANS = frozenset({"get_task_read", "get_task_claim_guard"})
STABLE_ID = re.compile(r"^auth:[0-9a-f]{64}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")


class Policy:
    def __init__(self, conf):
        self.token = conf["writer_token"]
        self.owner = conf["raft_owner"]
        self.term = conf["raft_term"]
        self.genesis = conf["raft_genesis"]
        if (not isinstance(self.token, str) or len(self.token) < 24 or
                type(self.term) is not int or self.term != 1):
            raise ValueError("INVALID_DISPOSABLE_POLICY_CONFIG")
        self.quorum = EtcdQuorumFence(
            ResearchHTTP(conf["raft_endpoint"]), conf["raft_key"],
            pinned_cluster_id=conf["raft_cluster"])

    def admit(self, request):
        if (not isinstance(request, dict) or
                set(request) != {"plan_id", "operation_id", "request_digest"} or
                request.get("plan_id") not in ALLOWED_PLANS or
                not isinstance(request.get("operation_id"), str) or
                not STABLE_ID.fullmatch(request["operation_id"]) or
                not isinstance(request.get("request_digest"), str) or
                not DIGEST.fullmatch(request["request_digest"])):
            raise ValueError("UNRECOGNIZED_POLICY_REQUEST")
        view = self.quorum.snapshot()
        doc = view.document
        if (doc["genesis"] != self.genesis or
                doc["owner"] != self.owner or doc["term"] != self.term):
            raise FenceRefused("STALE_RAFT_EPOCH_OR_OWNER")
        return self.quorum.admit(
            self.owner, self.term, request["operation_id"],
            request_digest=request["request_digest"])


class Handler(BaseHTTPRequestHandler):
    server_version = "AssistXDisposablePolicyWriter/1"

    def log_message(self, *_args):
        # No request IDs, bearer tokens or raw graph/etcd addresses in logs.
        pass

    def reply(self, status, response):
        output = json.dumps(response, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(output)))
        self.end_headers()
        self.wfile.write(output)

    def do_POST(self):
        if self.path != "/v1/admit":
            self.reply(404, {"error": "NOT_FOUND"})
            return
        if not compare_digest(
                self.headers.get("Authorization", ""),
                "Bearer " + self.server.policy.token):
            self.reply(401, {"error": "UNAUTHORIZED"})
            return
        try:
            size = int(self.headers.get("Content-Length", "-1"))
            if not 0 < size <= 2048:
                self.reply(413, {"error": "OVERSIZED_OR_MISSING_BODY"})
                return
            payload = json.loads(self.rfile.read(size))
            token = self.server.policy.admit(payload)
        except ValueError:
            self.reply(422, {"error": "INVALID_POLICY_REQUEST"})
            return
        except FenceRefused as exc:
            if str(exc) in {
                    "EXISTING_OPERATION_REQUIRES_RECONCILIATION",
                    "IDEMPOTENCY_KEY_PAYLOAD_CONFLICT",
                    "CLOSED_OPERATION_REPLAY_DENIED"}:
                self.reply(409, {"error": "ALREADY_SUBMITTED_OR_CONFLICT"})
            else:
                self.reply(503, {"error": "ADMISSION_UNCERTAIN"})
            return
        except Exception:
            self.reply(503, {"error": "ADMISSION_UNCERTAIN"})
            return
        # An opaque reservation receipt is NOT a graph-server authorization
        # and is NEVER released on a successful HTTP or Neo4j response.
        self.reply(200, {"reserved": True, "reservation_id": token})


if __name__ == "__main__":
    conf = json.loads(Path("/tmp/writer-config.json").read_text())
    expected = {"writer_token", "raft_owner", "raft_term", "raft_genesis",
                "raft_endpoint", "raft_key", "raft_cluster"}
    if set(conf) != expected:
        raise SystemExit("INVALID_DISPOSABLE_WRITER_CONFIG")
    server = ThreadingHTTPServer(("0.0.0.0", 8810), Handler)
    server.policy = Policy(conf)
    server.serve_forever(poll_interval=0.1)
