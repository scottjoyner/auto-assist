"""Disposable Redis-restart / real Neo4j async-cancellation acceptance client.

Must run ONLY within the approved internal-only synthetic Docker experiment.
This code NEVER uses the real AssistX graph, Redis, API, providers or keys.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import time
import types

import redis
from neo4j import AsyncGraphDatabase, Query, READ_ACCESS

if os.getenv("ASSISTX_DISPOSABLE_REDIS_NEO_CANARY") != "synthetic-explicit-opt-in":
    raise RuntimeError("disposable-only explicit opt-in is required")
NEO = "assistx-neo-redis-fence-isolated-20261009"
REDIS = "assistx-redis-neo-fence-isolated-20261009"
URL = f"bolt://{NEO}:7687"
TAG = "ASSISTX_REDIS_LOSS_PHYSICAL_CANCEL_SYNTHETIC"
if os.getenv("ASSISTX_TEST_TARGET_URI") != URL:
    raise RuntimeError("unexpected Neo4j target")

# Avoid AssistX application import side effects: pure research modules only.
package = types.ModuleType("assistx")
package.__path__ = ["/work/src/assistx"]
sys.modules["assistx"] = package
from assistx.trace_fenced_staging_adapter import (
    FencedReadUnavailable, StagingParameters, run_staging_fenced_read,
)

state = {
    "visible_before_restart": False,
    "server_transaction_cleared": False,
    "driver_task_cancelled": False,
    "cancel_callback_called": False,
}


async def active(driver):
    async with driver.session(database="system", default_access_mode=READ_ACCESS) as s:
        records = await s.run(
            "SHOW TRANSACTIONS YIELD transactionId,currentQuery,status "
            "WHERE currentQuery CONTAINS $tag "
            "RETURN transactionId,status",
            {"tag": TAG},
        )
        return [dict(row) async for row in records]


async def expensive_read(driver):
    async with driver.session(database="neo4j", default_access_mode=READ_ACCESS) as s:
        query = Query(
            f"/* {TAG} */ UNWIND range(1, 12000) AS x "
            "UNWIND range(1, 12000) AS y RETURN sum(x * y) AS total",
            timeout=3.0,
        )
        result = await s.run(query)
        return await result.consume()


async def read_real_graph():
    driver = AsyncGraphDatabase.driver(
        URL, auth=None, connection_timeout=3, max_connection_pool_size=5,
    )
    try:
        async with driver.session(database="neo4j") as s:
            sanity = await s.run("RETURN 1 AS ready")
            assert (await sanity.single())["ready"] == 1
        loop = asyncio.get_running_loop()
        query_task = asyncio.create_task(expensive_read(driver))
        state["loop"] = loop
        state["query_task"] = query_task
        try:
            for _ in range(28):
                await asyncio.sleep(0.06)
                if query_task.done():
                    break
                if await active(driver):
                    state["visible_before_restart"] = True
                    print("REAL_GRAPH_TRANSACTION_VISIBLE", flush=True)
                    break
            if not state["visible_before_restart"]:
                raise RuntimeError("disposable graph transaction not observed")
            await query_task
            raise RuntimeError("graph query unexpectedly completed before Redis restart")
        except asyncio.CancelledError:
            state["driver_task_cancelled"] = True
            for _ in range(32):
                if not await active(driver):
                    state["server_transaction_cleared"] = True
                    break
                await asyncio.sleep(0.05)
            raise
        finally:
            if not query_task.done():
                query_task.cancel()
                try:
                    await query_task
                except BaseException:
                    pass
    finally:
        await driver.close()


def main() -> None:
    client = redis.Redis(host=REDIS, port=6379, socket_connect_timeout=2,
                         socket_timeout=2, decode_responses=False)
    assert client.ping() is True
    run_id = client.info(section="server")["run_id"]
    assert isinstance(run_id, str) and len(run_id) == 40

    def on_cancel():
        state["cancel_callback_called"] = True
        loop = state.get("loop")
        task = state.get("query_task")
        if loop is None or task is None or loop.is_closed():
            raise RuntimeError("real async graph cancellation target unavailable")
        loop.call_soon_threadsafe(task.cancel)

    rejected = False
    try:
        run_staging_fenced_read(
            redis_client=client,
            principal="synthetic-real-redis-neo-reader",
            receiver_key="synthetic-only-receiver-owned-rotation-free-key-0001",
            redis_run_id_pin=run_id,
            query=lambda _event: asyncio.run(read_real_graph()),
            request_cancel=on_cancel,
            parameters=StagingParameters(
                heartbeat_seconds=0.20, watchdog_join_seconds=1.5
            ),
        )
    except FencedReadUnavailable:
        rejected = True

    print("RESPONSE_DENIED", rejected, flush=True)
    print("PHYSICAL_QUERY_VISIBLE", state["visible_before_restart"], flush=True)
    print("DRIVER_TASK_CANCELLED", state["driver_task_cancelled"], flush=True)
    print("SERVER_TRANSACTION_CLEARED", state["server_transaction_cleared"], flush=True)
    print("CANCEL_CALLBACK_CALLED", state["cancel_callback_called"], flush=True)
    assert all((
        rejected, state["visible_before_restart"],
        state["driver_task_cancelled"],
        state["server_transaction_cleared"], state["cancel_callback_called"],
    ))
    print("REAL_REDIS_RESTART_NEO4J_CANCEL_SYNTHETIC_PASS", flush=True)


if __name__ == "__main__":
    main()
