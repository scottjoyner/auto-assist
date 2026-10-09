"""Read-only Neo4j 5.26 timeout observation on a disposable isolated container.

No production credentials or URI arguments accepted. Runs at most three
bounded synthetic calculation queries, reads SHOW TRANSACTIONS, removes no
data, and does not attempt to terminate a production transaction.
"""
import json
import subprocess
import time
from pathlib import Path
from neo4j import GraphDatabase, Query, READ_ACCESS

NAME="assistx-trace-cancel-stage-20261008"
NET="assistx-trace-cancel-isolated-20261008"
DEST=Path(__file__).resolve().parents[1]/"docs"/"TRACE_INDEX_CANCELLATION_RESULT_20261008.json"

def checked_uri():
    def inspect(*cmd):
        x=subprocess.run(["docker",*cmd],capture_output=True,text=True,timeout=8,check=False)
        if x.returncode:raise RuntimeError("CANNOT_INSPECT_DISPOSABLE")
        return json.loads(x.stdout)
    objs=inspect("inspect",NAME)
    if len(objs)!=1:raise RuntimeError("UNKNOWN_OR_AMBIGUOUS_TARGET")
    o=objs[0]
    h=o["HostConfig"]
    nets=o["NetworkSettings"]["Networks"]
    net=inspect("network","inspect",NET)
    if (o.get("Name")!="/"+NAME or not o["State"]["Running"]
        or o["Config"].get("Image")!="neo4j:5.26-enterprise"
        or h.get("NetworkMode")!=NET
        or set(nets)!={NET}
        or not net or not net[0].get("Internal")
        or h.get("PortBindings")
        or not (0<h.get("NanoCpus",0)<=1_000_000_000)
        or not (0<h.get("Memory",0)<=2_306_867_200)
        or any(m.get("Type")=="bind" for m in o.get("Mounts",[]))
        or "NEO4J_AUTH=none" not in o["Config"].get("Env",[])):
        raise RuntimeError("UNSAFE_STAGING_TARGET")
    ip=nets[NET]["IPAddress"]
    if not ip.startswith("172.") or not ip.endswith(".2"):
        raise RuntimeError("UNEXPECTED_STAGING_IP")
    return "bolt://"+ip+":7687"

def query(driver,statement,timeout):
    started=time.perf_counter()
    err=None
    state="ok"
    rows=[]
    try:
        with driver.session(database="neo4j",default_access_mode=READ_ACCESS,
                            fetch_size=100) as s:
            result=s.run(Query(statement,timeout=timeout))
            rows=[dict(r) for r in result]
            result.consume()
    except Exception as e:
        state="error"
        err={"type":type(e).__name__,"code":str(getattr(e,"code",""))[:100],
             "message":str(e)[:240]}
    return {"status":state,"duration_ms":round((time.perf_counter()-started)*1000,2),
            "error":err,"row_count":len(rows),"rows":rows[:3]}

def run():
    uri=checked_uri()
    report={"schema":"trace-index-deadline-probe.v1","graph":"fully disposable neo4j 5.26",
        "production_access":False,"trace_data_used":False,
        "network":"isolated internal Docker network, no published ports",
        "request_timeout_seconds":[0.001,0.03,0.2],
        "hard_physical_fence_proven":False,
        "notes":"Cooperative normal-network driver timeout only; stalled transport not simulated",
        "probes":[]}
    with GraphDatabase.driver(uri,auth=None,connection_timeout=2,
                              max_connection_pool_size=2,
                              connection_acquisition_timeout=2) as driver:
        driver.verify_connectivity()
        version=query(driver,"CALL dbms.components() YIELD versions RETURN versions[0] AS v",2)
        if version["status"]!="ok" or not version["rows"] or not version["rows"][0]["v"].startswith("5.26."):
            raise RuntimeError("WRONG_SERVER_VERSION")
        report["version"]=version["rows"][0]["v"]
        # Deliberately expensive but bounded, read-only calculation on no real
        # graph data. Very short timeouts exercise cooperative cancellation.
        stmt=("UNWIND range(1, 12000) AS n UNWIND range(1, 12000) AS m "
              "WITH n,m WHERE (n*m)%97 = 7 RETURN count(*) AS count")
        for ttl in report["request_timeout_seconds"]:
            outcome=query(driver,stmt,ttl)
            # Follow-up health checks test whether the graph remains useful,
            # not whether the physical query was definitely cancelled.
            health=query(driver,"RETURN 1 AS healthy",2.0)
            outcome["requested_timeout_seconds"]=ttl
            outcome["post_timeout_health"]=health["status"]
            report["probes"].append(outcome)
            print("PROBE",ttl,outcome["status"],outcome["duration_ms"],
                  (outcome["error"] or {}).get("type"),"health",health["status"],flush=True)
            if health["status"]!="ok":break
        active=query(driver,
          "SHOW TRANSACTIONS YIELD currentQuery, status "
          "WHERE currentQuery STARTS WITH 'UNWIND' RETURN count(*) AS benchmark_active",3.0)
        report["staging_transaction_check"]=active
        print("TRANSACTIONS",active["status"],active["rows"],flush=True)
    DEST.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print("RESULT",DEST,flush=True)

if __name__=="__main__":
    run()
