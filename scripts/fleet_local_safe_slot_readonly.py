#!/usr/bin/env python3
"""Bounded research-only fleet capacity census. Never executes inference.

Rechecks two consecutive pressure samples per host. Returning a technical
manual candidate does NOT lift quality holds or canonical production HOLD.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import re
import shlex
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

SCHEMA = "assistx.fleet-local-safe-slot-readonly.v1"
ALIASES = ("x1-370", "xwing", "lenovo", "deathstar", "joyner", "destroyer")
MODEL_IDS = {
    "xwing": "ornith-1.5-35b-a3b-apex-mtp",
    "lenovo": "lenovo-lfm-cpu",
    "deathstar": "qwen3-0.6b:latest",
    "joyner": "joyner-toolcall",
}
MIN_RAM_MIB = {
    "x1-370": 4096, "xwing": 2500, "lenovo": 3000,
    "deathstar": 4096, "joyner": 3000, "destroyer": 3000,
}
MIN_SWAP_MIB = 1024
MIN_ROOT_GIB = 5
MAX_MEM_FULL_AVG10 = 1.0
MAX_IO_FULL_AVG10 = 5.0
MAX_IO_FULL_AVG60 = 5.0
SSH_OPTIONS = [
    "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
    "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes",
    "-o", "ControlMaster=no", "-o", "ControlPath=none",
    "-o", "UpdateHostKeys=no", "-o", "PermitLocalCommand=no",
    "-o", "ConnectTimeout=3", "-o", "ServerAliveInterval=3",
]
FIELDS = {
    "sample1", "sample2", "catalog", "mode", "nonce",
}
SAMPLE_FIELDS = {
    "ram_mib", "swap_mib", "root_gib", "mem_full_avg10",
    "io_full_avg10", "io_full_avg60",
}
CATALOG_FIELDS = {"available", "resident", "reasoning_off_supported"}
HEX_NONCE = re.compile(r"[0-9a-f]{32}\Z")

REMOTE = r'''
import json,os,time,urllib.request
from pathlib import Path
MODE=NODE_MODE
NONCE=PROBE_NONCE
def sample():
 d={}
 for line in Path("/proc/meminfo").read_text().splitlines():
  if line.startswith(("MemAvailable:","SwapFree:")):
   k,v=line.split(":",1)
   d["ram_mib" if k=="MemAvailable" else "swap_mib"]=int(v.split()[0])//1024
 stat=os.statvfs("/")
 d["root_gib"]=round(stat.f_bavail*stat.f_frsize/(1024**3),2)
 for p in ("memory","io"):
  line=next(x for x in (Path("/proc/pressure")/p).read_text().splitlines()
            if x.startswith("full "))
  vals=dict(v.split("=",1) for v in line.split()[1:])
  d["mem_full_avg10" if p=="memory" else "io_full_avg10"]=float(vals["avg10"])
  if p=="io":d["io_full_avg60"]=float(vals["avg60"])
 return d
def read_api(port,path):
 try:
  with urllib.request.urlopen("http://127.0.0.1:%d%s"%(port,path),timeout=2) as r:
   if r.status!=200: return None
   raw=r.read(65537)
   if not 0<len(raw)<=65536:return None
   o=json.loads(raw)
  return o if isinstance(o,dict) else None
 except (OSError,ValueError):return None
def catalog():
 out={"available":False,"resident":False,"reasoning_off_supported":False}
 if MODE=="xwing":
  o=read_api(1234,"/api/v1/models")
  if not o or not isinstance(o.get("models"),list):return out
  matches=[m for m in o["models"] if isinstance(m,dict)
           and m.get("display_name","").startswith("Ornith")]
  if len(matches)!=1:return out
  m=matches[0]
  i=m.get("loaded_instances")
  out["available"]=True
  out["resident"]=isinstance(i,list) and len(i)==1 and i[0].get("id")=="ornith-1.5-35b-a3b-apex-mtp"
  caps=(m.get("capabilities") or {}).get("reasoning") or {}
  out["reasoning_off_supported"]="off" in (caps.get("allowed_options") or [])
 elif MODE in ("lenovo","joyner"):
  o=read_api(1234,"/v1/models")
  if not o or not isinstance(o.get("data"),list):return out
  model="lenovo-lfm-cpu" if MODE=="lenovo" else "joyner-toolcall"
  out["available"]=any(isinstance(m,dict) and m.get("id")==model for m in o["data"])
  # The advertised ID is not independent physical runtime residency.
  out["resident"]=False
 elif MODE=="deathstar":
  o=read_api(11434,"/api/tags")
  out["available"]=bool(o and isinstance(o.get("models"),list) and any(
      isinstance(m,dict) and m.get("name")=="qwen3-0.6b:latest"
      for m in o["models"]))
  o=read_api(11434,"/api/ps")
  out["resident"]=bool(o and isinstance(o.get("models"),list) and any(
      isinstance(m,dict) and m.get("name")=="qwen3-0.6b:latest"
      for m in o["models"]))
 elif MODE=="x1-370":
  o=read_api(1234,"/api/v1/models")
  out["available"]=bool(o and isinstance(o.get("models"),list) and len(o["models"])>0)
  # No runtime identity/lease check here; ALWAYS production HOLD.
 return out
try:
 first=sample()
 time.sleep(0.9)
 second=sample()
 print(json.dumps({"sample1":first,"sample2":second,
                   "catalog":catalog(),"mode":MODE,"nonce":NONCE},
                  separators=(",",":")))
except (OSError,ValueError,KeyError,StopIteration,TypeError):
 print("{}")
'''

def _is_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def parse_remote(raw: str, *, host: str, nonce: str) -> dict:
    if host not in ALIASES:
        raise ValueError("unreviewed_host")
    if not isinstance(raw, str) or not 0 < len(raw) <= 2048:
        raise ValueError("invalid_remote_length")

    def pairs_unique(items: list[tuple[str, Any]]) -> dict:
        out = {}
        for key, val in items:
            if key in out:
                raise ValueError("duplicate_remote_key")
            out[key] = val
        return out

    obj = json.loads(
        raw, object_pairs_hook=pairs_unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
    )
    if not isinstance(obj, dict) or set(obj) != FIELDS:
        raise ValueError("untrusted_remote_schema")
    if obj["nonce"] != nonce or obj["mode"] != host:
        raise ValueError("wrong_nonce_or_mode")
    for key in ("sample1", "sample2"):
        item = obj[key]
        if not isinstance(item, dict) or set(item) != SAMPLE_FIELDS:
            raise ValueError("invalid_resource_sample")
        if not all(_is_number(value) for value in item.values()):
            raise ValueError("invalid_resource_number")
        if any(item[x] > 10**6 for x in ("ram_mib", "swap_mib")):
            raise ValueError("implausible_resource_sample")
    cat = obj["catalog"]
    if not isinstance(cat, dict) or set(cat) != CATALOG_FIELDS:
        raise ValueError("invalid_catalog")
    if any(type(x) is not bool for x in cat.values()):
        raise ValueError("invalid_catalog_type")
    return obj


def evaluate(host: str, report: dict) -> dict:
    """Technical resource readiness is NOT output quality/admission approval."""
    reasons: list[str] = []
    qualification: list[str] = []
    samples = (report["sample1"], report["sample2"])
    if any(s["ram_mib"] < MIN_RAM_MIB[host] for s in samples):
        reasons.append("ram_below_manual_floor")
    if any(s["swap_mib"] < MIN_SWAP_MIB for s in samples):
        reasons.append("swap_below_manual_floor")
    if any(s["root_gib"] < MIN_ROOT_GIB for s in samples):
        reasons.append("root_disk_below_floor")
    if any(s["mem_full_avg10"] >= MAX_MEM_FULL_AVG10 for s in samples):
        reasons.append("memory_pressure")
    if any(
        s["io_full_avg10"] >= MAX_IO_FULL_AVG10
        or s["io_full_avg60"] >= MAX_IO_FULL_AVG60 for s in samples
    ):
        reasons.append("io_pressure")
    if host == "x1-370":
        reasons.append("gpu_lease_and_canonical_hold_not_evaluated")
    if host == "destroyer":
        reasons.append("no_reviewed_active_model_endpoint")
    if not report["catalog"]["available"]:
        reasons.append("reviewed_model_not_advertised")
    if host == "xwing":
        if not report["catalog"]["resident"]:
            reasons.append("ornith_not_loaded")
        if not report["catalog"]["reasoning_off_supported"]:
            reasons.append("reasoning_off_not_supported")
        qualification.append("scheduled_xwing_quality_stop_preserved")
    elif host == "deathstar":
        if not report["catalog"]["resident"]:
            qualification.append("ollama_requires_transient_manual_model_load")
        qualification.append("recent_small_model_quality_failures")
    elif host == "lenovo":
        qualification.append("advertised_model_not_physically_attested")
        qualification.append("recent_output_format_quality_failures")
    elif host == "joyner":
        qualification.append("advertised_model_not_physically_attested")
    if reasons:
        status = "HOLD"
    elif host == "xwing":
        status = "MANUAL_SHADOW_REQUALIFICATION_ONLY"
    elif host in ("lenovo", "deathstar", "joyner"):
        status = "MANUAL_QUALITY_QUALIFICATION_ONLY"
    else:
        status = "HOLD"
    return {
        "host": host,
        "resource_samples_verified": True,
        "catalog_claim_present": report["catalog"]["available"],
        "catalog_resident_claim": report["catalog"]["resident"],
        "capacity_status": status,
        "hold_reasons": reasons,
        "qualification_reasons": qualification,
        "requires_human_quality_review": True,
        "existing_xwing_manual_quality_hold_preserved": True,
        "can_run_scheduled_research": False,
        "can_dispatch_production": False,
    }


def probe(
    host: str, *, runner: Callable = subprocess.run,
    nonce_source: Callable = os.urandom,
) -> dict:
    result = {
        "host": host, "resource_samples_verified": False,
        "capacity_status": "HOLD", "hold_reasons": ["probe_unavailable"],
        "requires_human_quality_review": True,
        "existing_xwing_manual_quality_hold_preserved": True,
        "can_run_scheduled_research": False, "can_dispatch_production": False,
    }
    if host not in ALIASES:
        return result
    nonce = nonce_source(16)
    if not isinstance(nonce, bytes) or len(nonce) != 16:
        return result
    challenge = nonce.hex()
    code = "NODE_MODE=" + repr(host) + "\nPROBE_NONCE=" + repr(challenge) + "\n" + REMOTE
    if host == "x1-370":
        cmd = ["python3", "-c", code]
    else:
        cmd = ["ssh", *SSH_OPTIONS, host, "python3 -c " + shlex.quote(code)]
    try:
        proc = runner(
            cmd, capture_output=True, text=True, timeout=13, check=False
        )
        if proc.returncode != 0:
            return result
        report = parse_remote(proc.stdout, host=host, nonce=challenge)
        return evaluate(host, report)
    except (OSError, ValueError, subprocess.TimeoutExpired, TypeError):
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        rows = list(executor.map(probe, ALIASES))
    print(json.dumps({
        "schema": SCHEMA, "utc": datetime.now(UTC).isoformat(),
        "mode": "READ_ONLY_CAPACITY_ADVISORY",
        "hosts": rows,
        "technical_manual_candidates": [
            r["host"] for r in rows
            if r["capacity_status"].startswith("MANUAL_")
        ],
        "inference_requests": 0, "paid_api_calls": 0,
        "production_mutations": 0, "can_dispatch_production": False,
    }, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
