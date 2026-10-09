"""Run only against an inspected disposable, fully disconnected Redis container.

This script writes synthetic admission windows into the test Redis instance.
It never opens production Redis or the fleet Neo4j service.
"""
import ast
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CONTAINER="assistx-traceguard-20261008"
TEST_KEY="ratelimit:{assistx_trace_index}:peer:fixture"
FLEET_KEY="ratelimit:{assistx_trace_index}:global"

def run(*args):
    return subprocess.run(args,capture_output=True,text=True,check=True,timeout=8).stdout

def preflight():
    info=json.loads(run("docker","inspect",CONTAINER))[0]
    host=info["HostConfig"]
    assert info["Name"]=="/"+CONTAINER
    assert info["State"]["Running"]
    assert info["Config"]["Image"]=="redis:7-alpine"
    assert host["NetworkMode"]=="none"
    assert host["PortBindings"] in (None,{})
    assert 0<host.get("NanoCpus",0)<=500_000_000
    assert 0<host.get("Memory",0)<=256*1024*1024
    assert not any(m.get("Type")=="bind" for m in info["Mounts"])

def load_script():
    tree=ast.parse((ROOT/"src/assistx/rate_limiter.py").read_text())
    expr=next(x.value for x in tree.body if isinstance(x,ast.Assign)
              and any(isinstance(t,ast.Name) and t.id=="TRACE_INDEX_LUA" for t in x.targets))
    return ast.literal_eval(expr)

def redis_eval(lua,client,member,per_max=2,global_max=3,window=10_000):
    out=run("docker","exec",CONTAINER,"redis-cli","--raw","EVAL",
        lua,"2",client,FLEET_KEY,str(per_max),str(global_max),str(window),member)
    values=[int(x) for x in out.splitlines() if x.strip()]
    if len(values)!=3:raise AssertionError("INVALID_LUA_RESPONSE: "+repr(out))
    return values

def zcard(key):
    return int(run("docker","exec",CONTAINER,"redis-cli","--raw","ZCARD",key).strip())

def main():
    preflight()
    lua=load_script()
    p1=TEST_KEY+"1";p2=TEST_KEY+"2";p3=TEST_KEY+"3"
    responses=[
      redis_eval(lua,p1,"a"),
      redis_eval(lua,p1,"b"),
      redis_eval(lua,p1,"c"),
      redis_eval(lua,p2,"d"),
      redis_eval(lua,p2,"e"),
      redis_eval(lua,p3,"f")
    ]
    assert responses[0]==[1,1,0],responses
    assert responses[1]==[1,0,0],responses
    assert responses[2][0]==0 and responses[2][2]>=1,responses
    assert responses[3]==[1,1,0],responses
    assert responses[4][0]==0 and responses[5][0]==0,responses
    assert zcard(FLEET_KEY)==3
    assert zcard(p1)==2 and zcard(p2)==1 and zcard(p3)==0
    print("ATOMIC_LIMIT_TEST_PASSED",json.dumps({"responses":responses,"global_count":3}))
    # Distinct separate test namespace would test expiry without slowing the
    # shared host. Do not sleep with production-related services.
    preflight()
    print("NO_PRODUCTION_REDIS_CONNECTED")

if __name__=="__main__":
    main()
