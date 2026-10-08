"""Read-only correction pass for Neo4j 5.26 camelCase profile keys (synthetic staging)."""
import json
import sys
from pathlib import Path

from neo4j import GraphDatabase, Query, READ_ACCESS

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tests"))
from bench_trace_526_driver import guarded_uri, profile_tree, OUT
from bench_trace_global_neo4j import query_pair

def refresh_profile_only():
    uri=guarded_uri()
    report=json.loads(OUT.read_text())
    assert report["exact_server_version"]=="5.26.30"
    report["profile_parse_correction"]="Neo4j driver uses operatorType/dbHits camelCase keys. Plan profiles refreshed read-only after original driver samples, not part of the original five-run timings."
    with GraphDatabase.driver(uri,auth=None,connection_timeout=3) as d:
        with d.session(database="neo4j",default_access_mode=READ_ACCESS) as s:
            version=s.run("CALL dbms.components() YIELD versions RETURN versions[0] AS v").single()["v"]
            groups=s.run("MATCH (g:TraceGroup) RETURN count(g) AS n").single()["n"]
            assert version=="5.26.30" and groups==85000
            for label in ("failed","completed","open"):
                for kind,query in zip(("count","page"),query_pair(label)):
                    result=s.run(Query("PROFILE "+query,timeout=4.0))
                    list(result)
                    stats=profile_tree(result.consume().profile)
                    assert stats["operator_count"]>=3, (label,kind)
                    assert stats["total_reported_db_hits"]>0, (label,kind)
                    report["results"][label]["profile_"+kind]=stats
                    print("PROFILE",label,kind,"operators",stats["operator_count"],
                          "dbHits",stats["total_reported_db_hits"],flush=True)
    OUT.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print("CORRECTED",OUT,flush=True)

if __name__=="__main__":
    refresh_profile_only()
