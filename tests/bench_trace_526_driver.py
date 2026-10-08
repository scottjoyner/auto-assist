"""Strictly sandboxed, disposable Neo4j 5.26 trace-index driver benchmark.

Synthetic graph writes occur ONLY after verifying an exact Docker container,
private internal network, storage/cpu caps, and zero existing TraceGroup nodes.
Reads use Neo4j 6.2 driver 4-second per-query guard. Not a release acceptance.
"""
import concurrent.futures as cf
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

from neo4j import GraphDatabase, Query, READ_ACCESS

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
sys.path.insert(0, str(ROOT/"tests"))
from assistx.swarm_core import list_traces
from bench_trace_global_neo4j import query_pair

NAME="assistx-tracebench-526-20261008"
NET="assistx-tracebench-isolated-20261008"
EXPECTED_COUNTS={"failed":34000,"completed":34000,"open":17000}
OUT=ROOT/"docs"/"trace_526_driver_synthetic_results.json"

def docker_json(*args):
    p=subprocess.run(["docker",*args],capture_output=True,text=True,timeout=9)
    if p.returncode:raise RuntimeError("INSPECTION_UNAVAILABLE")
    return json.loads(p.stdout)

def guarded_uri():
    info=docker_json("inspect",NAME)
    if len(info)!=1:raise RuntimeError("AMBIGUOUS_CONTAINER")
    c=info[0]
    host=c["HostConfig"]
    nets=c["NetworkSettings"]["Networks"]
    network=docker_json("network","inspect",NET)
    if (c.get("Name")!="/"+NAME or not c["State"]["Running"]
        or c["Config"].get("Image")!="neo4j:5.26-enterprise"
        or host.get("NetworkMode")!=NET
        or set(nets)!={NET}
        or not network[0].get("Internal")
        or not (0 < host.get("NanoCpus",0)<=1000000000)
        or not (0 < host.get("Memory",0)<=2306867200)
        or any(m.get("Type")=="bind" for m in c.get("Mounts",[]))
        or "NEO4J_AUTH=none" not in c["Config"].get("Env",[])
        or any(v.get("HostIp")!="127.0.0.1"
               for bindings in (host.get("PortBindings") or {}).values()
               for v in (bindings or []))):
        raise RuntimeError("UNSAFE_BENCHMARK_CONTAINER")
    addr=nets[NET]["IPAddress"]
    if not addr.startswith("172.23.") or addr!= "172.23.0.2":
        raise RuntimeError("UNEXPECTED_PRIVATE_TARGET_ADDRESS")
    return "bolt://"+addr+":7687"

def execute(driver,cypher,params=None,timeout=4):
    start=time.perf_counter()
    with driver.session(database="neo4j",default_access_mode=READ_ACCESS) as session:
        records=list(session.run(Query(cypher,timeout=timeout),params or {}))
    return records,round((time.perf_counter()-start)*1000,2)

def seed(driver):
    query="""UNWIND range(0,84999) AS i
    CALL {
      WITH i
      CREATE (g:TraceGroup {correlation_id: 'synthetic-' + toString(i)})
      CREATE (t1:TraceEvent {event_type: CASE i % 5
        WHEN 0 THEN 'task.failed'
        WHEN 1 THEN 'task.failed'
        WHEN 2 THEN 'task.completed'
        WHEN 3 THEN 'task.accepted'
        ELSE 'task.started' END, ts_ms:i*1000})
      CREATE (t2:TraceEvent {event_type: CASE i % 5
        WHEN 0 THEN 'task.completed'
        WHEN 1 THEN 'task.started'
        ELSE 'task.progress' END, ts_ms:i*1000+1})
      CREATE (g)-[:HAS_EVENT]->(t1)
      CREATE (g)-[:HAS_EVENT]->(t2)
    } IN TRANSACTIONS OF 1000 ROWS"""
    with driver.session(database="neo4j") as session:
        began=time.perf_counter()
        # A disposable synthetic fixture, never an existing graph.
        session.run(Query(query,timeout=120.0)).consume()
        print("SEED_WALL_MS",round((time.perf_counter()-began)*1000),flush=True)

def profile_tree(p):
    if not p:return {}
    names=[]
    def walk(node):
        if not isinstance(node,dict):return
        names.append({"operator":node.get("operatorType",node.get("operator_type")),
                      "db_hits":node.get("dbHits",node.get("db_hits")),
                      "rows":node.get("rows"),
                      "estimated_rows":node.get("args",{}).get("EstimatedRows")})
        for child in node.get("children",[]):walk(child)
    walk(p)
    return {"operator_nodes":names[:55],"operator_count":len(names),
            "total_reported_db_hits":sum(int(x["db_hits"] or 0) for x in names)}

