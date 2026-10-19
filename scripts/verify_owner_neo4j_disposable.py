#!/usr/bin/env python3
"""Disposable native Neo4j concurrency/schema qualification (explicit opt-in).

Starts only an image ALREADY PRESENT locally; zero published host ports,
Docker --network none, no bind mounts, bounded CPU/memory/PIDs, and
automatic removal. Never contacts the configured production graph.

This verifies Cypher behavior and uniqueness under concurrent writers,
not the actual application driver/transaction or production safety.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from assistx.owner_identity import scoped_key

IMAGE="neo4j:5.23.0"
NAME="owner-contract-neo4j-disposable-20261019"
MIGRATION=ROOT/"migrations/20261018_task_owner_unique.cypher"


def cmd(args:list[str], *,timeout:int=12,input_text:str|None=None) -> str:
    result=subprocess.run(args,input=input_text,capture_output=True,text=True,
                          timeout=timeout,check=False)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args[:3])}: exit {result.returncode}: "
                           f"{result.stderr[-1800:]}; stdout={result.stdout[-450:]}")
    return result.stdout


def cypher(statement:str, *,timeout:int=14) -> str:
    return cmd(["docker","exec","-e","JAVA_TOOL_OPTIONS=-Xms16m -Xmx96m",
                "-i",NAME,"cypher-shell","--format","plain"],
               input_text=statement+"\n",timeout=timeout)


def reject_if_already_owned()->None:
    existing=cmd(["docker","ps","-a","--format","{{.Names}}"],timeout=8)
    if NAME in existing.splitlines():
        raise RuntimeError("NO_GO named container already exists; will not touch it")


def run_trials()->dict:
    reject_if_already_owned()
    check=cmd(["docker","image","inspect",IMAGE,"--format","{{.Id}}"],timeout=8)
    image_sha=check.strip()
    start=time.monotonic()
    subprocess.run([
        "docker","run","-d","--name",NAME,"--hostname","localhost",
        "--network","none","--memory","3g","--memory-swap","3g",
        "--cpus","2","--pids-limit","192",
        "--security-opt","no-new-privileges:true",
        "-e","NEO4J_AUTH=none",
        "-e","NEO4J_server_memory_heap_initial__size=256m",
        "-e","NEO4J_server_memory_heap_max__size=512m",
        "-e","NEO4J_server_memory_pagecache_size=128m",
        IMAGE,
    ],capture_output=True,text=True,timeout=15,check=True)
    try:
        ready=False
        for attempt in range(24):
            try:
                if "1" in cypher("RETURN 1 AS ok;",timeout=6):
                    ready=True
                    break
            except (RuntimeError,subprocess.TimeoutExpired):
                pass
            time.sleep(2)
        if not ready:
            diagnostic=subprocess.run(["docker","logs","--tail","35",NAME],
                capture_output=True,text=True,timeout=8,check=False)
            raise RuntimeError(
                "NO_GO disposable Neo4j failed health deadline: "
                +(diagnostic.stdout+"\n"+diagnostic.stderr)[-900:]
            )

        pre=cypher("SHOW CONSTRAINTS YIELD name WHERE name = "
                   "'task_owner_idempotency_unique' RETURN count(*) = 0 AS absent;")
        if "true" not in pre.lower():
            raise RuntimeError("NO_GO unexpected preexisting uniqueness constraint")
        migration=MIGRATION.read_text()
        if "CREATE CONSTRAINT task_owner_idempotency_unique" not in migration:
            raise RuntimeError("NO_GO migration file changed unexpectedly")
        cypher(migration)
        schema=cypher("SHOW CONSTRAINTS YIELD name,type,labelsOrTypes,properties "
                      "WHERE name='task_owner_idempotency_unique' "
                      "RETURN name,type,labelsOrTypes,properties;")
        if not all(s in schema for s in ("task_owner_idempotency_unique","UNIQUENESS",
                                       "Task","owner_idempotency_key")):
            raise RuntimeError("NO_GO incorrect native constraint shape")

        a={"owner_issuer":"test-idp","owner_subject":"disposable-person-a"}
        b={"owner_issuer":"test-idp","owner_subject":"disposable-person-b"}
        k_a=scoped_key(a,"same-client-key")
        k_b=scoped_key(b,"same-client-key")
        assert k_a!=k_b

        def write(owner:dict[str,str],key:str,nonce:str) -> str:
            # Mirrors the exact CREATE-only transaction's MERGE property and
            # ON CREATE semantics; test data are fixed synthetic strings.
            q=(f"MERGE (t:Task {{owner_idempotency_key:'{key}'}}) "
               f"ON CREATE SET t.id='{nonce}', t.creation_nonce='{nonce}', "
               f"t.owner_issuer='{owner['owner_issuer']}', "
               f"t.owner_subject='{owner['owner_subject']}' "
               "RETURN t.id AS task_id;")
            return cypher(q,timeout=18)

        with ThreadPoolExecutor(max_workers=2) as pool:
            inputs=[(a,k_a,"a-"+str(i)) for i in range(6)]
            inputs.extend([(b,k_b,"b-"+str(i)) for i in range(3)])
            handles=[pool.submit(write,*args) for args in inputs]
            results=[future.result(timeout=25) for future in handles]
        for result in results:
            if "task_id" not in result:
                raise RuntimeError("NO_GO missing Cypher write result")

        final=cypher("MATCH (t:Task) WITH count(t) AS n,"
                     "count(DISTINCT t.owner_idempotency_key) AS keys "
                     "RETURN (n=2 AND keys=2) AS exact_cardinality;")
        if "true" not in final.lower():
            raise RuntimeError("NO_GO unexpected final task cardinality "+final[:200])
        repeated=cypher(f"MATCH (t:Task {{owner_idempotency_key:'{k_a}'}}) "
                        "RETURN t.id AS task_id,t.owner_subject AS owner_subject;")
        if "disposable-person-a" not in repeated:
            raise RuntimeError("NO_GO owner changed in same-key retries")

        return {"result":"PASS","container_removed_on_exit":True,
                "image_sha256":image_sha,"network":"none","ports_published":0,
                "concurrent_writes":len(results),"distinct_owners":2,
                "distinct_owner_scoped_tasks":2,
                "same_owner_idempotent_writes":6,
                "elapsed_seconds":round(time.monotonic()-start,2)}
    finally:
        # Remove only the unique known container created above.
        removed=subprocess.run(["docker","rm","-f",NAME],capture_output=True,
                               text=True,timeout=12,check=False)
        if removed.returncode:
            raise RuntimeError("NO_GO disposable container cleanup failed: "
                               +removed.stderr[-260:])


def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--run",action="store_true",help="start disposable isolated Neo4j")
    args=ap.parse_args()
    if not args.run:
        print("PLAN_ONLY: --run is required; no container started")
        return 0
    print(json.dumps(run_trials(),sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
