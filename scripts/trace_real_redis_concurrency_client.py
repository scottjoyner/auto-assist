"""Physical Redis 7 atomic quota contention: 1/3/5/10 synthetic workers.

Runs only inside a newly created internal Docker network, on fresh
non-persistent Redis and with synthetic-only receiver-owned HMAC material.
NO real Neo4j query, FastAPI listener, provider call or credential use.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import sys
import threading
import types

import redis

if os.getenv("ASSISTX_DISPOSABLE_REDIS_CONTENTION") != "synthetic-explicit-opt-in":
    raise RuntimeError("no operator approval for isolated contention test")
if os.getenv("ASSISTX_TEST_REDIS_HOST") != "assistx-trace-contention-redis-20261009":
    raise RuntimeError("unapproved Redis target")

package = types.ModuleType("assistx")
package.__path__ = ["/work/src/assistx"]
sys.modules["assistx"] = package
from assistx.trace_index_fenced_research import (  # noqa: E402
    Lease, Policy, acquire, release,
)

HOST = "assistx-trace-contention-redis-20261009"
SECRET = "synthetic-only-not-production-key-trace-contention-20261009"
KEY = "traceidx:{assistx-trace-index-v2}:active:fleet"
POLICY = Policy(
    principal_rate=30, fleet_rate=60, window_seconds=60,
    principal_inflight=2, fleet_inflight=3, lease_seconds=45,
)
client = redis.Redis(
    host=HOST, port=6379, socket_connect_timeout=2,
    socket_timeout=2, decode_responses=False,
)


def run_round(size: int) -> None:
    start = threading.Barrier(size + 1, timeout=8)

    def worker(index: int):
        start.wait()
        return acquire(
            client, "synthetic-participant-" + str(size) + "-" + str(index),
            SECRET, POLICY,
        )

    with ThreadPoolExecutor(max_workers=size) as pool:
        futures = [pool.submit(worker, i) for i in range(size)]
        start.wait()
        decisions = [future.result(timeout=9) for future in futures]

    admitted = [d.lease for d in decisions if d.allowed]
    denied = [d for d in decisions if not d.allowed]
    target = min(3, size)
    try:
        assert len(admitted) == target, "wrong admitted in-flight count"
        assert len(denied) == size - target, "wrong deny count"
        assert all(d.reason == "fleet_inflight" for d in denied), (
            "unexpected denial reason; quota tests inconclusive"
        )
        assert all(d.retry_after_seconds >= 1 for d in denied)
        assert all(isinstance(lease, Lease) for lease in admitted)
        assert client.zcard(KEY) == target, "actual Redis fleet occupancy wrong"

        if admitted:
            original = admitted[0]
            forged = Lease(
                nonce="0" * 32,
                per_active=original.per_active,
                fleet_active=original.fleet_active,
                lease_seconds=original.lease_seconds,
            )
            assert release(client, forged) is False, (
                "forged/stale release must not free physical capacity"
            )
            assert client.zcard(KEY) == target
    finally:
        for lease in admitted:
            if lease is None or not release(client, lease):
                raise RuntimeError("owned lease cleanup unavailable")
    assert client.zcard(KEY) == 0, "synthetic Redis slot leaked"
    print(
        "SYNTHETIC_WORKERS", size, "ADMITTED", target,
        "DENIED", size - target, "CAPACITY", 3, flush=True,
    )


def main() -> None:
    assert client.ping()
    initial_id = client.info(section="server")["run_id"]
    assert isinstance(initial_id, str) and len(initial_id) == 40
    assert client.zcard(KEY) == 0, "contaminated scratch Redis"
    for n in (1, 3, 5, 10):
        run_round(n)
    assert client.info(section="server")["run_id"] == initial_id, (
        "Redis process restarted during experiment"
    )
    assert client.zcard(KEY) == 0
    print("REAL_REDIS_1_3_5_10_CONCURRENCY_FENCE_PASS", flush=True)
    print("PRODUCTION_QUERIES_OR_CREDENTIALS_USED=false", flush=True)


if __name__ == "__main__":
    main()