def run():
    uri=guarded_uri()
    report={"schema":"trace-526-synthetic-driver-v1",
            "network":"docker internal only, inspected container bridge IP",
            "isolated":True,"production_neo4j_access":False,
            "version_expected":"5.26","driver_version":"6.2",
            "group_count_expected":85000,"events_expected":170000,
            "expected_counts":EXPECTED_COUNTS,
            "driver_timeout_seconds":4,
            "note":"single CPU; 2200MiB; fully synthetic; staging only",
            "results":{}}
    with GraphDatabase.driver(uri,auth=None,connection_timeout=3,
                              max_connection_pool_size=8) as drv:
        with drv.session(database="neo4j",default_access_mode=READ_ACCESS) as s:
            version=s.run("CALL dbms.components() YIELD versions RETURN versions[0] AS v").single()["v"]
            starting=s.run("MATCH (g:TraceGroup) RETURN count(g) AS n").single()["n"]
        if not version.startswith("5.26.") or starting!=0:
            raise RuntimeError("NOT_EMPTY_OR_WRONG_SERVER")
        report["exact_server_version"]=version
        print("PREFLIGHT",version,"empty",starting,flush=True)
        seed(drv)
        rows,_=execute(drv,"MATCH (g:TraceGroup) RETURN count(g) AS n")
        if rows[0]["n"]!=85000:raise RuntimeError("SEED_COUNT_MISMATCH")
        event_rows,_=execute(drv,"MATCH (t:TraceEvent) RETURN count(t) AS n")
        if event_rows[0]["n"]!=170000:raise RuntimeError("EVENT_COUNT_MISMATCH")
        for label in ("failed","completed","open"):
            cq,pq=query_pair(label)
            # query_pair uses literal fixed offset/limit zero/50; no user input.
            case={"sequential":{}}
            for name,query in (("count",cq),("page",pq)):
                elapsed=[]
                response=None
                for i in range(5):
                    try:
                        records,ms=execute(drv,query)
                        elapsed.append(ms)
                        response=records
                    except Exception as exc:
                        case["error_"+name]=type(exc).__name__+":"+str(exc)[:200]
                        break
                if response is not None:
                    if name=="count" and response[0]["n"]!=EXPECTED_COUNTS[label]:
                        raise RuntimeError("FILTER_COUNT_MISMATCH")
                    case["sequential"][name]={"ms":elapsed,
                        "median_ms":round(statistics.median(elapsed),2),
                        "max_ms":max(elapsed),"rows_returned":len(response)}
                    if name=="count":case["count_verified"]=response[0]["n"]
                print("MEASURE",label,name,case["sequential"].get(name),flush=True)
                # Profile only in disconnected disposable synthetic database.
                try:
                    with drv.session(database="neo4j",default_access_mode=READ_ACCESS) as s:
                        result=s.run(Query("PROFILE "+query,timeout=4.0))
                        list(result)
                        profile=result.consume().profile
                    case["profile_"+name]=profile_tree(profile)
                except Exception as exc:
                    case["profile_error_"+name]=type(exc).__name__+":"+str(exc)[:200]
                print("PLAN",label,name,
                      len(case.get("profile_"+name,{}).get("operator_nodes",[])),flush=True)
            report["results"][label]=case
        # Bounded concurrent genuine driver reads, no writes and no more than
        # 3 concurrent clients on a 1-CPU disposable synthetic node.
        def task(label):
            class Client:
                def _session(self):
                    return drv.session(database="neo4j",
                                       default_access_mode=READ_ACCESS)
            t=time.perf_counter()
            outcome=list_traces(Client(),limit=50,offset=0,outcome=label)
            if outcome["total"]!=EXPECTED_COUNTS[label]:
                raise AssertionError("CONCURRENT_INCORRECT_COUNT")
            return round((time.perf_counter()-t)*1000,2)
        for concurrency in (1,3):
            with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures=[pool.submit(task,("failed","completed","open")[i%3])
                         for i in range(concurrency)]
                results=[]
                for future in futures:
                    try:results.append({"ms":future.result(timeout=14)})
                    except Exception as exc:results.append({"error":type(exc).__name__+":"+str(exc)[:140]})
            report.setdefault("concurrent",{})[str(concurrency)]=results
            print("CONCURRENT",concurrency,results,flush=True)
    OUT.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print("RESULT",OUT,flush=True)

if __name__=="__main__":
    run()
