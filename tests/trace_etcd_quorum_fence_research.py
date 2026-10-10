"""Research-only etcd Raft quorum CAS fencing, no production admission wiring.

Uses etcd v3 JSON gateway over mTLS, default LINEARIZABLE reads and atomic
Txn CAS writes. A committed reservation is NEVER TTL-leased: it remains until
an exact external witness-signed closure. A signed human/operator takeover
requires zero unresolved attempts. Raft term != application fencing term.
The gateway and Neo4j do NOT yet enforce this service-issued permit.
"""
from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import ssl

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

VERSION = "assistx-etcd-fence-research-v1"


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def b64(value: bytes | str) -> str:
    return base64.b64encode(value.encode() if isinstance(value, str)
                            else value).decode()


class FenceRefused(RuntimeError):
    """Not authorized; never treat a failed response as a free reservation."""


class Kv(Protocol):
    def range(self, key: str) -> dict: ...
    def txn(self, request: dict) -> dict: ...


class EtcdTLS(Kv):
    def __init__(self, endpoint: str, ca: str, cert: str, key: str):
        if not endpoint.startswith("https://"):
            raise ValueError("REQUIRE_ETCD_TLS")
        self.endpoint = endpoint.rstrip("/")
        self.context = ssl.create_default_context(cafile=ca)
        self.context.load_cert_chain(cert, key)

    def _post(self, path: str, body: dict) -> dict:
        request = Request(
            self.endpoint + path, data=canon(body),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=3, context=self.context) as response:
                return json.loads(response.read(2_000_000))
        except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
            raise FenceRefused("QUORUM_UNAVAILABLE_OR_OUTCOME_UNCERTAIN") from None

    def range(self, key: str) -> dict:
        # Do NOT set serializable=true: the default must be quorum-linearizable.
        return self._post("/v3/kv/range", {"key": b64(key)})

    def txn(self, request: dict) -> dict:
        return self._post("/v3/kv/txn", request)


@dataclass(frozen=True)
class View:
    document: dict
    mod_revision: int
    cluster_id: str


