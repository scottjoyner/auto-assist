"""Unwired research bridge: quorum CAS token -> reviewed graph-entry controller.

The query gateway holds a pinned cluster/owner/application-term configuration.
Workers NEVER select those fields. A real Neo4j network/role isolation layer
and generation-aware closure witness remain mandatory before production.
"""
from __future__ import annotations

from trace_etcd_quorum_fence_research import EtcdQuorumFence, FenceRefused
from trace_graph_entry_research import Grant


class QuorumPlanGrantAdapter:
    """Implements ProtectedGraphEntry.Authority without any release pathway."""

    def __init__(self, quorum: EtcdQuorumFence, owner: str,
                 term: int, genesis: str):
        if not owner or type(term) is not int or term < 1 or not genesis:
            raise ValueError("INVALID_PINNED_GATEWAY_AUTHORITY")
        if not quorum.pinned_cluster_id:
            raise ValueError("REQUIRE_PINNED_RAFT_CLUSTER")
        self._quorum = quorum
        self._owner = owner
        self._term = term
        self._genesis = genesis

    def admit(self, operation_id: str, plan_id: str) -> Grant:
        if (not operation_id or not plan_id or
                not operation_id.isascii() or not plan_id.isascii()):
            raise FenceRefused("INVALID_GATEWAY_OPERATION")
        # The plan id is part of the quorum-persisted idempotency key.
        # Do not accept a raw worker query or caller-defined fencing token.
        operation = f"{plan_id}:{operation_id}"
        if len(operation) > 128:
            raise FenceRefused("INVALID_GATEWAY_OPERATION")
        view = self._quorum.snapshot()
        state = view.document
        if (view.cluster_id != self._quorum.pinned_cluster_id or
                state["genesis"] != self._genesis or
                state["owner"] != self._owner or
                state["term"] != self._term):
            raise FenceRefused("GATEWAY_EPOCH_TERM_OR_OWNER_STALE")
        # CAS commit BEFORE the graph/journal path can start an actual Bolt tx.
        token = self._quorum.admit(self._owner, self._term, operation)
        if not token:
            raise FenceRefused("MISSING_QUORUM_RESERVATION")
        # An opaque token is an id and release key, NOT a graph-issued permit.
        # No lease expiry or automatic graph-success release is supported.
        return Grant(reservation_id=token, token=token,
                     term=self._term, epoch=self._genesis)

    def admit_stable(self, operation_id: str, request_digest: str,
                     plan_id: str) -> Grant:
        # This path is separate from legacy UUID per-attempt admission.
        # Stable operation ID is principal+request scoped; do NOT prefix it
        # with plan ID, since reusing a request ID for a different plan must
        # be rejected as a request-digest conflict.
        import re
        if (not isinstance(operation_id, str) or
                not re.fullmatch(r"auth:[0-9a-f]{64}", operation_id)
                or not isinstance(request_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", request_digest)
                or not isinstance(plan_id, str)):
            raise FenceRefused("INVALID_STABLE_OPERATION")
        view = self._quorum.snapshot()
        state = view.document
        if (view.cluster_id != self._quorum.pinned_cluster_id
                or state["genesis"] != self._genesis
                or state["owner"] != self._owner
                or state["term"] != self._term):
            raise FenceRefused("GATEWAY_EPOCH_TERM_OR_OWNER_STALE")
        token = self._quorum.admit(
            self._owner, self._term, operation_id,
            request_digest=request_digest)
        return Grant(reservation_id=token, token=token,
                     term=self._term, epoch=self._genesis)

    def reconcile_operation(self, operation_id: str) -> dict:
        import re
        if (not isinstance(operation_id, str)
                or not re.fullmatch(r"auth:[0-9a-f]{64}", operation_id)):
            raise FenceRefused("INVALID_STABLE_OPERATION")
        state = self._quorum.reconcile_operation(operation_id)
        return {"state": state["state"]}
