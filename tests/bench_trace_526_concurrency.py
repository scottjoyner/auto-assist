"""Bounded five-client contention screen on one disposable 5.26 fixture.

Read-only: 15 requests max, real 4-second query timeouts; no new seed writes.
"""
import concurrent.futures as cf
import json
import statistics
import sys
import time
from pathlib import Path

from neo4j import GraphDatabase, READ_ACCESS

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
sys.path.insert(0,str(ROOT/"tests"))
from assistx.swarm_core import list_traces
from bench_trace_526_driver import guarded_uri, OUT

EXPECT={"failed":34000,"completed":34000,"open":17000}
def run():
    uri=guarded_uri()
    prior=json.loads(OUT.read_text())
    if prior.get("exact_server_version")!="5.26.30":raise RuntimeError("BAD_PRIOR_FIXTURE")
    with GraphDatabase.driver(uri,auth=None,connection_timeout=3,
                              max_connection_pool_size=6) as driver:
        with driver.session(database="neo4j",default_access_mode=READ_ACCESS) as session:
            count=session.run("MATCH (g:TraceGroup) RETURN count(g) AS n").single()["n"]
            version=session.run("CALL dbms.components() YIELD versions RETURN versions[0] AS v").single()["v"]
        if count!=85000 or version!="5.26.30":raise RuntimeError("FIXTURE_MISMATCH")
        def call(label):
            class Adapter:
                def _session(self):
                    return driver.session(database="neo4j",default_access_mode=READ_ACCESS)
            start=time.perf_counter()
            try:
                result=list_traces(Adapter(),limit=50,offset=0,outcome=label)
                if result["total"]!=EXPECT[label]:raise AssertionError("COUNT_MISMATCH")
                return {"label":label,"latency_ms":round((time.perf_counter()-start)*1000,2),
                        "returned":len(result["traces"]),"status":"ok"}
            except Exception as exc:
                return {"label":label,"latency_ms":round((time.perf_counter()-start)*1000,2),
                        "status":"failed","error":type(exc).__name__,"detail":str(exc)[:130]}
        rows=[]
        labels=("failed","completed","open","failed","open")
        for wave in range(3):
            with cf.ThreadPoolExecutor(max_workers=5) as pool:
                fut=[pool.submit(call,x) for x in labels]
                wave_data=[x.result(timeout=18) for x in fut]
            rows.extend(wave_data)
            print("WAVE",wave+1,wave_data,flush=True)
    successes=[r["latency_ms"] for r in rows if r["status"]=="ok"]
    result={"schema":"trace-526-concurrency-screen-v1","fixture":"isolated synthetic 5.26.30",
            "requests":len(rows),"concurrency":5,"waves":3,"data":rows,
            "successful":len(successes),
            "failures":len(rows)-len(successes),
            "median_request_ms":round(statistics.median(successes),2) if successes else None,
            "max_request_ms":max(successes) if successes else None,
            "production_sla_proven":False,
            "user_data_access":False,
            "note":"15 synthetic requests only, each 2 read queries with 4s driver cap"}
    path=ROOT/"docs"/"trace_526_driver_concurrency_results.json"
    path.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print("SUMMARY",result["successful"],result["failures"],result["median_request_ms"],result["max_request_ms"],flush=True)
if __name__=="__main__":
    run()
