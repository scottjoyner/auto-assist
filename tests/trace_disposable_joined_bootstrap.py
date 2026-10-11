"""Bootstrap exclusively disposable Raft on broker-only internal bridge."""
from __future__ import annotations
import json, os, sys, time
from trace_etcd_quorum_fence_research import EtcdQuorumFence, FenceRefused
from trace_disposable_raft_http import ResearchHTTP

endpoint, key, genesis, owner = sys.argv[1:5]
quorum = EtcdQuorumFence(ResearchHTTP(endpoint), key)
deadline = time.monotonic() + 22
while True:
    try:
        cluster = quorum.bootstrap(
            genesis, owner, b"\x01" * 32, b"\x02" * 32, capacity=3)
        break
    except FenceRefused:
        if time.monotonic() >= deadline:
            raise RuntimeError("DISPOSABLE_RAFT_BOOTSTRAP_FAILED") from None
        time.sleep(1)
print(json.dumps({"cluster_id": cluster, "scope": "DISPOSABLE_ONE_HOST_QUORUM_ONLY"}))
