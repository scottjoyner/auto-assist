"""Disposable synthetic Neo4j benchmark for AssistX TraceGroup outcome filters.

Use ONLY the standalone docker container named below; never connects to live Neo4j,
reads event payloads, mounts NAS, changes production, or handles credentials.
"""
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from assistx.swarm_core import list_traces

NAME="assistx-tracebench-stage-20261008"
COUNT=85000
TIMEOUT=140


def verify_disposable_container():
    """Fail closed before writes unless the target is an isolated test DB."""
    result=subprocess.run(["docker","inspect",NAME],capture_output=True,text=True,timeout=8)
    if result.returncode:raise RuntimeError("DISPOSABLE_CONTAINER_NOT_FOUND")
    details=json.loads(result.stdout)[0]
    cfg=details["HostConfig"]
    if (details["Name"]!="/"+NAME
        or cfg.get("NetworkMode")!="none"
        or cfg.get("PortBindings")
        or not (0 < cfg.get("NanoCpus",0) <= 1000000000)
        or not (0 < cfg.get("Memory",0) <= 2250*1024*1024)
        or any(m.get("Type")=="bind" for m in details.get("Mounts",[]))
        or not details["Config"]["Image"].startswith("neo4j:5.23.")
        or "NEO4J_AUTH=none" not in details["Config"].get("Env",[])):
        raise RuntimeError("UNSAFE_BENCHMARK_CONTAINER")
    if not details["State"]["Running"]:
        raise RuntimeError("STAGING_CONTAINER_NOT_RUNNING")


def cli(cypher,timeout=TIMEOUT):
    started=time.perf_counter()
    proc=subprocess.run(["docker","exec",NAME,"cypher-shell",
            "-a","bolt://localhost:7687","--format","plain",cypher],
        capture_output=True,text=True,timeout=timeout,check=False)
    duration=1000*(time.perf_counter()-started)
    if proc.returncode:
        raise RuntimeError("Synthetic staging Cypher exit "+str(proc.returncode)+": "+proc.stderr[:450])
    return duration,proc.stdout.strip()

def query_pair(outcome):
    class FakeResult:
        def single(self):return {"n":0}
        def __iter__(self):return iter(())
    class Session:
        def __init__(self):self.queries=[]
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def run(self,query,params):
            self.queries.append((str(query),dict(params),query.timeout))
            return FakeResult()
    class Neo:
        def __init__(self):self.s=Session()
        def _session(self):return self.s
    n=Neo()
    list_traces(n,limit=50,offset=0,outcome=outcome)
    assert len(n.s.queries)==2
    for q,p,to in n.s.queries:
        assert to==4.0 and p=={"limit":50,"offset":0}
    # Only fixed literal numbers replace bound params; no private/user input.
    return [q.replace("$offset","0").replace("$limit","50") for q,_,_ in n.s.queries]

def make_graph():
    seed="""UNWIND range(0,84999) AS i
CALL {
  WITH i
  CREATE (g:TraceGroup {correlation_id: 'synthetic-' + toString(i)})
  CREATE (t1:TraceEvent {event_type: CASE i % 5
     WHEN 0 THEN 'task.failed'
     WHEN 1 THEN 'task.failed'
     WHEN 2 THEN 'task.completed'
     WHEN 3 THEN 'task.accepted'
     ELSE 'task.started' END,
     ts_ms:i*1000})
  CREATE (t2:TraceEvent {event_type:CASE i % 5
     WHEN 0 THEN 'task.completed'
     WHEN 1 THEN 'task.started'
     ELSE 'task.progress' END,
     ts_ms:i*1000+1})
  CREATE (g)-[:HAS_EVENT]->(t1)
  CREATE (g)-[:HAS_EVENT]->(t2)
} IN TRANSACTIONS OF 1000 ROWS"""
    t,stdout=cli(seed,180)
    print("seed_wall_ms",round(t),"stdout",stdout[:100],flush=True)
    t2,out=cli("MATCH (g:TraceGroup) RETURN count(g) AS groups")
    print("groups",out[:250],"query_shell_wall_ms",round(t2),flush=True)
    assert "85000" in out, out
    return t

def run():
    report={"schema":"assistx-trace-stage-bench-v1",
            "source":"generated-only",
            "database":"isolated docker neo4j 5.23.0 community no network/ports/host mounts",
            "group_count":COUNT,"events_per_group":2,
            "expected":{"failed":34000,"completed":34000,"open":17000},
            "production_access":False,"live_graph_queries":False,
            "metrics_include_cypher_shell_startup":True,
            "raw_trace_text_read":False,"provider_actions":False}
    report["seed_wall_ms"]=round(make_graph())
    results={}
    for outcome in ("failed","completed","open"):
        cq,pq=query_pair(outcome)
        explain=[]
        for kind,q in (("count",cq),("page",pq)):
            elapsed,out=cli("EXPLAIN "+q,timeout=35)
            explain.append({"kind":kind,"wall_ms":round(elapsed),"plan_excerpt":out[:3600]})
        items={}
        for kind,q in (("count",cq),("page",pq)):
            samples=[]
            last=None
            for i in range(3):
                elapsed,out=cli(q,timeout=25)
                samples.append(round(elapsed))
                last=out[:350]
                if kind=="count":
                    # cypher-shell prints a column header then numeric total.
                    numbers=[int(x.strip()) for x in out.splitlines() if x.strip().isdigit()]
                    if not numbers or numbers[-1]!=report["expected"][outcome]:
                        raise AssertionError("UNEXPECTED_SYNTHETIC_COUNT: "+outcome)
            items[kind]={"wall_ms":samples,"median_ms":round(statistics.median(samples)),
                         "sample":last}
            print("sample",outcome,kind,samples,last[:100],flush=True)
        results[outcome]={"explain":explain,"measurements":items}
    report["results"]=results
    out=ROOT/"docs"/"trace_global_perf_synthetic_results.json"
    # Keep synthetic-only result in repo; no real trace identities/credentials.
    out.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print("RESULT",out,flush=True)

if __name__=="__main__":
    verify_disposable_container()
    run()
