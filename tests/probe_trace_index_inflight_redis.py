"""One-shot real Redis Lua lease smoke with synthetic tokens only.

Requires a *prestarted* exact-name, disconnected disposable Redis 7 container.
Runs redis-cli inside it; never opens fleet Redis, mounts source storage, or
creates/restores any production service. No CLI credentials or network ports.
"""
import hashlib
import json
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Import only constant Lua source via AST, not the optional runtime redis-py
# dependency or its production Redis URL.
import ast
source=ast.parse((ROOT/"src"/"assistx"/"rate_limiter.py").read_text())
constants={}
for node in source.body:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in (
                "TRACE_INDEX_LEASE_ACQUIRE_LUA", "TRACE_INDEX_LEASE_RELEASE_LUA",
                "TRACE_INDEX_LUA"
            ):
                constants[target.id]=ast.literal_eval(node.value)
TRACE_INDEX_LEASE_ACQUIRE_LUA=constants["TRACE_INDEX_LEASE_ACQUIRE_LUA"]
TRACE_INDEX_LEASE_RELEASE_LUA=constants["TRACE_INDEX_LEASE_RELEASE_LUA"]
TRACE_INDEX_LUA=constants["TRACE_INDEX_LUA"]

NAME="assistx-trace-inflight-redis-20261008"
KEY="lease:{assistx_trace_index}:inflight"

def safe_container():
    proc=subprocess.run(["docker","inspect",NAME],capture_output=True,text=True,timeout=8)
    if proc.returncode:raise RuntimeError("DISPOSABLE_REDIS_NOT_FOUND")
    j=json.loads(proc.stdout)[0]
    h=j["HostConfig"]
    if (j["Name"]!="/"+NAME or not j["State"]["Running"]
        or h.get("NetworkMode")!="none" or h.get("PortBindings")
        or not (0 < h.get("NanoCpus",0) <= 1000000000)
        or not (0 < h.get("Memory",0) <= 128*1024*1024)
        or any(m["Type"]=="bind" for m in j.get("Mounts",[]))
        or not j["Config"]["Image"].startswith("redis:7")
        or not j["HostConfig"].get("ReadonlyRootfs",False)):
        raise RuntimeError("UNSAFE_REDIS_BENCHMARK_TARGET")

def eval_lua(script,token,ttl=30000,cap=3):
    if script=="acquire":lua=TRACE_INDEX_LEASE_ACQUIRE_LUA;args=[cap,ttl,token]
    else:lua=TRACE_INDEX_LEASE_RELEASE_LUA;args=[token]
    call=["docker","exec",NAME,"redis-cli","--raw","EVAL",lua,"1",KEY,*map(str,args)]
    proc=subprocess.run(call,capture_output=True,text=True,timeout=15)
    if proc.returncode:raise RuntimeError("REDIS_CLI_ERROR "+proc.stderr[:160])
    rows=proc.stdout.strip().splitlines()
    if script=="acquire":
        if len(rows)!=3:raise RuntimeError("MALFORMED_REDIS_ACQUIRE "+repr(rows))
        return tuple(int(x) for x in rows)
    if len(rows)!=1:raise RuntimeError("MALFORMED_REDIS_RELEASE "+repr(rows))
    return int(rows[0])

def rate(peer, per=2, global_cap=5):
    identity=hashlib.sha256(peer.encode()).hexdigest()
    args=["docker","exec",NAME,"redis-cli","--raw","EVAL",
          TRACE_INDEX_LUA,"2",
          "ratelimit:{assistx_trace_index}:peer:"+identity,
          "ratelimit:{assistx_trace_index}:global",
          str(per),str(global_cap),"60000",uuid.uuid4().hex]
    proc=subprocess.run(args,capture_output=True,text=True,timeout=15)
    if proc.returncode:raise RuntimeError("RATE_EVAL_ERROR")
    parts=proc.stdout.strip().splitlines()
    if len(parts)!=3:raise RuntimeError("MALFORMED_RATE_RESULT")
    return tuple(int(v) for v in parts)


def composed_attempt(_):
    token=uuid.uuid4().hex
    granted=eval_lua("acquire",token)[0]==1
    if not granted:return "capacity_denied",None
    allowed=rate("synthetic-single-peer")[0]==1
    if not allowed:
        if eval_lua("release",token)!=1:raise RuntimeError("LEAKED_QUOTA_DENIED_LEASE")
        return "rate_denied",None
    return "accepted",token


def main():
    safe_container()
    report={"schema":"assistx-trace-index-inflight-redis-v1",
            "source":"synthetic-only","redis_image":"redis:7-alpine",
            "network":"none","published_ports":0,"bind_mounts":0,
            "production_access":False,"capacity":3,"ttl_seconds":30,
            "bursts":[]}
    for clients in (1,3,5,10):
        tokens=[uuid.uuid4().hex for _ in range(clients)]
        with ThreadPoolExecutor(max_workers=clients) as pool:
            answers=list(pool.map(lambda token:eval_lua("acquire",token),tokens))
        admitted=[tok for tok,result in zip(tokens,answers) if result[0]==1]
        assert len(admitted)==min(clients,3), (clients,answers)
        assert all(r[0] in (0,1) and r[1]<=3 for r in answers)
        for token in admitted:assert eval_lua("release",token)==1
        for token in admitted:assert eval_lua("release",token)==0
        report["bursts"].append({"synthetic_clients":clients,
                                 "accepted":len(admitted),
                                 "denied":clients-len(admitted)})
        print("BURST",clients,"accepted",len(admitted),"denied",clients-len(admitted),flush=True)
    # Crash expiry: simulated worker ceases without releasing, Redis owns clock.
    old=uuid.uuid4().hex
    assert eval_lua("acquire",old,ttl=600)==(1,1,0)
    time.sleep(0.8)
    new=uuid.uuid4().hex
    result=eval_lua("acquire",new,ttl=30000)
    assert result[0]==1 and eval_lua("release",old)==0
    assert eval_lua("release",new)==1
    report["expiry_recovered"]=True
    # Stale release from a completed generation never frees its successor.
    first=uuid.uuid4().hex
    assert eval_lua("acquire",first,cap=1)[0]==1
    assert eval_lua("release",first)==1
    current=uuid.uuid4().hex
    assert eval_lua("acquire",current,cap=1)[0]==1
    assert eval_lua("release",first)==0
    assert eval_lua("acquire",uuid.uuid4().hex,cap=1)[0]==0
    assert eval_lua("release",current)==1
    report["stale_release_fenced"]=True
    with ThreadPoolExecutor(max_workers=10) as pool:
        composed=list(pool.map(composed_attempt,range(10)))
    accepted=[token for status,token in composed if status=="accepted"]
    statuses=[status for status,_ in composed]
    assert len(accepted)==2, statuses
    assert any(s!="accepted" for s in statuses)
    for token in accepted:assert eval_lua("release",token)==1
    report["combined_rate_and_capacity"]={
        "synthetic_clients":10,"accepted":len(accepted),
        "denied":len(statuses)-len(accepted),
        "quota_refusals":statuses.count("rate_denied"),
        "capacity_refusals":statuses.count("capacity_denied")
    }
    print("COMPOSED",report["combined_rate_and_capacity"],flush=True)
    out=ROOT/"docs"/"trace_index_inflight_redis_results.json"
    out.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print("RESULT",out,flush=True)

if __name__=="__main__":
    main()
