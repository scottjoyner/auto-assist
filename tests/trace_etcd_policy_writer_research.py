"""Research-only least-privilege policy writer over Linux UNIX IPC.

A dedicated process would hold etcd's exact-key RBAC bearer writer token.
The graph gateway gets ONLY a local authenticated IPC client, not a raw
EtcdTLS/EtcdBearerTLS object. Policy is fixed on the server, not supplied by
workers. No production deployment, Neo4j privilege gate or OS user creation.
A same-UID process could still impersonate an authorized IPC caller: deploy
with distinct OS principals and a hardened socket namespace, not this test.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import socketserver
import struct

from trace_etcd_quorum_fence_research import EtcdQuorumFence, FenceRefused, canon
from trace_graph_entry_research import Grant

_OPERATION = re.compile(r"^[0-9a-f]{32}$")
_PLAN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
MAX_REQUEST = 4096


class PolicyRefused(RuntimeError):
    pass


class PolicyEngine:
    """The ONLY owner of the raw etcd write client in this experiment."""

    def __init__(self, authority: EtcdQuorumFence, owner: str, term: int,
                 genesis: str, allowed_plans: frozenset[str]):
        if (not authority.pinned_cluster_id or not owner or not genesis
                or type(term) is not int or term < 1 or
                not allowed_plans or
                not all(_PLAN.fullmatch(p) for p in allowed_plans)):
            raise ValueError("INVALID_PINNED_POLICY")
        self._authority = authority
        self._owner = owner
        self._term = term
        self._genesis = genesis
        self._plans = frozenset(allowed_plans)

    def dispatch(self, req: dict) -> dict:
        if not isinstance(req, dict):
            raise PolicyRefused("INVALID_POLICY_REQUEST")
        kind = req.get("kind")
        if kind == "admit" and set(req) == {
                "kind", "operation_id", "plan_id"}:
            plan = req["plan_id"]
            operation_id = req["operation_id"]
            if (not isinstance(plan, str) or plan not in self._plans
                    or not isinstance(operation_id, str)
                    or not _OPERATION.fullmatch(operation_id)):
                raise PolicyRefused("UNAPPROVED_GRAPH_PLAN_OR_OPERATION")
            view = self._authority.snapshot()
            state = view.document
            if (view.cluster_id != self._authority.pinned_cluster_id
                    or state["genesis"] != self._genesis
                    or state["owner"] != self._owner
                    or state["term"] != self._term):
                raise PolicyRefused("STALE_GATEWAY_OWNER_OR_GENESIS")
            # Application-level policy, signed takeover and witness gating
            # are enforced BEFORE any direct raw KV action by this service.
            token = self._authority.admit(
                self._owner, self._term, plan + ":" + operation_id)
            return {"reservation_id": token, "term": self._term,
                    "epoch": self._genesis}
        if kind == "close" and set(req) == {
                "kind", "token", "receipt", "signature"}:
            token = req["token"]
            sig = req["signature"]
            receipt = req["receipt"]
            if (not isinstance(token, str) or not _TOKEN.fullmatch(token)
                    or not isinstance(sig, str)
                    or not re.fullmatch(r"[0-9a-f]{128}", sig)
                    or not isinstance(receipt, dict)):
                raise PolicyRefused("INVALID_WITNESS_CLOSURE")
            # Validity, replay and exact binding are checked by Raft CAS.
            # An independent verifier must witness real physical absence;
            # this service never synthesizes or signs a closure receipt.
            self._authority.close_with_witness(
                token, receipt, bytes.fromhex(sig))
            return {"closed": True}
        if kind == "status" and set(req) == {"kind"}:
            view = self._authority.snapshot()
            return {"owner": view.document["owner"],
                    "term": view.document["term"],
                    "pending": len(view.document["pending"])}
        raise PolicyRefused("UNSUPPORTED_POLICY_ACTION")


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


class _Request(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            peer = self.request.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED,
                struct.calcsize("3i"))
            _, uid, _ = struct.unpack("3i", peer)
            if uid != self.server.allowed_uid:
                raise PolicyRefused("UNAUTHORIZED_UNIX_PEER")
            message = self.rfile.readline(MAX_REQUEST + 1)
            if (len(message) > MAX_REQUEST or not message.endswith(b"\n")):
                raise PolicyRefused("OVERSIZED_OR_TRUNCATED_REQUEST")
            req = json.loads(message)
            result = self.server.engine.dispatch(req)
            output = {"ok": result}
        except (PolicyRefused, FenceRefused,
                ValueError, TypeError, UnicodeDecodeError) as exc:
            # Never disclose exception repr: transport errors may contain
            # credentials, server endpoint data or sensitive grant state.
            output = {"denied": (
                str(exc) if isinstance(exc, PolicyRefused)
                else "POLICY_QUORUM_UNAVAILABLE_OR_UNCERTAIN"
            )}
        except Exception:
            output = {"denied": "POLICY_INTERNAL_FAILURE"}
        try:
            self.wfile.write(canon(output) + b"\n")
        except OSError:
            pass


class UnixPolicyWriterServer:
    """Bound to a fresh, private directory created by its caller."""

    def __init__(self, path: str | Path, engine: PolicyEngine,
                 allowed_uid: int):
        path = Path(path)
        if (not path.parent.is_dir() or
                path.parent.stat().st_mode & 0o077 or
                path.exists() or type(allowed_uid) is not int):
            raise ValueError("REQUIRE_PRIVATE_FRESH_UNIX_SOCKET")
        self.path = path
        self._server = _Server(str(path), _Request)
        self._server.engine = engine
        self._server.allowed_uid = allowed_uid
        os.chmod(path, 0o600)

    def serve_forever(self):
        self._server.serve_forever(poll_interval=0.1)

    def shutdown(self):
        self._server.shutdown()
        self._server.server_close()
        self.path.unlink(missing_ok=True)


class UnixPolicyClient:
    """No raw KV API, TLS bearer token, operator key, or release privilege."""

    def __init__(self, path: str | Path):
        self.path = str(path)

    def request(self, request: dict) -> dict:
        if not isinstance(request, dict):
            raise PolicyRefused("INVALID_CLIENT_REQUEST")
        encoded = canon(request) + b"\n"
        if len(encoded) > MAX_REQUEST:
            raise PolicyRefused("REQUEST_EXCEEDS_IPC_BOUND")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(3)
                sock.connect(self.path)
                sock.sendall(encoded)
                reply = bytearray()
                while len(reply) <= MAX_REQUEST:
                    char = sock.recv(1)
                    if not char:
                        break
                    reply.extend(char)
                    if char == b"\n":
                        break
            response = json.loads(reply)
        except (OSError, TimeoutError, ValueError) as exc:
            raise PolicyRefused("WRITER_UNAVAILABLE_OUTCOME_UNCERTAIN") from None
        if not isinstance(response, dict) or set(response) not in (
                {"ok"}, {"denied"}):
            raise PolicyRefused("INVALID_WRITER_RESPONSE")
        if "denied" in response:
            raise PolicyRefused(str(response["denied"]))
        return response["ok"]


class UnixPolicyGrantAdapter:
    """Drop-in ProtectedGraphEntry.Authority backed by policy IPC."""

    def __init__(self, client: UnixPolicyClient, epoch: str):
        if not epoch:
            raise ValueError("MISSING_PINNED_GENESIS")
        self._client = client
        self._epoch = epoch

    def admit(self, operation_id: str, plan_id: str) -> Grant:
        result = self._client.request({
            "kind": "admit", "operation_id": operation_id,
            "plan_id": plan_id})
        if (not isinstance(result, dict)
                or set(result) != {"reservation_id", "term", "epoch"}
                or result["epoch"] != self._epoch
                or type(result["term"]) is not int
                or result["term"] < 1
                or not isinstance(result["reservation_id"], str)
                or not _TOKEN.fullmatch(result["reservation_id"])):
            # Maybe the server occupied the slot; never assume otherwise.
            raise PolicyRefused("INVALID_SERVER_GRANT_RESERVATION_HELD")
        return Grant(result["reservation_id"], result["reservation_id"],
                     result["term"], result["epoch"])
