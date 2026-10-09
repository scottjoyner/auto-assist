"""Deterministic real Lua evaluation in a single disconnected disposable Redis 7.

START THE EXACT SAFETY-CAPPED CONTAINER FROM THE RUNBOOK FIRST.
This script refuses networked containers, unexpected images/names/binds/ports.
Never connects to the real AssistX Redis or Neo4j hosts.
"""
import json
import subprocess

from assistx.trace_index_quarantine import (
    QUARANTINE_ACQUIRE_LUA, QUARANTINE_RELEASE_LUA, QUARANTINE_INSPECT_LUA,
    TraceIndexQuarantine,
)

NAME="assistx-quarantine-test-20261008"
KEY=TraceIndexQuarantine.key

def shell(args, timeout=10):
    proc=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if proc.returncode:
        raise RuntimeError("COMMAND_FAILED "+str(proc.returncode)+": "+proc.stderr[:160])
    return proc.stdout.strip()

def check_target():
    j=json.loads(shell(["docker","inspect",NAME]))[0]
    host=j["HostConfig"]
    if (j["Name"]!="/"+NAME or not j["State"]["Running"] or
        j["Config"]["Image"]!="redis:7-alpine" or
        host["NetworkMode"]!="none" or
        host.get("PortBindings") or
        host.get("NanoCpus")!=1000000000 or
        host.get("Memory",0)>160*1024*1024 or
        host.get("Memory",0)<32*1024*1024 or
        any(m["Type"]=="bind" for m in j.get("Mounts",[]))):
        raise RuntimeError("REFUSING_UNSAFE_REDIS_TARGET")

def redis(*args):
    return shell(["docker","exec",NAME,"redis-cli","--raw",*map(str,args)])

def lua(code,*args):
    result=redis("EVAL",code,1,KEY,*args).splitlines()
    return [int(x) for x in result]

def main():
    check_target()
    assert redis("PING")=="PONG"
    assert redis("TYPE",KEY)=="none"
    first="a"*32
    second="b"*32
    assert lua(QUARANTINE_ACQUIRE_LUA,1,first)==[1,1]
    assert lua(QUARANTINE_ACQUIRE_LUA,1,second)==[0,1]
    assert lua(QUARANTINE_INSPECT_LUA)==[1]
    assert redis("TTL",KEY)=="-1"
    # No TTL reaping: changing the recorded timestamp to zero does not free it.
    assert redis("HSET",KEY,first,0)=="0"
    assert lua(QUARANTINE_ACQUIRE_LUA,1,second)==[0,1]
    assert lua(QUARANTINE_RELEASE_LUA,"c"*32)==[0]
    assert lua(QUARANTINE_RELEASE_LUA,first)==[1]
    assert lua(QUARANTINE_ACQUIRE_LUA,1,second)==[1,1]
    # Explicit adversarial negative control: losing Redis state with an
    # outstanding physical query violates the admission guarantee.
    assert redis("DEL",KEY)=="1"
    assert lua(QUARANTINE_ACQUIRE_LUA,1,"d"*32)==[1,1]
    print(json.dumps({
        "fixture":"disconnected_redis_7_synthetic",
        "correct_cap_and_exact_release":True,
        "no_key_ttl":True,
        "stale_record_not_reaped":True,
        "redis_state_loss_overadmission_counterexample":True,
        "production_access":False,
        "activation_permitted":False,
    },sort_keys=True))
if __name__=="__main__":main()
