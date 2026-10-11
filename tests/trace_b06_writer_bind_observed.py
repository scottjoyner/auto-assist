"""Research-only writer-side Raft binding from a separately observed Bolt row.

No network or signing grants are added to the graph observer. The privileged
disposable test coordinator copies observer evidence into the *separate*
writer, which already holds scoped exact-key mTLS/RBAC. This coordinator is
NOT independent trusted custody. The graph boot label is NOT attested.
No close_with_witness, signer, TTL, or capacity release is implemented.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys

from trace_etcd_bearer_rbac_research import EtcdBearerTLS
from trace_etcd_quorum_fence_research import EtcdQuorumFence, FenceRefused

ROOT = Path("/tmp")
_OBSERVED = ROOT / "b06-observer-active.json"
_TARGET = ROOT / "b06-original-target.json"
_CONFIG = ROOT / "writer-config.json"
_TXID = re.compile(r"neo4j-transaction-[0-9]+\Z")
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_KEYS = {
    "assistx_schema", "assistx_operation_id", "assistx_attempt_id",
    "assistx_reservation_id", "assistx_epoch", "assistx_term",
    "assistx_plan_id",
}


class BindingRefused(ValueError):
    pass


def checked_observation(target: dict, observation: dict, authority) -> tuple:
    """Only permit one exact RESERVED attempt; no caller-provided CAS permit."""
    if (not isinstance(target, dict) or
            set(target) != {"metadata", "graph_member", "graph_boot"} or
            not isinstance(observation, dict) or
            set(observation) != {"txid", "observed_row", "observed_sequence"} or
            observation["observed_sequence"] != 1 or
            type(observation["txid"]) is not str or
            not _TXID.fullmatch(observation["txid"]) or
            type(observation["observed_row"]) is not dict or
            set(observation["observed_row"]) != {
                "transactionId", "database", "metaData"} or
            observation["observed_row"]["transactionId"] != observation["txid"] or
            observation["observed_row"]["database"] != "neo4j" or
            type(observation["observed_row"]["metaData"]) is not dict or
            observation["observed_row"]["metaData"] != target["metadata"] or
            type(target["metadata"]) is not dict or
            set(target["metadata"]) != _KEYS):
        raise BindingRefused("UNTRUSTED_OBSERVED_TRANSACTION")
    m = target["metadata"]
    if (m["assistx_schema"] != "research-gateway-entry-v1" or
            m["assistx_plan_id"] != "get_task_read" or
            type(m["assistx_reservation_id"]) is not str or
            not _HEX.fullmatch(m["assistx_reservation_id"]) or
            m["assistx_attempt_id"] != "b06-" + m["assistx_reservation_id"] or
            type(m["assistx_term"]) is not int or
            type(m["assistx_operation_id"]) is not str or
            not re.fullmatch(r"auth:[0-9a-f]{64}", m["assistx_operation_id"]) or
            type(target["graph_member"]) is not str or
            type(target["graph_boot"]) is not str or
            target["graph_member"] != "disposable-neo4j" or
            target["graph_boot"] != "unattested-fixture-boot"):
        raise BindingRefused("UNTRUSTED_OR_UNATTESTED_BINDING_INPUT")
    view = authority.snapshot()
    doc = view.document
    reservation = doc["pending"].get(m["assistx_reservation_id"])
    if (doc["genesis"] != m["assistx_epoch"] or
            doc["term"] != m["assistx_term"] or
            reservation is None or
            reservation["state"] != "RESERVED" or
            reservation["operation"] != m["assistx_operation_id"] or
            reservation["owner"] != doc["owner"] or
            reservation["term"] != doc["term"] or
            reservation["server"] is not None or
            reservation["generation"] is not None or
            reservation["txid"] is not None):
        raise BindingRefused("NOT_ORIGINAL_UNBOUND_RESERVATION")
    # WARNING: This only binds *fixture-declared* graph identity; it may not
    # be used as physical-closure or server-identity authorization.
    return m["assistx_reservation_id"], doc["owner"], doc["term"]


def run(mode: str, target: dict, evidence: dict, authority) -> dict:
    if mode not in {"bad-txid", "bind", "replay"}:
        raise BindingRefused("UNREVIEWED_BIND_MODE")
    data = json.loads(json.dumps(evidence))
    if mode == "bad-txid":
        data["txid"] = "neo4j-transaction-999999999"
        try:
            checked_observation(target, data, authority)
        except BindingRefused:
            return {"forged_observation_denied": True,
                    "authoritative_state_unchanged": True}
        raise AssertionError("FORGED_TXID_ALLOWED")
    if mode == "replay":
        try:
            checked_observation(target, data, authority)
        except BindingRefused as exc:
            if str(exc) == "NOT_ORIGINAL_UNBOUND_RESERVATION":
                return {"duplicate_binding_denied": True}
            raise
        raise AssertionError("REPLAYED_BINDING_ALLOWED")
    token, owner, term = checked_observation(target, data, authority)
    authority.bind(token, owner, term, target["graph_member"],
                   target["graph_boot"], data["txid"])
    result = authority.snapshot().document["pending"][token]
    if (result["state"] != "STARTED" or
            result["txid"] != data["txid"] or
            result["server"] != target["graph_member"] or
            result["generation"] != target["graph_boot"]):
        raise RuntimeError("RAFT_BIND_DID_NOT_PERSIST_EXACT_TX")
    return {"exact_original_transaction_raft_bound": True,
            "state": "STARTED", "generation_attested": False,
            "signer_authorized": False, "release_authorized": False}


def main():
    conf = json.loads(_CONFIG.read_text())
    if set(conf) != {
            "writer_token", "raft_owner", "raft_term", "raft_genesis",
            "raft_key", "raft_cluster", "raft_tls_endpoint", "raft_bearer"}:
        raise BindingRefused("SCOPED_THREE_HOST_WRITER_REQUIRED")
    client = EtcdBearerTLS(
        conf["raft_tls_endpoint"], "/tmp/etcd-client/ca.pem",
        "/tmp/etcd-client/node.pem", "/tmp/etcd-client/node-key.pem",
        token=conf["raft_bearer"])
    authority = EtcdQuorumFence(client, conf["raft_key"],
                               pinned_cluster_id=conf["raft_cluster"])
    result = run(sys.argv[1], json.loads(_TARGET.read_text()),
                 json.loads(_OBSERVED.read_text()), authority)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
