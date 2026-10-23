#!/usr/bin/env python3
"""Read-only, fail-closed AssistX mobile Tailscale Serve routing census.

No client requests, writes, credentials, or route mutations. Always HOLD:
safe mobile SSO needs device acceptance after any ingress cutover.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess

EXPECTED_PATHS=(
    "/health",
    "/api/v1/auth/whoami",
    "/api/v1/runtime/catalog",
    "/api/v1/agent/chat/completions",
    "/api/v1/model/chat/completions",
)
EXPECTED_PORT="8443"
EXPECTED_HOST="127.0.0.1:8000"

class Rejected(ValueError):
    pass

def need(ok,reason):
    if not ok:
        raise Rejected(reason)

def classify(serve,hostname,canonical_header,allowlist_present):
    need(type(serve) is dict and type(serve.get("Web")) is dict,
         "missing Serve routing inventory")
    need(type(hostname) is str and hostname.endswith(".ts.net"),
         "unverified Tailnet DNS")
    records={}
    for host,config in serve["Web"].items():
        need(type(host) is str and type(config) is dict
             and type(config.get("Handlers")) is dict,"malformed Serve entry")
        domain,sep,port=host.rpartition(":")
        need(bool(sep) and port.isdigit(),"unparsable Serve port")
        for path,entry in config["Handlers"].items():
            need(type(path) is str and path.startswith("/")
                 and type(entry) is dict,"invalid Serve path")
            proxy=entry.get("Proxy")
            if type(proxy) is not str:
                continue
            backend=proxy.startswith("http://"+EXPECTED_HOST)
            if backend:
                need(domain==hostname and port==EXPECTED_PORT,
                     "AssistX reachable through unexpected Serve host/port")
                need(path in EXPECTED_PATHS,
                     "AssistX exposed outside the five mobile paths")
                need(proxy=="http://"+EXPECTED_HOST+path,
                     "Serve mobile path does not match backend target")
                need(path not in records,"duplicate AssistX mobile mount")
                records[path]=proxy
            elif domain==hostname and port==EXPECTED_PORT and path in EXPECTED_PATHS:
                raise Rejected("expected mobile route mapped to another backend")
    exact=set(records)==set(EXPECTED_PATHS)
    return {
        "decision":"HOLD",
        "serve_mobile_routes_exact":exact,
        "route_count":len(records),
        "tailscale_login_header_canonical":bool(canonical_header),
        "mobile_allowlist_available":bool(allowlist_present),
        "unrelated_routes_preserved":True,
        "live_mobile_client_accepted":False,
        "global_legacy_auth_setting_safe_to_change":False,
        "production_mutations":0,
        "secrets_returned":False,
    }

def call(args,timeout=9):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    need(result.returncode==0,"read-only inventory unavailable")
    return result.stdout

def physical():
    need(os.uname().nodename=="x1-370" and os.geteuid()==1000,
         "nonroot x1-370 inspection only")
    status=json.loads(call(["tailscale","status","--json"]))
    name=str((status.get("Self") or {}).get("DNSName") or "").strip().rstrip(".")
    serve=json.loads(call(["tailscale","serve","status","--json"]))
    inspect=json.loads(call(["docker","inspect","-f","{{json .Config.Env}}",
                             "assistx-api"]))
    need(type(inspect) is list and all(type(s) is str for s in inspect),
         "unexpected runtime metadata")
    flags={}
    for item in inspect:
        key,sep,value=item.partition("=")
        if key in ("TRUSTED_AUTH_HEADER","KIPNERTER_TAILNET_ALLOWED_LOGINS"):
            need(bool(sep) and key not in flags,"ambiguous auth setting")
            flags[key]=value
    canonical=(flags.get("TRUSTED_AUTH_HEADER")=="Tailscale-User-Login")
    # Does not read/print mobile login values. The mounted fallback existence
    # was separately verified: an env-empty allowlist is not proof of absence.
    env_allowlist=bool(flags.get("KIPNERTER_TAILNET_ALLOWED_LOGINS","").strip())
    report=classify(serve,name,canonical,env_allowlist)
    report["mobile_allowlist_provenance"]=(
        "environment" if env_allowlist else "mounted_fallback_not_revalidated")
    need(report["decision"]=="HOLD","unsafe automatic release decision")
    return report

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-readonly",action="store_true")
    args=parser.parse_args()
    print(json.dumps(physical() if args.physical_readonly else {
        "decision":"HOLD","physical_executed":False,"production_mutations":0,
        "global_legacy_auth_setting_safe_to_change":False},sort_keys=True))