class EtcdQuorumFence:
    """Serialized authoritative state under ONE Raft consensus namespace.

    Caller must pin cluster ID across restarts and restrict mTLS client keys.
    A successful response does not mean downstream graph work has terminated.
    """

    def __init__(self, kv: Kv, key: str, pinned_cluster_id: str | None = None):
        if not key.startswith("/assistx/research/fencing/") or len(key) > 180:
            raise ValueError("NOT_ISOLATED_RESEARCH_NAMESPACE")
        self.kv = kv
        self.key = key
        self.pinned_cluster_id = pinned_cluster_id

    def _cluster(self, response: dict) -> str:
        cluster = str(response.get("header", {}).get("cluster_id", ""))
        if not cluster:
            raise FenceRefused("CLUSTER_ID_MISSING")
        if self.pinned_cluster_id and cluster != self.pinned_cluster_id:
            raise FenceRefused("WRONG_RAFT_CLUSTER")
        return cluster

    def snapshot(self) -> View:
        response = self.kv.range(self.key)
        cluster = self._cluster(response)
        records = response.get("kvs", [])
        if len(records) != 1:
            raise FenceRefused("AUTHORITY_UNINITIALIZED_OR_AMBIGUOUS")
        row = records[0]
        try:
            document = json.loads(base64.b64decode(row["value"]))
            rev = int(row["mod_revision"])
            if (document["schema"] != VERSION or
                    not isinstance(document["term"], int) or
                    document["term"] < 1 or rev < 1 or
                    not isinstance(document["pending"], dict)):
                raise ValueError("not valid")
        except (ValueError, KeyError, TypeError) as exc:
            raise FenceRefused("CORRUPTED_AUTHORITY") from exc
        return View(document, rev, cluster)

    def bootstrap(self, genesis: str, owner: str, operator_public: bytes,
                  witness_public: bytes, capacity: int = 1) -> str:
        if (not genesis or not owner or type(capacity) is not int
                or not (1 <= capacity <= 16)
                or len(operator_public) != 32 or len(witness_public) != 32):
            raise FenceRefused("INVALID_GENESIS")
        document = {
            "schema": VERSION, "genesis": genesis, "owner": owner,
            "term": 1, "capacity": capacity, "pending": {},
            "operator_public": operator_public.hex(),
            "witness_public": witness_public.hex(),
        }
        response = self.kv.txn({
            "compare": [{"key": b64(self.key), "target": "VERSION",
                         "result": "EQUAL", "version": "0"}],
            "success": [{"request_put": {"key": b64(self.key),
                                        "value": b64(canon(document))}}],
            "failure": []
        })
        cluster = self._cluster(response)
        if response.get("succeeded") is not True:
            raise FenceRefused("AUTHORITY_ALREADY_EXISTS")
        self.pinned_cluster_id = cluster
        return cluster

    def _write(self, before: View, next_doc: dict) -> None:
        response = self.kv.txn({
            "compare": [{"key": b64(self.key), "target": "MOD",
                         "result": "EQUAL",
                         "mod_revision": str(before.mod_revision)}],
            "success": [{"request_put": {"key": b64(self.key),
                                        "value": b64(canon(next_doc))}}],
            "failure": [],
        })
        if self._cluster(response) != before.cluster_id:
            raise FenceRefused("WRONG_RAFT_CLUSTER")
        if response.get("succeeded") is not True:
            raise FenceRefused("CONCURRENT_AUTHORITY_UPDATE_REJECTED")

    def admit(self, owner: str, term: int, operation: str) -> str:
        if not isinstance(operation, str) or not operation or len(operation) > 128:
            raise FenceRefused("INVALID_OPERATION")
        prior = self.snapshot()
        state = prior.document
        if type(term) is not int or state["owner"] != owner or state["term"] != term:
            raise FenceRefused("STALE_TERM_OR_OWNER")
        if len(state["pending"]) >= state["capacity"]:
            raise FenceRefused("PHYSICAL_CAPACITY_OCCUPIED")
        if any(x.get("operation") == operation for x in state["pending"].values()):
            raise FenceRefused("DUPLICATE_OPERATION")
        token = secrets.token_hex(16)
        updated = json.loads(canon(state))
        updated["pending"][token] = {"operation": operation, "state": "RESERVED",
                                     "owner": owner, "term": term,
                                     "server": None, "generation": None,
                                     "txid": None}
        self._write(prior, updated)
        return token

    def bind(self, token: str, owner: str, term: int, server: str,
             generation: str, txid: str) -> None:
        if (not server or not generation or not isinstance(txid, str)
                or not txid.startswith("neo4j-transaction-")
                or not txid.removeprefix("neo4j-transaction-").isdigit()):
            raise FenceRefused("INVALID_PHYSICAL_BINDING")
        before = self.snapshot()
        doc = before.document
        pending = doc["pending"].get(token)
        if (doc["owner"] != owner or doc["term"] != term or
                pending is None or pending["state"] != "RESERVED" or
                pending["owner"] != owner or pending["term"] != term):
            raise FenceRefused("STALE_OR_AMBIGUOUS_BINDING")
        updated = json.loads(canon(doc))
        updated["pending"][token].update({
            "state": "STARTED", "server": server,
            "generation": generation, "txid": txid})
        self._write(before, updated)

    def close_with_witness(self, token: str, receipt: dict, signature: bytes) -> None:
        before = self.snapshot()
        doc = before.document
        pending = doc["pending"].get(token)
        if not pending or pending["state"] != "STARTED":
            raise FenceRefused("NO_VERIFIED_PHYSICAL_BINDING")
        expected = {
            "schema": "assistx-raft-closure-research-v1",
            "genesis": doc["genesis"], "term": pending["term"],
            "owner": pending["owner"], "token": token,
            "server": pending["server"], "generation": pending["generation"],
            "txid": pending["txid"], "observation": "external-verified-closure",
        }
        if receipt != expected or not isinstance(signature, bytes):
            raise FenceRefused("MISMATCHED_WITNESS_RECEIPT")
        try:
            Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(doc["witness_public"])
            ).verify(signature, canon(receipt))
        except (ValueError, InvalidSignature) as exc:
            raise FenceRefused("INVALID_WITNESS_SIGNATURE") from exc
        updated = json.loads(canon(doc))
        del updated["pending"][token]
        # Atomic revision protects against concurrent/replayed closure.
        self._write(before, updated)

    def takeover(self, new_owner: str, approval: dict, signature: bytes) -> int:
        before = self.snapshot()
        doc = before.document
        if doc["pending"]:
            raise FenceRefused("UNCERTAIN_PHYSICAL_WORK_BLOCKS_TAKEOVER")
        expected = {
            "schema": "assistx-raft-takeover-research-v1",
            "genesis": doc["genesis"],
            "cluster_id": before.cluster_id,
            "expected_term": doc["term"], "new_owner": new_owner,
        }
        if approval != expected or not isinstance(signature, bytes):
            raise FenceRefused("INVALID_OPERATOR_APPROVAL")
        if not new_owner or new_owner == doc["owner"]:
            raise FenceRefused("INVALID_SUCCESSOR")
        try:
            Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(doc["operator_public"])
            ).verify(signature, canon(approval))
        except (ValueError, InvalidSignature) as exc:
            raise FenceRefused("INVALID_OPERATOR_SIGNATURE") from exc
        updated = json.loads(canon(doc))
        updated["term"] = doc["term"] + 1
        updated["owner"] = new_owner
        self._write(before, updated)
        return updated["term"]


def witness_receipt(view: View, token: str) -> dict:
    attempt = view.document["pending"][token]
    return {
        "schema": "assistx-raft-closure-research-v1",
        "genesis": view.document["genesis"], "term": attempt["term"],
        "owner": attempt["owner"], "token": token,
        "server": attempt["server"], "generation": attempt["generation"],
        "txid": attempt["txid"], "observation": "external-verified-closure",
    }


def takeover_request(view: View, new_owner: str) -> dict:
    return {
        "schema": "assistx-raft-takeover-research-v1",
        "genesis": view.document["genesis"],
        "cluster_id": view.cluster_id,
        "expected_term": view.document["term"], "new_owner": new_owner,
    }
