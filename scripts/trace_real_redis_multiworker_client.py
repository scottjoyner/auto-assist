"""Opt-in real Redis 1/3/5/10 concurrent atomic admission witness.

No Neo4j query, API endpoint, real principal, provider call or production key.
Must run inside disposable --internal Docker network with temporary Redis only.
"""
from __future__ import annotations

import concurrent.futures
import os
import sys
import threading
import time
import types

import redis

HOST = "assistx-redis-mw-fence-isolated-20261009"
if os.getenv("ASSISTX_DISPOSABLE_REDIS_MULTIWORKER") != "synthetic-explicit-opt-in":
    raise RuntimeError("explicit isolated research opt-in required")
if os.getenv("ASSISTX_TEST_REDIS_HOST") != HOST:
    raise RuntimeError("unapproved test Redis endpoint")
package = types.ModuleType("assistx")
package.__path__ = ["/work/src/assistx"]
sys.modules["assistx"] = package
from assistx import trace_index_fenced_research as model

FLEET_MAX = 3
SECRET = "synthetic-receiver-only-hmac-key-never-live-000000"
POLICY = model.Policy(
    principal_rate=20, fleet_rate=90, window_seconds=60,
    principal_inflight=2, fleet_inflight=FLEET_MAX, lease_seconds=20,
)


def batch(db: redis.Redis, pin: str, total: int) -> dict[str, int]:
    start = threading.Barrier(total + 1)
    release_gate = threading.Event()
    lock = threading.Condition()
    decided: list[str] = []
    release_results: list[bool] = []

    def worker(index: int) -> str:
        start.wait(timeout=4)
        if db.info(section="server").get("run_id") != pin:
            raise RuntimeError("Redis generation changed")
        principal = f"synthetic-read-{total}-{index}"
        choice = model.acquire(db, principal, SECRET, POLICY)
        with lock:
            decided.append("admitted" if choice.allowed else choice.reason)
            lock.notify_all()
        if not choice.allowed:
            return "denied"
        if choice.lease is None:
            raise RuntimeError("grant missing exact nonce")
        if not release_gate.wait(timeout=9):
            raise RuntimeError("acceptance controller failed to release reads")
        if db.info(section="server").get("run_id") != pin:
            raise RuntimeError("Redis generation changed before release")
        confirmed = model.release(db, choice.lease)
        with lock:
            release_results.append(confirmed)
        return "released"

    with concurrent.futures.ThreadPoolExecutor(max_workers=total) as pool:
        futures = [pool.submit(worker, i) for i in range(total)]
        start.wait(timeout=4)
        try:
            deadline = time.monotonic() + 7
            with lock:
                while len(decided) < total:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise RuntimeError("not all Redis admission decisions observed")
                    lock.wait(timeout=left)
                observed = list(decided)
            # Authoritative Redis occupancy at peak, before allowing a release.
            occupied = db.zcard("traceidx:{assistx-trace-index-v2}:active:fleet")
            granted = observed.count("admitted")
            expected = min(total, FLEET_MAX)
            denied = total - granted
            if (occupied != expected or granted != expected or
                    any(result != "fleet_inflight" for result in observed if result != "admitted")):
                raise RuntimeError("atomic in-flight cap or denial reason violated")
            assert db.info(section="server")["run_id"] == pin
        finally:
            release_gate.set()
        outcomes = [f.result(timeout=10) for f in futures]
    if db.zcard("traceidx:{assistx-trace-index-v2}:active:fleet") != 0:
        raise RuntimeError("exact owned releases did not restore fleet capacity")
    if sum(release_results) != granted or outcomes.count("released") != granted:
        raise RuntimeError("lease release acknowledgment not confirmed")
    if outcomes.count("denied") != denied:
        raise RuntimeError("denial outcome mismatch")
    return {"attempted": total, "admitted": granted,
            "denied_inflight": denied, "peak_redis_slots": occupied}


def main() -> None:
    db = redis.Redis(host=HOST, port=6379,
                     socket_connect_timeout=2, socket_timeout=2,
                     decode_responses=True)
    if db.ping() is not True:
        raise RuntimeError("disposable Redis unavailable")
    pin = db.info(section="server").get("run_id")
    if not isinstance(pin, str) or len(pin) != 40:
        raise RuntimeError("unverified Redis boot ID")
    reports = []
    for total in (1, 3, 5, 10):
        report = batch(db, pin, total)
        reports.append(report)
        print("REAL_REDIS_CONCURRENT_ADMISSION",
              report["attempted"], report["admitted"],
              report["denied_inflight"], report["peak_redis_slots"], flush=True)
    assert [r["admitted"] for r in reports] == [1, 3, 3, 3]
    assert [r["denied_inflight"] for r in reports] == [0, 0, 2, 7]
    print("REAL_REDIS_1_3_5_10_ATOMIC_CAP_AND_RELEASE_PASS", flush=True)


if __name__ == "__main__":
    main()
