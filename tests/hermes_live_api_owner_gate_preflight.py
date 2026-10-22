#!/usr/bin/env python3
"""Opt-in read-only proof of deployed AssistX claim/owner gate configuration.

Does not call claim/heartbeat/complete or read protected task records.
Never prints Docker environment values, tokens, passwords or user data.
Default inert HOLD. An exposed route does NOT prove authenticated acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.request

CONTAINER="assistx-api"
REQUIRED_ROUTES=(
    "/api/tasks/{task_id}/heartbeat",
    "/api/tasks/{task_id}/claim",
    "/api/tasks/{task_id}/complete",
    "/api/executor/claims/{task_id}/token",
    "/api/executor/claims/{task_id}/status",
)
REQUIRED_APP="assistx.api_router:app"

class PreflightDenied(ValueError):
    pass

def inspect_metadata(metadata: dict, routes: set[str]) -> dict:
    if type(metadata) is not dict or type(routes) is not set:
        raise PreflightDenied("unrecognized API metadata")
    config=metadata.get("Config")
    state=metadata.get("State")
    if type(config) is not dict or type(state) is not dict:
        raise PreflightDenied("missing API process state")
    raw_env=config.get("Env")
    cmd=config.get("Cmd")
    if type(state.get("Running")) is not bool or type(cmd) is not list:
        raise PreflightDenied("incomplete live process configuration")
    if type(raw_env) is not list or any(type(x) is not str for x in raw_env):
        raise PreflightDenied("invalid Docker environment metadata")
    env={}
    for entry in raw_env:
        key,delimiter,value=entry.partition("=")
        if not delimiter or key in env:
            raise PreflightDenied("ambiguous Docker config entry")
        env[key]=value
    app_is_expected=(type(cmd) is list and REQUIRED_APP in cmd
                     and "uvicorn" in cmd)
    owner_mode=(env.get("ASSISTX_REQUIRE_OWNER_BINDING") == "true")
    signed_gateway=(env.get("ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY") == "true")
    signed_gateway_key=bool(env.get("ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE"))
    signed_gateway_nonce_db=bool(env.get("ASSISTX_OWNER_GATEWAY_NONCE_DB"))
    route_set=set(REQUIRED_ROUTES).issubset(routes)
    running=(state.get("Running") is True)
    release_authorized=False  # independently administered trust is separate
    return {
        "schema":1,
        "decision":"HOLD",
        "api_running":running,
        "api_router_entrypoint_verified":app_is_expected,
        "claim_routes_registered":route_set,
        "owner_binding_enforced":owner_mode,
        "signed_owner_gateway_enabled":signed_gateway,
        "signed_gateway_key_path_configured":signed_gateway_key,
        "signed_gateway_nonce_path_configured":signed_gateway_nonce_db,
        "owner_binding_must_be_enabled_before_cutover":not owner_mode,
        "signed_gateway_must_be_qualified_before_cutover":not (
            signed_gateway and owner_mode and signed_gateway_key
            and signed_gateway_nonce_db),
        "owner_trust_custody_independent":False,
        "production_deployment_authorized":release_authorized,
        "task_mutations":0,
        "credential_values_exposed":False,
    }

def live_readonly() -> dict:
    if os.uname().nodename!="x1-370" or os.geteuid()!=1000:
        raise PreflightDenied("x1-370 nonroot research scope only")
    result=subprocess.run(["docker","inspect","--format","{{json .}}",CONTAINER],
                          capture_output=True,text=True,timeout=10,check=False)
    if result.returncode or len(result.stdout)>750000:
        raise PreflightDenied("Docker read-only metadata unavailable")
    # parse only in memory and DO NOT log raw Config.Env or token values
    raw=json.loads(result.stdout)
    paths=set()
    with urllib.request.urlopen(
        urllib.request.Request("http://127.0.0.1:8000/openapi.json",
                               method="GET"),timeout=5) as response:
        if response.status!=200:
            raise PreflightDenied("public OpenAPI contract unavailable")
        data=response.read(350_000)
        if len(data)>=350_000:
            raise PreflightDenied("oversized OpenAPI schema")
        routes=json.loads(data).get("paths",{})
        if type(routes) is not dict:
            raise PreflightDenied("invalid route catalog")
        paths=set(routes)
    return inspect_metadata(raw,paths)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-readonly",action="store_true")
    args=parser.parse_args()
    report=live_readonly() if args.physical_readonly else {
        "decision":"HOLD","physical_executed":False,
        "production_deployment_authorized":False,
    }
    print(json.dumps(report,sort_keys=True))
