#!/usr/bin/env python3
"""Offline, CPU-only reproducibility check. Not LM Studio qualification or admission.

Uses an EXISTING local Docker image and llama.cpp binary, with Docker
network_mode='none'. No host port published and no routing credentials mounted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

MODEL = Path("/home/scott/.lmstudio/models/IFM/K2-Horizon-0.9B-GGUF/K2-Horizon-1B-BF16.gguf")
BIN_DIR = Path("/home/scott/git/esp32-lfm3-lab-20261028/.cache/llama-cpu-build/bin")
LIB_DIR = Path("/lib/x86_64-linux-gnu")
IMAGE = "python:3.12-slim-bookworm"
IMAGE_ID = "sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337"
SERVER_SHA256 = "c55245a646cff688acb7093f33a697def1a2838057ec3eab1ab727411fa3f3cd"
SERVER_IMPL_SHA256 = "69ff01e98c50ca4d5bd740aba410badeb20c48365bc27a956b52602f7d60e341"
LLAMA_LIB_SHA256 = "10d2d52e159880233eef51ed3aea15822ac8d964cbb68c4855a58eed70699000"
CPU_LIB_SHA256 = "512aa00bed9c4cd8de24ea0a5f193987a12ce46aef8f8ed24e11a7fad0f52894"
SHA256 = "371010db1807bb07b62e738422ee0de26c1e15a347f31108ed2c6e219095a8b8"
STATE = Path("/home/scott/.local/state/assistx-admission-preflight")
CLIENT = r'''
import concurrent.futures,json,time,urllib.request,urllib.error,hashlib
URL="http://127.0.0.1:18721/v1/chat/completions"
S=["Reply with exactly OK.","What is 3 plus 4? Reply with a number.","Continue: red, blue, green. Name one color.","Say READY."]
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(i):
    body=json.dumps({"model":"k2-cpu-repro","messages":[{"role":"user","content":S[i]}],
       "temperature":0,"stream":False,"max_tokens":48}).encode()
    t0=time.monotonic()
    try:
        req=urllib.request.Request(URL,data=body,headers={"Content-Type":"application/json"},method="POST")
        with opener.open(req,timeout=40) as resp:
            raw=resp.read(131073)
            code=resp.status
        if len(raw)>131072: raise ValueError("response_exceeds_bound")
        value=json.loads(raw)
        if not isinstance(value,dict) or value.get("error") is not None: raise ValueError("response_error")
        u=value.get("usage") or {}
        ins=u.get("prompt_tokens")
        outs=u.get("completion_tokens")
        total=u.get("total_tokens")
        verified=all(type(n) is int and n>=0 for n in (ins,outs,total))
        verified=verified and outs<=48 and total>=ins+outs and outs>0
        options=value.get("choices") or []
        first=options[0] if options and isinstance(options[0],dict) else {}
        msg=first.get("message") or {}
        content=msg.get("content") if isinstance(msg,dict) else None
        return {"case":i,"http":code,"usage_valid":verified,
                "prompt_tokens":ins if verified else None,
                "completion_tokens":outs if verified else None,
                "total_tokens":total if verified else None,
                "text_visible":bool(isinstance(content,str) and content.strip()),
                "finish_reason":str(first.get("finish_reason") or "")[:24],
                "wall_seconds":round(time.monotonic()-t0,3)}
    except Exception as e:
        return {"case":i,"error_type":type(e).__name__,"usage_valid":False}
seq=[request(0),request(1)]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    concurrent=list(pool.map(request,[2,3]))
print(json.dumps({"sequential":seq,"concurrent":concurrent},sort_keys=True))
'''


def run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(8*1024**2),b""):
            h.update(block)
    return h.hexdigest()


def guard() -> list[str]:
    errors=[]
    try:
        if sha256_file(MODEL)!=SHA256: errors.append("model_sha256_mismatch")
    except OSError:
        errors.append("model_unavailable")
    for filename, expected in (
        ("llama-server", SERVER_SHA256),
        ("libllama-server-impl.so", SERVER_IMPL_SHA256),
        ("libllama.so.0.8.0", LLAMA_LIB_SHA256),
        ("libggml-cpu.so.0.28.0", CPU_LIB_SHA256),
    ):
        try:
            if sha256_file(BIN_DIR / filename) != expected:
                errors.append("cpu_binary_fingerprint_mismatch_" + filename)
        except OSError:
            errors.append("cpu_binary_unavailable_" + filename)
    if not LIB_DIR.is_dir(): errors.append("host_dynamic_library_path_unavailable")
    try:
        stat=os.statvfs("/")
        if stat.f_bavail*stat.f_frsize < 15*1024**3:
            errors.append("insufficient_root_free_space")
        with open("/proc/meminfo",encoding="ascii") as m:
            available=next(int(line.split()[1])*1024 for line in m if line.startswith("MemAvailable:"))
        if available < 16*1024**3: errors.append("insufficient_available_memory")
    except (OSError,ValueError,StopIteration):
        errors.append("resource_evidence_missing")
    try:
        inspect=run(["docker","image","inspect",IMAGE,"--format","{{.Id}}"],timeout=8)
        if inspect.returncode!=0:
            errors.append("pinned_local_container_image_missing")
        elif inspect.stdout.strip() != IMAGE_ID:
            errors.append("pinned_local_container_image_identity_mismatch")
    except (OSError,subprocess.TimeoutExpired):
        errors.append("docker_image_unavailable")
    return errors


def docker_args(name: str) -> list[str]:
    return [
        "docker","run","--rm","-d","--pull","never","--name",name,
        "--network","none","--read-only","--cap-drop","ALL",
        "--security-opt","no-new-privileges","--pids-limit","64",
        "--cpus","3","--memory","6g","--memory-swap","6g",
        "--user","1000:1000","--tmpfs","/tmp:rw,nosuid,size=96m",
        "--mount",f"type=bind,src={BIN_DIR},dst=/llama,readonly",
        "--mount",f"type=bind,src={LIB_DIR},dst=/hostlib,readonly",
        "--mount",f"type=bind,src={MODEL},dst=/models/k2.gguf,readonly",
        IMAGE_ID, "/hostlib/ld-linux-x86-64.so.2","--library-path","/hostlib:/llama",
        "/llama/llama-server","--model","/models/k2.gguf",
        "--alias","k2-cpu-repro","--host","127.0.0.1",
        "--port","18721","--ctx-size","4096","--parallel","2",
        "--threads","3","--n-gpu-layers","0","--no-webui",
    ]


def run_experiment() -> dict:
    report={"kind":"k2-cpu-network-none-repro-v1","source":"synthetic_only",
            "backend":"llama.cpp CPU-only, NOT LM Studio",
            "model_sha256_expected":SHA256,
            "docker_image_id_expected":IMAGE_ID,
            "llama_server_sha256_expected":SERVER_SHA256,
            "llama_impl_sha256_expected":SERVER_IMPL_SHA256,
            "llama_lib_sha256_expected":LLAMA_LIB_SHA256,
            "ggml_cpu_sha256_expected":CPU_LIB_SHA256,
            "context_tokens":4096,"parallel_slots":2,
            "production_traffic":0,"model_approvals":0,
            "can_dispatch":False,"live_trace_use":False,
            "status":"HOLD","cleanup":False}
    blocks=guard()
    if blocks:
        report["reasons"]=blocks
        return report
    name="k2-cpu-repro-"+uuid.uuid4().hex[:10]
    launched=False
    try:
        started=run(docker_args(name),timeout=20)
        if started.returncode!=0:
            report["reason"]="container_start_failed"
            return report
        launched=True
        config=run(["docker","inspect",name,"--format","{{json .HostConfig}}"],timeout=10)
        if config.returncode!=0:
            report["reason"]="container_isolation_unverified"
            return report
        host=json.loads(config.stdout)
        binds=host.get("PortBindings") or {}
        report["network_none_verified"]=(
            host.get("NetworkMode")=="none" and not binds
            and host.get("ReadonlyRootfs") is True
            and host.get("Privileged") is False
            and host.get("CapDrop") == ["ALL"]
        )
        if not report["network_none_verified"]:
            report["reason"]="unexpected_container_network_permissions"
            return report
        healthy=False
        for _ in range(24):
            check=run(["docker","exec",name,"python","-c",
               "import urllib.request; u=urllib.request.build_opener(urllib.request.ProxyHandler({}));"
               "print(u.open('http://127.0.0.1:18721/health',timeout=3).status)"],timeout=7)
            if check.returncode==0 and check.stdout.strip()=="200":
                healthy=True
                break
            ps=run(["docker","inspect",name,"--format","{{.State.Running}}"],timeout=5)
            if ps.returncode!=0 or ps.stdout.strip()!="true":break
            time.sleep(2)
        report["server_healthy"]=healthy
        if not healthy:
            report["reason"]="runtime_not_healthy_within_bound"
            return report
        data=run(["docker","exec",name,"python","-c",CLIENT],timeout=105)
        if data.returncode:
            report["reason"]="synthetic_client_failed"
            return report
        if len(data.stdout)>16000:
            report["reason"]="synthetic_results_oversized"
            return report
        parsed=json.loads(data.stdout)
        cases=parsed.get("sequential",[])+parsed.get("concurrent",[])
        report["observations"]=cases
        valid= len(cases)==4 and all(item.get("usage_valid") is True and item.get("http")==200 for item in cases)
        report["measured_prompt_tokens"]=sum(i["prompt_tokens"] for i in cases if i.get("usage_valid"))
        report["measured_output_tokens"]=sum(i["completion_tokens"] for i in cases if i.get("usage_valid"))
        report["status"]="CROSS_RUNTIME_SYNTHETIC_PASS" if valid else "HOLD_PARTIAL_SYNTHETIC"
        return report
    except (OSError,ValueError,KeyError,subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
        report["reason"]="exception_"+type(exc).__name__
        return report
    finally:
        if launched:
            stopped=run(["docker","rm","-f",name],timeout=15)
            report["cleanup"]=stopped.returncode==0 or "No such container" in stopped.stderr
            # Even if model served correctly, cleanup uncertainty is a HOLD.
            if not report["cleanup"]:
                report["status"]="HOLD_CLEANUP_UNVERIFIED"


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--evidence-output",type=Path)
    args=parser.parse_args()
    if not args.execute:
        print(json.dumps({"mode":"DRY_RUN_ONLY","container_created":False,"can_dispatch":False}))
        return 0
    if args.evidence_output is None:
        parser.error("--execute requires --evidence-output")
    out=args.evidence_output.absolute()
    if out.parent != STATE or out.exists() or out.is_symlink():
        parser.error("output must be new and directly within the evidence folder")
    report=run_experiment()
    encoded=json.dumps(report,indent=2,sort_keys=True)+"\n"
    fd=os.open(out,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as stream:stream.write(encoded)
    print(json.dumps({"status":report["status"],"reason":report.get("reason"),
                      "network_none_verified":report.get("network_none_verified",False),
                      "cleanup":report["cleanup"],
                      "measured_prompt_tokens":report.get("measured_prompt_tokens"),
                      "measured_output_tokens":report.get("measured_output_tokens"),
                      "evidence_sha256":hashlib.sha256(encoded.encode()).hexdigest(),
                      "evidence_filename":out.name},sort_keys=True))
    return 0 if report["status"]=="CROSS_RUNTIME_SYNTHETIC_PASS" and report["cleanup"] else 1


if __name__=="__main__":
    sys.exit(main())
