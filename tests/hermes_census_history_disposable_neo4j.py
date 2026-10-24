#!/usr/bin/env python3
"""Disposable local Neo4j census negative witness; never production data."""
from __future__ import annotations

if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("run only in synthetic disposable Neo4j CI")

import importlib.util
import os
from pathlib import Path
import time
import uuid
from neo4j import GraphDatabase

assert os.environ.get("HERMES_DISPOSABLE_NEO4J")=="synthetic-ci-only"
assert "NEO4J_URI" not in os.environ
src=Path(__file__).resolve().parents[1]/"src/assistx/hermes_claim_census.py"
assert src.is_file()
spec=importlib.util.spec_from_file_location("hermes_synthetic_census",src)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

driver=GraphDatabase.driver("bolt://127.0.0.1:7687",auth=None,
                            connection_timeout=5)
for i in range(60):
    try:
        driver.verify_connectivity()
        break
    except Exception:
        if i==59:raise
        time.sleep(1)

prefix="SYNTHETIC_HISTORY_"+uuid.uuid4().hex[:12]
def run(q,**params):
    with driver.session(database="neo4j") as s:
        s.run(q,params).consume()
def session():
    return driver.session(database="neo4j")

fields=("completed_by","heartbeat_by","failed_by",
        "paused_from_agent_id","migration_target_agent_id",
        "passive_claim_agent_id","last_passive_claim_agent_id")
try:
    run("""
        UNWIND range(0,620) AS i
        CREATE(t:Task {id:$prefix+toString(i),claimed_by:'hermes-local',
            target_agent_id:'hermes-local',claim_id:'claim-'+toString(i),
            agent_session_id:'synthetic-session',execution_attempt:1,
            lease_expires_at_ts:timestamp()+120000,status:'CLAIMED'})
    """,prefix=prefix)
    baseline=module.candidate_readonly(session)
    assert baseline["candidate_count"]==621
    assert baseline["bounded_double_scan_stable"]
    assert not baseline["authoritative_complete_census"]
    print("PASS historical_census_baseline_621_HOLD",flush=True)

    for index,field in enumerate(fields):
        name=prefix+"_residual_"+str(index)
        # The malicious historical record retains only a non-current owner
        # witness; a query limited to claimed_by/target would miss it.
        run(
            "CREATE(t:Task{id:$id,status:'DONE'}) "
            "SET t."+field+"='hermes-local'",
            id=name,
        )
        result=module.candidate_readonly(session)
        assert result["candidate_count"] is None,(field,result)
        assert not result["bounded_double_scan_stable"]
        assert result["reason"]=="incomplete_or_unqualified_source_claim_inventory"
        assert not result["production_dispatch_authorized"]
        run("MATCH(t:Task{id:$id}) DETACH DELETE t",id=name)
        clean=module.candidate_readonly(session)
        assert clean["candidate_count"]==621 and clean["bounded_double_scan_stable"]
        print("PASS historical_owner_"+field+"_omission_denied",flush=True)

    # Also detect a residual target with claimed_by and claim ID erased.
    orphan=prefix+"_target_orphan"
    run("CREATE(t:Task{id:$id,status:'DONE',target_agent_id:'hermes-local'})",id=orphan)
    assert not module.candidate_readonly(session)["bounded_double_scan_stable"]
    run("MATCH(t:Task{id:$id}) DETACH DELETE t",id=orphan)
    print("PASS historical_target_claimless_denied",flush=True)

    # No query may mutate a synthetic Task during census reads.
    assert module.candidate_readonly(session)["candidate_count"]==621
    with driver.session(database="neo4j") as s:
        rec=s.run("MATCH(t:Task) WHERE t.id STARTS WITH $prefix "
                  "RETURN count(t) AS n",{"prefix":prefix}).single()
        assert rec["n"]==621
    print("CENSUS_HISTORY_DISPOSABLE_NEO4J_PASS=9;PRODUCTION_TASKS_TOUCHED=0;DECISION=HOLD",flush=True)
finally:
    # Synthetic-only cleanup; never use an unbounded graph deletion.
    try:
        run("MATCH(t:Task) WHERE t.id STARTS WITH $prefix DETACH DELETE t",
            prefix=prefix)
    finally:
        driver.close()
