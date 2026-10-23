#!/usr/bin/env python3
"""Opt-in, network-isolated Neo4j source census over >500 synthetic tasks."""
from __future__ import annotations
if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("requires explicitly isolated disposable Neo4j")

import importlib.util
import os
from pathlib import Path
from neo4j import GraphDatabase

host=os.getenv("HERMES_SYNTHETIC_CENSUS_DB","")
assert host.startswith("hermes-census-db-")
path=Path("/candidate/src/assistx/hermes_claim_census.py")
spec=importlib.util.spec_from_file_location("census_test_only",path)
c=importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
driver=GraphDatabase.driver("bolt://"+host+":7687",auth=None,connection_timeout=8)
driver.verify_connectivity()

def run(query,**kw):
    with driver.session(database="neo4j") as s:
        return s.run(query,**kw).consume()

run("""UNWIND range(0, 620) AS i
CREATE (t:Task {id:'synthetic-hermes-'+toString(i),
    claimed_by:'hermes-local', target_agent_id:'hermes-local',
    claim_id:'synthetic-claim-'+toString(i),
    agent_session_id:'synthetic-session', execution_attempt:1,
    lease_expires_at_ts:timestamp()+120000, status:'CLAIMED'})""")

def make_session():
    return driver.session(database="neo4j")

result=c.candidate_readonly(make_session)
assert result["bounded_double_scan_stable"] and result["candidate_count"]==621,result
assert not result["authoritative_complete_census"]
print("REAL_NEO4J_OVER_500_SCAN_PASS count=621",flush=True)

overflow=c.candidate_readonly(make_session,max_rows=600)
assert not overflow["bounded_double_scan_stable"] and overflow["candidate_count"] is None
print("REAL_NEO4J_BOUNDED_OVERFLOW_REJECTED",flush=True)

run("""CREATE (t:Task {id:'synthetic-hermes-missing',
    claimed_by:'hermes-local',target_agent_id:'hermes-local',
    agent_session_id:'synthetic-session',execution_attempt:1,
    lease_expires_at_ts:timestamp()+120000,status:'CLAIMED'})""")
denied=c.candidate_readonly(make_session)
assert denied["reason"]=="incomplete_or_unqualified_source_claim_inventory",denied
print("REAL_NEO4J_MISSING_CLAIM_DENIED",flush=True)
run("MATCH(t:Task {id:'synthetic-hermes-missing'}) DETACH DELETE t")

class ChurnSession:
    def __init__(self):
        self.s=driver.session(database="neo4j")
        self.calls=0
    def __enter__(self):
        return self
    def __exit__(self,*args):
        self.s.close()
    def run(self,q,kw):
        self.calls+=1
        if self.calls==2:
            run("""MATCH (t:Task {id:'synthetic-hermes-0'})
                SET t.claim_id='synthetic-changed-claim'""")
        return self.s.run(q,kw)

churn=c.candidate_readonly(ChurnSession)
assert churn["reason"]=="source_changed_between_read_committed_scans",churn
print("REAL_NEO4J_CLAIM_CHURN_DENIED",flush=True)
assert not churn["production_dispatch_authorized"]

run("""CREATE (t:Task {id:'synthetic-hermes-unapproved-owner',
    claimed_by:'hermes-shadow',target_agent_id:'hermes-shadow',
    claim_id:'shadow-claim',agent_session_id:'shadow-session',
    execution_attempt:1,lease_expires_at_ts:timestamp()+120000,
    status:'CLAIMED'})""")
shadow = c.candidate_readonly(make_session)
assert shadow["reason"] == "incomplete_or_unqualified_source_claim_inventory", shadow
run("MATCH(t:Task {id:'synthetic-hermes-unapproved-owner'}) DETACH DELETE t")
print("REAL_NEO4J_UNAPPROVED_HERMES_OWNER_DENIED", flush=True)

driver.close()
print("CENSUS_DISPOSABLE_NEO4J_PASS=5 REAL_CLAIMS_TOUCHED=0 decision=HOLD")
