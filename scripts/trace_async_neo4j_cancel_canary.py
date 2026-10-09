"""Explicit operator-run, READ-ONLY real Neo4j async cancellation research canary.

Refuses every host except the literal disposable Docker container hostname and
requires an isolated network and a fixed synthetic database. No writes. An
active Cypher scan is bounded by server-side per-query 3 second timeout.
"""
from __future__ import annotations

import asyncio
import os
import time
from neo4j import AsyncGraphDatabase, Query, READ_ACCESS

HOST = "assistx-neo526-physical-cancel-20261009"
URL = f"bolt://{HOST}:7687"
DATABASE = "syntheticcancel"
TAG = "OBS_RC1_SYNTHETIC_CANCEL_ONLY"


def check_environment():
    if os.getenv("ASSISTX_ISOLATED_NEO4J_CANCEL_CANARY") != "synthetic-explicit-opt-in":
        raise RuntimeError("explicit isolated test approval missing")
    if os.getenv("ASSISTX_TEST_TARGET_URI") != URL:
        raise RuntimeError("refusing target other than the dedicated disposable Neo4j")


async def find_transactions(driver):
    async with driver.session(database="system", default_access_mode=READ_ACCESS) as session:
        result = await session.run(
            "SHOW TRANSACTIONS YIELD transactionId,currentQuery,status "
            "WHERE currentQuery CONTAINS $marker "
            "RETURN transactionId,currentQuery,status", {"marker": TAG})
        return [dict(row) async for row in result]


async def read_while_cancellable(driver):
    async with driver.session(database=DATABASE, default_access_mode=READ_ACCESS,
                              fetch_size=64) as session:
        # The cross product is lazy, read-only and never returns until aggregated.
        # The server enforces timeout=3 seconds if cancellation does not work.
        statement = Query(
            f"/* {TAG} */ UNWIND range(1, 12000) AS x "
            "UNWIND range(1, 12000) AS y RETURN sum(x * y) AS total",
            timeout=3.0)
        res = await session.run(statement)
        summary = await res.consume()
        return summary


async def main():
    check_environment()
    driver = AsyncGraphDatabase.driver(URL, auth=None, connection_timeout=3,
                                       max_connection_pool_size=5)
    try:
        async with driver.session(database=DATABASE) as session:
            res = await session.run("RETURN 1 AS ready")
            assert (await res.single())["ready"] == 1
        print("DISPOSABLE_NEO4J_READ_READY", flush=True)
        started = time.monotonic()
        task = asyncio.create_task(read_while_cancellable(driver))
        found = False
        seen = []
        for _ in range(17):
            await asyncio.sleep(.075)
            if task.done():
                break
            hits = await find_transactions(driver)
            if hits:
                found = True
                seen = hits
                break
        print("TRANSACTION_VISIBLE_BEFORE_CANCEL",found,flush=True)
        if not found:
            if not task.done():
                task.cancel()
                try: await task
                except BaseException: pass
            raise AssertionError("query never visibly ran in disposable Neo4j")
        # Explicit physical client task cancellation (not merely result masking).
        task.cancel()
        canceled = False
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            canceled = True
        except Exception as exc:
            print("CANCEL_QUERY_EXCEPTION",type(exc).__name__,flush=True)
        print("ASYNC_TASK_CANCELLED",canceled,flush=True)
        vanished = False
        for _ in range(24):
            remaining = await find_transactions(driver)
            if not remaining:
                vanished = True
                break
            await asyncio.sleep(.075)
        print("SERVER_TRANSACTION_CLEARED",vanished,flush=True)
        print("ELAPSED_SECONDS",round(time.monotonic()-started,3),flush=True)
        if not (canceled and vanished):
            raise AssertionError("real server-side cancellation acceptance not established")
        print("REAL_NEO4J_ASYNC_CANCELLATION_PASS",flush=True)
    finally:
        await driver.close()


if __name__ == "__main__":
    asyncio.run(main())
