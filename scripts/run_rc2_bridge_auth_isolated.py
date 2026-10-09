#!/usr/bin/env python3
"""Disposable same-Docker-network authentication differential; NO live API.

Requires exact clean Git HEAD and immutable local Docker image SHA256 IDs.
Creates an internal-only bridge with two temporary, sequential AssistX servers
(strict and legacy), and a synthetic HTTP peer. Never mounts .env or keys.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NETWORK = "assistx-bridge-auth-proof-internal-20261009"
STRICT = "assistx-bridge-strict-synthetic-20261009"
LEGACY = "assistx-bridge-legacy-synthetic-20261009"
SERVER_IMAGE = "git-assistx:latest"
CLIENT_IMAGE = "python:3.12-slim"
HEX = re.compile(r"^[0-9a-f]{40}$")
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
CID = re.compile(r"^[a-f0-9]{64}$")

PEER = r'''
import base64,sys,time,urllib.error,urllib.request
host, mode = sys.argv[1:]
url = "http://" + host + ":8000"
def status(path, headers=None):
    req = urllib.request.Request(url+path,headers=headers or {})
    try:
        with urllib.request.urlopen(req,timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0
for _ in range(40):
    if status("/fleet-dashboard") != 0:
        break
    time.sleep(.25)
identity = {"Tailscale-User-Login": "synthetic-forged-identity"}
wrong = base64.b64encode(b"synthetic-test-only-operator:wrong").decode()
valid = base64.b64encode(
    b"synthetic-test-only-operator:synthetic-test-only-password"
).decode()
observed = {
    "anonymous": status("/fleet-dashboard"),
    "forged": status("/fleet-dashboard", identity),
    "wrong_basic": status(
        "/fleet-dashboard", {**identity,"Authorization": "Basic "+wrong}
    ),
    "valid_basic": status(
        "/fleet-dashboard", {**identity,"Authorization": "Basic "+valid}
    ),
    "traces_forged": status("/traces",identity),
    "trace_index_forged": status("/api/traces",identity),
    "trace_detail_forged": status(
        "/api/traces/synthetic-no-real-graph",identity
    ),
    "dashboard_api_forged": status("/api/fleet/dashboard",identity),
}
for k,v in observed.items():
    print(k.upper(),v,flush=True)
if mode == "strict":
    expected = {
        "anonymous":401,"forged":401,"wrong_basic":401,
        "valid_basic":200,"traces_forged":401,
        "trace_index_forged":401,"trace_detail_forged":401,
        "dashboard_api_forged":401,
    }
    assert observed == expected, "strict backend negative check failed"
    print("BRIDGE_STRICT_BASIC_DENIAL_PASS")
else:
    # The API might return 500 after legacy auth succeeds because the
    # disposable test has no graph. Its status does not authorize access.
    assert observed["anonymous"] == 401
    assert observed["forged"] == 200
    assert observed["wrong_basic"] == 200
    assert observed["traces_forged"] == 200
    # The disposable API has no graph: a 500 after auth is expected and
    # *never* evidence of successful data access. It is not an auth denial.
    assert observed["trace_index_forged"] not in (0,401,403)
    assert observed["trace_detail_forged"] not in (0,401,403)
    print("BRIDGE_LEGACY_HEADER_NEGATIVE_CONTROL_PASS")
'''


def call(*args: str, timeout: int = 20) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args, capture_output=True, timeout=timeout, check=False
    )


def okay(*args: str, timeout: int = 20) -> str:
    p = call(*args, timeout=timeout)
    if p.returncode:
        raise RuntimeError("isolated Docker/Git command failed")
    return p.stdout.decode("utf-8", "replace").strip()


def missing(kind: str, name: str) -> bool:
    return call("docker", kind, "inspect", name, timeout=8).returncode != 0


def ensure_network(nid: str, expected: set[str]) -> None:
    item = json.loads(okay("docker", "network", "inspect", nid))[0]
    if (item.get("Id") != nid or item.get("Name") != NETWORK
            or item.get("Internal") is not True
            or item.get("Attachable") is True
            or item.get("Ingress") is True):
        raise RuntimeError("not an approved isolated Docker network")
    members = {x.get("Name") for x in (item.get("Containers") or {}).values()}
    if members != expected:
        raise RuntimeError("unexpected peer on isolated bridge")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--approve-disposable-bridge-auth", action="store_true")
    ap.add_argument("--expected-source-sha", required=True)
    ap.add_argument("--server-image-id", required=True)
    ap.add_argument("--client-image-id", required=True)
    args = ap.parse_args(argv)
    if not (args.approve_disposable_bridge_auth
            and isinstance(args.expected_source_sha,str)
            and HEX.fullmatch(args.expected_source_sha)
            and isinstance(args.server_image_id,str)
            and SHA.fullmatch(args.server_image_id)
            and isinstance(args.client_image_id,str)
            and SHA.fullmatch(args.client_image_id)):
        print("HOLD: missing approval or immutable identities")
        return 2
    try:
        if okay("git","-C",str(ROOT),"rev-parse","HEAD") != args.expected_source_sha:
            raise RuntimeError("source mismatch")
        if okay("git","-C",str(ROOT),"status","--porcelain"):
            raise RuntimeError("uncommitted source")
        for image,pin in ((SERVER_IMAGE,args.server_image_id),
                          (CLIENT_IMAGE,args.client_image_id)):
            if okay("docker","image","inspect",image,
                    "--format","{{.Id}}") != pin:
                raise RuntimeError("image identity mismatch")
        if not all((ROOT / p).is_dir() for p in ("src","static","templates")):
            raise RuntimeError("missing isolated source surface")
        if not missing("network",NETWORK):
            raise RuntimeError("network name already exists")
        if any(not missing("container",n) for n in (STRICT,LEGACY)):
            raise RuntimeError("unowned disposable container")
    except (OSError,RuntimeError,ValueError,subprocess.SubprocessError):
        print("HOLD: source or image custody failed")
        return 2

    network_id = None
    owned: list[str] = []
    try:
        network_id = okay("docker","network","create","--internal",NETWORK)
        if not CID.fullmatch(network_id):
            raise RuntimeError("invalid network ID")
        ensure_network(network_id,set())
        for name,mode in ((STRICT,"strict"),(LEGACY,"legacy")):
            cid = okay(
                "docker","run","-d","--pull","never","--name",name,
                "--network",NETWORK,"--read-only","--workdir","/tmp",
                "--tmpfs","/tmp:rw,size=64m,mode=1777",
                "--cpus","0.35","--memory","768m","--pids-limit","64",
                "--cap-drop","ALL","--security-opt","no-new-privileges",
                "--mount",f"type=bind,source={ROOT/'src'},target=/work/src,readonly",
                "--mount",f"type=bind,source={ROOT/'static'},target=/work/static,readonly",
                "--mount",f"type=bind,source={ROOT/'templates'},target=/work/templates,readonly",
                "-e","HOME=/tmp","-e","XDG_CACHE_HOME=/tmp",
                "-e","ASSISTX_OUTBOX_DB=/tmp/synthetic-only-outbox.db",
                "-e","PYTHONDONTWRITEBYTECODE=1","-e","PYTHONPATH=/work/src",
                "-e",("ASSISTX_REQUIRE_BASIC_AUTH=1"
                      if mode=="strict" else "ASSISTX_REQUIRE_BASIC_AUTH=0"),
                "-e","TRUSTED_AUTH_HEADER=Tailscale-User-Login",
                "-e","BASIC_AUTH_USER=synthetic-test-only-operator",
                "-e","BASIC_AUTH_PASS=synthetic-test-only-password",
                "--entrypoint","python",args.server_image_id,
                "-m","uvicorn","assistx.api:app","--host","0.0.0.0",
                "--port","8000","--lifespan","off","--no-access-log",
            )
            if not CID.fullmatch(cid):
                raise RuntimeError("invalid synthetic container ID")
            owned.append(cid)
            obj = json.loads(okay("docker","inspect",cid))[0]
            host = obj.get("HostConfig") or {}
            if (obj.get("Name") != "/" + name
                    or obj.get("Image") != args.server_image_id
                    or host.get("NetworkMode") != NETWORK
                    or not host.get("ReadonlyRootfs")
                    or bool(host.get("PortBindings"))
                    or host.get("Privileged")):
                raise RuntimeError("disposable API topology not proven")
            ensure_network(network_id,{name})
            peer = call(
                "docker","run","--rm","--pull","never",
                "--network",NETWORK,"--read-only",
                "--tmpfs","/tmp:rw,size=8m","--cpus","0.1",
                "--memory","128m","--pids-limit","16",
                "--cap-drop","ALL","--security-opt","no-new-privileges",
                "--entrypoint","python",args.client_image_id,
                "-c",PEER,name,mode,timeout=40,
            )
            out = peer.stdout.decode("utf-8","replace")
            marker = ("BRIDGE_STRICT_BASIC_DENIAL_PASS" if mode=="strict"
                      else "BRIDGE_LEGACY_HEADER_NEGATIVE_CONTROL_PASS")
            if peer.returncode or marker not in out:
                raise RuntimeError("isolated API negative test failed")
            print(marker)
            call("docker","stop","-t","1",cid,timeout=8)
            call("docker","rm","-f",cid,timeout=8)
            owned.remove(cid)
            ensure_network(network_id,set())
        print("BRIDGE_AUTH_DIFFERENTIAL_DISPOSABLE_PASS")
        return 0
    except (OSError,RuntimeError,ValueError,KeyError,subprocess.SubprocessError):
        print("BRIDGE_AUTH_PROOF_FAILED_OR_INCONCLUSIVE")
        return 1
    finally:
        for cid in reversed(owned):
            if CID.fullmatch(cid):
                call("docker","stop","-t","1",cid,timeout=8)
                call("docker","rm","-f",cid,timeout=8)
        if network_id and CID.fullmatch(network_id):
            call("docker","network","rm",network_id,timeout=9)


if __name__ == "__main__":
    sys.exit(main())
