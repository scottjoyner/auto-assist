"""Explicit-only read-only real Neo4j cancellation induced by fake Redis lease loss.

Runs ONLY in a disposable, Docker internal network with an isolated 5.26.30
Neo4j database. Does not connect to real AssistX, Redis or production graph.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import types
from pathlib import Path

from neo4j import AsyncGraphDatabase, Query, READ_ACCESS

# The normal AssistX package initializer installs runtime services including
# a SQLite outbox. This research canary must import ONLY the two pure source
# modules; it must not initialize the real app, use external paths, or write.
if os.getenv("ASSISTX_ISOLATED_FENCED_NEO4J_CANARY") != "synthetic-explicit-opt-in":
    raise RuntimeError("disposable-only explicit opt-in required before imports")
package = types.ModuleType("assistx")
package.__path__ = [str(Path(__file__).resolve().parent / "src" / "assistx")]
sys.modules["assistx"] = package
from assistx import trace_fenced_staging_adapter as staging
from assistx import trace_index_fenced_research as model

HOST = "assistx-neo526-physical-cancel-20261009"
URL = f"bolt://{HOST}:7687"
DATABASE = "syntheticcancel"
TAG = "OBS_RC1_FENCED_RENEW_LOSS_SYNTHETIC"
SYNTHETIC_KEY = "synthetic-only-receiver-owned-key-do-not-use-in-fleet"


class SyntheticRedis:
    """Only injected fault responses; no Redis transport ever created."""
    def __init__(self):
        self.calls: list[str] = []
        self.nonce = None

    def eval(self, lua, key_count, *args):
        if lua == model.ACQUIRE_LUA:
            self.calls.append("ACQUIRE")
            self.nonce = args[-1]
            return [1, 0, 0]
        if lua == model.RENEW_LUA:
            self.calls.append("RENEW_DENIED")
            assert args[-2] == self.nonce
            return 0
        if lua == model.RELEASE_LUA:
            self.calls.append("RELEASE_ACK")
            assert args[-1] == self.nonce
            return 1
        raise AssertionError("unexpected Redis statement")


def verify_environment():
    if os.getenv("ASSISTX_ISOLATED_FENCED_NEO4J_CANARY") != "synthetic-explicit-opt-in":
        raise RuntimeError("explicitly approved research invocation required")
    if os.getenv("ASSISTX_TEST_TARGET_URI") != URL:
        raise RuntimeError("test target not the disposable Neo4j instance")


async def active_synthetic_transactions(driver):
    async with driver.session(database="system", default_access_mode=READ_ACCESS) as s:
        r = await s.run(
            "SHOW TRANSACTIONS YIELD transactionId,currentQuery,status "
            "WHERE currentQuery CONTAINS $tag RETURN transactionId,status",
            {"tag": TAG},
        )
        return [dict(row) async for row in r]


async def expensive_read(driver):
    async with driver.session(database=DATABASE, default_access_mode=READ_ACCESS,
                              fetch_size=64) as session:
        stmt = Query(
            f"/* {TAG} */ UNWIND range(1, 12000) AS x "
            "UNWIND range(1, 12000) AS y RETURN sum(x * y) AS total",
            timeout=3.0,
        )
        res = await session.run(stmt)
        return await res.consume()


async def physically_cancellable_read(state):
    driver = AsyncGraphDatabase.driver(URL, auth=None, connection_timeout=3,
                                       max_connection_pool_size=5)
    try:
        async with driver.session(database=DATABASE) as session:
            sanity = await session.run("RETURN 1 AS ready")
            assert (await sanity.single())["ready"] == 1
        long_task = asyncio.create_task(expensive_read(driver))
        state["loop"] = asyncio.get_running_loop()
        state["task"] = long_task
        try:
            for _ in range(22):
                await asyncio.sleep(.055)
                if long_task.done():
                    break
                hits = await active_synthetic_transactions(driver)
                if hits:
                    state["visible_before_cancel"] = True
                    break
            if not state["visible_before_cancel"]:
                raise AssertionError("live synthetic query was not observed before lease loss")
            await long_task
            raise AssertionError("heavy synthetic query completed without cancellation")
        except asyncio.CancelledError:
            state["driver_task_cancelled"] = True
            for _ in range(26):
                if not await active_synthetic_transactions(driver):
                    state["server_transaction_cleared"] = True
                    break
                await asyncio.sleep(.045)
            # Preserve CancelledError through the synchronous wrapper's release.
            raise
        finally:
            if not long_task.done():
                long_task.cancel()
                try:
                    await long_task
                except BaseException:
                    pass
    finally:
        await driver.close()


def main():
    verify_environment()
    db = SyntheticRedis()
    state = {"visible_before_cancel": False,
             "driver_task_cancelled": False,
             "server_transaction_cleared": False,
             "cancel_callback_calls": 0}
    started = time.monotonic()

    def query(_cancel_event):
        return asyncio.run(physically_cancellable_read(state))

    def request_cancel():
        state["cancel_callback_calls"] += 1
        loop = state.get("loop")
        task = state.get("task")
        if loop is None or task is None or loop.is_closed():
            raise RuntimeError("async query loop is not available")
        loop.call_soon_threadsafe(task.cancel)

    rejected = False
    try:
        staging.run_staging_fenced_read(
            redis_client=db,
            principal="synthetic-scoped-operator",
            receiver_key=SYNTHETIC_KEY,
            query=query,
            request_cancel=request_cancel,
            parameters=staging.StagingParameters(
                heartbeat_seconds=1.0, watchdog_join_seconds=1.0),
        )
    except staging.FencedReadUnavailable:
        rejected = True

    print("READ_RESULT_DENIED_AFTER_LOSS", rejected, flush=True)
    print("REDIS_FAULT_REACTIONS", ','.join(db.calls), flush=True)
    for k in ("visible_before_cancel", "driver_task_cancelled",
              "server_transaction_cleared", "cancel_callback_calls"):
        print(k.upper(), state[k], flush=True)
    print("ELAPSED_SECONDS", round(time.monotonic() - started, 3), flush=True)
    assert rejected
    assert db.calls == ["ACQUIRE", "RENEW_DENIED", "RELEASE_ACK"], db.calls
    assert state["visible_before_cancel"]
    assert state["driver_task_cancelled"]
    assert state["server_transaction_cleared"]
    assert state["cancel_callback_calls"] == 1
    print("LEASE_LOSS_REAL_NEO4J_CANCELLATION_PASS", flush=True)


if __name__ == "__main__":
    main()
