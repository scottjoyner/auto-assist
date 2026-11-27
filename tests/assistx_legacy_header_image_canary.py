#!/usr/bin/env python3
"""Physical, offline, original-AssistX-image emergency-auth canary.

Only an explicitly requested disposable Docker build/run. The live AssistX
API, production Hermes and user-level Tailscale proxy are never changed.
No production credentials, host mounts, network or real task mutations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"src/assistx/api.py"
PROD_API="assistx-api"
PROD_HERMES="assistx-hermes-adapter"
API_IMAGE="sha256:74df40c5c720dbef283f83eaf69eae585212748ca3889be508eaa9c88cd5b4c4"
HERMES_IMAGE="sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307"
PINNED_SOURCE_BLOB="dddae05f2a27de56ac64135aa6a43ab030653fb7"

# Executed in the original-image-derived, networkless candidate container.
PROBE=r'''
import ast,hashlib,json,os
from pathlib import Path
from types import SimpleNamespace
import hmac
from fastapi.security import HTTPBasicCredentials
path=Path("/app/src/assistx/api.py")
assert path.is_file() and not path.is_symlink()
source=ast.parse(path.read_text())
fn=next(n for n in source.body
        if isinstance(n,ast.FunctionDef)
        and n.name=="_auth_user_from_credentials")
env={
    "__name__":"assistx.synthetic_runtime_auth",
    "__package__":"assistx",
    "hmac":hmac,"os":os,
    "Request":object,
    "HTTPBasicCredentials":HTTPBasicCredentials,
    "Optional":__import__("typing").Optional,
    "TRUSTED_AUTH_HEADER":"X-Synthetic-Trusted-User",
    "USER":"synthetic-admin",
    "PASS":"synthetic-password",
}
exec(compile(ast.fix_missing_locations(ast.Module(
    body=[fn],type_ignores=[])),str(path),"exec"),env)
auth=env["_auth_user_from_credentials"]
def req(header=None):
    return SimpleNamespace(
        headers=({"X-Synthetic-Trusted-User":header} if header else {}),
        method="GET", url=SimpleNamespace(path="/api/tasks"),
        scope={"state":{}},
    )
valid=HTTPBasicCredentials(username="synthetic-admin",
                           password="synthetic-password")
invalid=HTTPBasicCredentials(username="synthetic-admin",
                             password="wrong-synthetic-password")
assert os.getenv("ASSISTX_REJECT_LEGACY_TRUSTED_HEADER")=="true"
checks={
    "patched_api_file_present":path.is_file(),
    "legacy_header_alone_rejected":auth(req("synthetic-person"),None) is None,
    "header_with_valid_basic_rejected":auth(req("synthetic-person"),valid) is None,
    "valid_basic_without_header_accepted":auth(req(),valid)=="synthetic-admin",
    "invalid_basic_without_header_rejected":auth(req(),invalid) is None,
}
# Change environment in THIS disposable process only to demonstrate the
# opposite legacy fallback, not in the production container.
os.environ.pop("ASSISTX_REJECT_LEGACY_TRUSTED_HEADER",None)
checks["legacy_fallback_is_dangerous_when_disabled"]=(
    auth(req("synthetic-person"),None)=="synthetic-person")
assert all(checks.values()),checks
print(json.dumps({"decision":"HOLD","checks":checks,
                  "synthetic_credentials_only":True,
                  "network_or_provider_requests":0,
                  "task_mutations":0},sort_keys=True))
'''

class UnsafeCanary(RuntimeError):
    pass

def require(ok,why):
    if not ok:
        raise UnsafeCanary(why)

def execute(args:list[str],timeout:int=25) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args,text=True,capture_output=True,
                          timeout=timeout,check=False)

def exact_runtime(container:str,image:str,mount_count:int|None=None) -> str:
    result=execute(["docker","inspect","-f",
        "{{.Image}} {{.State.Running}} {{len .Mounts}}",container],10)
    require(result.returncode==0,"cannot inspect original running runtime")
    parts=result.stdout.strip().split()
    require(len(parts)==3 and parts[0]==image and parts[1]=="true",
            "original image not running as expected")
    if mount_count is not None:
        require(parts[2]==str(mount_count),"original mount count changed")
    return result.stdout.strip()

def physical():
    require(os.uname().nodename=="x1-370" and os.geteuid()==1000,
            "restricted x1-370 nonroot physical scope")
    observed=execute(["git","-C",str(ROOT),"hash-object",str(SOURCE)],7)
    require(observed.returncode==0
            and observed.stdout.strip()==PINNED_SOURCE_BLOB,
            "unreviewed API auth source bytes")
    api_before=exact_runtime(PROD_API,API_IMAGE)
    hermes_before=exact_runtime(PROD_HERMES,HERMES_IMAGE,16)
    unique=uuid.uuid4().hex[:12]
    label="assistx-owner-containment-"+unique
    base_alias="assistx-owner-source-"+unique
    checks={}
    try:
        tagged=execute(["docker","tag",API_IMAGE,base_alias],12)
        require(tagged.returncode==0,
                "could not create temporary reference to pinned local image")
        with tempfile.TemporaryDirectory(prefix="assistx-owner-image-canary-") as work:
            home=Path(work)
            home.chmod(0o700)
            shutil.copyfile(SOURCE,home/"api.py")
            (home/"probe.py").write_text(PROBE,encoding="utf-8")
            (home/"Dockerfile").write_text(
                "FROM "+base_alias+"\n"
                "COPY api.py /app/src/assistx/api.py\n"
                "COPY probe.py /opt/assistx-owner-probe.py\n",
                encoding="utf-8")
            built=execute(["docker","build","--network=none","--pull=false",
                 "--tag",label,"--file",str(home/"Dockerfile"),str(home)],
                 105)
            require(built.returncode==0,
                    "offline pinned API overlay failed: "+
                    (built.stderr+"\n"+built.stdout)[-900:])
            checks["offline_pinned_running_image_overlay_built"]=True
            run=execute([
                "docker","run","--rm","--network","none",
                "--user","65534:65534","--read-only",
                "--cap-drop","ALL","--security-opt","no-new-privileges",
                "--pids-limit","64","--memory","512m","--cpus","1",
                "--env","PYTHONDONTWRITEBYTECODE=1",
                "--env","ASSISTX_REJECT_LEGACY_TRUSTED_HEADER=true",
                "--entrypoint","python3",
                label,"-B","/opt/assistx-owner-probe.py",
            ],40)
            require(run.returncode==0,
                    "isolated image auth regression check rejected")
            data=json.loads(run.stdout)
            require(data.get("network_or_provider_requests")==0
                    and data.get("task_mutations")==0,
                    "synthetic canary attempted external effects")
            checks.update(data["checks"])
            checks["restricted_container_without_host_mounts_or_network"]=True
    finally:
        delete=execute(["docker","image","rm",label],18)
        if delete.returncode!=0:
            image_check=execute(["docker","image","inspect",label],8)
            require(image_check.returncode!=0,"disposable API image not deleted")
        # Never remove by source SHA. This is only a disposable local alias.
        untag=execute(["docker","image","rm",base_alias],18)
        if untag.returncode!=0:
            check=execute(["docker","image","inspect",base_alias],8)
            require(check.returncode!=0,"disposable base alias not removed")
    checks["disposable_image_cleaned"]=True
    checks["original_api_running_unchanged"]=(
        exact_runtime(PROD_API,API_IMAGE)==api_before)
    checks["original_hermes_running_unchanged"]=(
        exact_runtime(PROD_HERMES,HERMES_IMAGE,16)==hermes_before)
    require(all(checks.values()),"physical canary checks incomplete")
    return {
        "decision":"HOLD","checks":checks,
        "count_passed":sum(checks.values()),
        "task_mutations":0,"production_restarts":0,
        "network_requests_from_candidate":0,
        "credential_values_exposed":False,
    }

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-disposable-image",action="store_true")
    args=parser.parse_args()
    report=physical() if args.physical_disposable_image else {
        "decision":"HOLD","physical_executed":False,
        "production_restarts":0,"task_mutations":0,
    }
    print(json.dumps(report,sort_keys=True))
