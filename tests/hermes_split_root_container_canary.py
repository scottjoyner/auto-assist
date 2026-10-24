#!/usr/bin/env python3
"""Research-only: overlay three reviewed source files onto the exact local
running Hermes image and execute synthetic split-root adapter twice.
NO production container, mounts, credentials, providers or network traffic.
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "assistx"
SCRATCH = pathlib.Path("/home/scott/.cache")
IMAGE_ID = "sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307"
LIVE = "assistx-hermes-adapter"

def cmd(args, seconds=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=seconds)

def need(ok, msg):
    if not ok:
        raise RuntimeError(msg)

SMOKE = r'''
import json
import os
from pathlib import Path
from assistx.agents import hermes_agent_adapter as ad
from assistx import swarm_memory
r = Path("/research/knowledge")
w = Path("/workspace")
source = r / "decision.md"
assert os.geteuid() == 1000 and ad.KNOWLEDGE_SPLIT_ENABLED
assert ad.KNOWLEDGE_READ_ROOT == str(r)
assert ad.KNOWLEDGE_WRITE_ROOT == str(w / "knowledge")
assert "SYNTHETIC_CONTEXT_42" in ad._gather_knowledge_context()
assert source.read_text() == "SYNTHETIC_CONTEXT_42\n"
assert not Path("/media/scott/SSD_4TB").exists()
assert not Path("/media/scott/NAS5").exists()
try:
    assert not Path("/root/.ssh/id_hermes").exists()
except PermissionError:
    pass
assert "NoNewPrivs:\t1" in Path("/proc/self/status").read_text()
try:
    (r / "forbidden.md").write_text("fail")
    raise AssertionError("read-only input writable")
except OSError:
    pass
try:
    Path("/etc/forbidden-hermes").write_text("fail")
    raise AssertionError("rootfs writable")
except OSError:
    pass
ad.fleet = None
ad.swarm_memory = None
ad.call_self_task_llm = lambda *_a, **_k: {
    "success": True,
    "output": "SYNTHETIC structured summary, not a real provider result.",
    "elapsed": 0.01,
}
ad.process_self_task(None, "bulk_summarize")
assert (w / "knowledge" / "SUMMARY.md").is_file()
assert not (r / "SUMMARY.md").exists()
reg = ad.load_eval()
count = reg["models"]["synthetic/model"]["tasks"]["self:bulk_summarize"]["runs"]
stage = w / "stage.json"
if not stage.exists():
    assert count == 1
    stage.write_text(json.dumps({"stage": 1, "model_calls": 0, "provider_calls": 0}) + "\n")
    print("HERMES_ACTUAL_SPLIT_ROOT_STAGE_1_PASS", flush=True)
else:
    assert json.loads(stage.read_text())["stage"] == 1
    assert count == 2
    assert source.read_text() == "SYNTHETIC_CONTEXT_42\n"
    assert not (w / "knowledge" / "synthetic_model").exists()
    stage.write_text(json.dumps({"stage": 2, "model_calls": 0, "provider_calls": 0}) + "\n")
    print("HERMES_ACTUAL_SPLIT_ROOT_STAGE_2_PASS", flush=True)
'''
def physical():
    need(os.uname().nodename == "x1-370" and os.geteuid() == 1000, "x1-370 UID 1000 only")
    before = cmd(["docker", "inspect", "-f", "{{.Image}} {{.State.Running}}", LIVE])
    need(before.returncode == 0 and before.stdout.strip() == IMAGE_ID + " true",
         "production service image or running state changed")
    ident = uuid.uuid4().hex[:16]
    tag = "og-hermes-split-" + ident
    base_tag = "og-hermes-split-base-" + ident
    name = "og-hermes-split-test-" + ident
    checks = {}
    with tempfile.TemporaryDirectory(prefix="og-hermes-split-", dir=SCRATCH) as directory:
        root=pathlib.Path(directory)
        root.chmod(0o755)
        ctx=root / "build"
        ctx.mkdir()
        sources = [
            ("knowledge_roots.py", "knowledge_roots.py"),
            ("swarm_memory.py", "swarm_memory.py"),
            ("agents/hermes_agent_adapter.py", "hermes_agent_adapter.py"),
        ]
        for original, staged in sources:
            shutil.copyfile(SOURCE / original, ctx / staged)
            (ctx / staged).chmod(0o644)
            need((ctx / staged).read_bytes() == (SOURCE / original).read_bytes(),
                 "candidate code changed during staging")
        dockerfile = (
            "FROM " + base_tag + "\n"
            "COPY knowledge_roots.py /app/src/assistx/knowledge_roots.py\n"
            "COPY swarm_memory.py /app/src/assistx/swarm_memory.py\n"
            "COPY hermes_agent_adapter.py /app/src/assistx/agents/hermes_agent_adapter.py\n"
        )
        (ctx / "Dockerfile").write_text(dockerfile)
        work=root / "work"
        knowledge=root / "knowledge"
        work.mkdir(); knowledge.mkdir()
        (knowledge / "decision.md").write_text("SYNTHETIC_CONTEXT_42\n")
        (work / "smoke.py").write_text(SMOKE)
        for f in (knowledge / "decision.md", work / "smoke.py"):
            f.chmod(0o644)
        built=False; created=False; base_created=False
        try:
            need(cmd(["docker","image","inspect",tag],6).returncode != 0, "tag exists")
            need(cmd(["docker","container","inspect",name],6).returncode != 0, "container exists")
            need(cmd(["docker","image","inspect",base_tag],6).returncode != 0, "base tag exists")
            pin=cmd(["docker", "tag", IMAGE_ID, base_tag], 10)
            need(pin.returncode == 0, "cannot tag exact local running image")
            base_created=True
            b=cmd(["docker","build","--pull=false","--network=none","--no-cache",
                   "--tag",tag,"--file",str(ctx/"Dockerfile"),str(ctx)],90)
            need(b.returncode == 0, "offline candidate build failed: " + b.stderr[-350:])
            built=True
            source_id=cmd(["docker","image","inspect","-f","{{.Id}}",tag],7)
            need(source_id.returncode == 0 and source_id.stdout.strip().startswith("sha256:"),
                 "missing exact candidate digest")
            create=[
                "docker","create","--pull=never","--name",name,
                "--network=none","--read-only","--user=1000:1000",
                "--cap-drop=ALL","--security-opt=no-new-privileges:true",
                "--pids-limit=32","--memory=384m","--cpus=0.5",
                "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16777216",
                "--mount",f"type=bind,src={knowledge},dst=/research/knowledge,readonly",
                "--mount",f"type=bind,src={work},dst=/workspace",
                "--env=HERMES_KNOWLEDGE_READ_ROOT=/research/knowledge",
                "--env=HERMES_KNOWLEDGE_WRITE_ROOT=/workspace/knowledge",
                "--env=HERMES_KNOWLEDGE_ROOT=/workspace/knowledge",
                "--env=HERMES_EVAL_PATH=/workspace/knowledge/eval.json",
                "--env=HERMES_SELFTASK_BULK_MODELS=synthetic/model",
                "--env=PYTHONDONTWRITEBYTECODE=1",
                "--env=ASSISTX_OUTBOX_DB=/workspace/outbox.sqlite",
                "--env=HOME=/workspace",
                "--workdir=/workspace","--entrypoint=python3",
                tag,"-B","/workspace/smoke.py"
            ]
            cr=cmd(create,15)
            need(cr.returncode==0, "cannot create disposable worker: "+cr.stderr[-450:])
            created=True
            host=cmd(["docker","inspect","-f","{{json .HostConfig}}",name],7)
            mounts=cmd(["docker","inspect","-f","{{json .Mounts}}",name],7)
            cfg=cmd(["docker","inspect","-f","{{json .Config}}",name],7)
            h=json.loads(host.stdout);m=json.loads(mounts.stdout);c=json.loads(cfg.stdout)
            need(h["NetworkMode"]=="none" and h["ReadonlyRootfs"] and not h["Privileged"]
                 and "ALL" in h["CapDrop"] and c["User"]=="1000:1000"
                 and len(m)==2 and {e["Source"] for e in m}=={str(knowledge),str(work)}
                 and next(e for e in m if e["Destination"]=="/research/knowledge")["RW"] is False,
                 "unsafe disposable execution settings")
            checks["candidate_isolated"]=True
            for stage in (1,2):
                start=cmd(["docker","start","--attach",name],30)
                code=cmd(["docker","inspect","-f","{{.State.ExitCode}}",name],7)
                marker=f"HERMES_ACTUAL_SPLIT_ROOT_STAGE_{stage}_PASS"
                need(start.returncode==0 and code.stdout.strip()=="0"
                     and marker in start.stdout.splitlines(),
                     "actual candidate stage "+str(stage)+" failed: "+(start.stdout+start.stderr)[-900:])
            checks["two_actual_image_processes_pass"]=True
            need(json.loads((work/"stage.json").read_text())=={
                "stage":2,"model_calls":0,"provider_calls":0}, "journal invalid")
            checks["isolated_write_restart"]=True
        finally:
            if created:cmd(["docker","rm","-f",name],15)
            if built:cmd(["docker","image","rm",tag],15)
            if base_created:cmd(["docker","image","rm",base_tag],15)
            checks["all_objects_removed"]=(cmd(["docker","container","inspect",name],7).returncode!=0
                and cmd(["docker","image","inspect",tag],7).returncode!=0
                and cmd(["docker","image","inspect",base_tag],7).returncode!=0)
    after=cmd(["docker","inspect","-f","{{.Image}} {{.State.Running}}",LIVE])
    checks["production_unchanged"]=(after.returncode==0 and after.stdout.strip()==before.stdout.strip())
    need(all(checks.values()),"canary incomplete: "+str(checks))
    return {"decision":"HOLD","physical_checks":checks,
            "production_deployment_authorized":False,
            "real_agent_tasks_executed":0,"provider_requests":0}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--physical-disposable",action="store_true")
    a=p.parse_args()
    if not a.physical_disposable:
        print(json.dumps({"decision":"HOLD","physical_executed":False}))
    else:
        print(json.dumps(physical(),sort_keys=True,indent=2))
