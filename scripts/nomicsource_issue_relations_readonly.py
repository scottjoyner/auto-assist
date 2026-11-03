#!/usr/bin/env python3
"""One-shot nonproduction source-linked issue matching on already loaded Xwing Nomic.

No language generation, no production routing/task API, no auto-issue mutations,
no model load, no paid providers. Only issue titles from an explicit JSON input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

SCHEMA = "assistx.nomicsource-issue-relations.v1"
INPUT_SCHEMA = "assistx.open-issue-matches.input.v1"
HOST = "xwing"
MODEL = "text-embedding-nomic-embed-text-v1.5"
REPOS = {"auto-assist", "auto-router"}
MAX_ISSUES = 100
MAX_INPUT_BYTES = 32_768
MAX_OUTPUT_BYTES = 12_000
MAX_RESULTS = 20
SHARED_EXCLUDED = {
    "the", "and", "for", "with", "from", "when", "into", "after",
    "before", "without", "under", "against", "this", "that",
    "source", "code", "issue", "issues", "model", "models", "data",
    "fleet", "local", "security", "review", "gate", "gates",
    "runtime", "p0", "p1", "p2", "new", "fix", "safe", "research",
    "production", "approval", "allow", "using", "support",
    "all", "two", "per", "require", "fail", "auto", "router",
    "separate", "task", "operator", "open", "needs", "related",
    "shared", "service",
}
SSH_OPTIONS = [
    "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
    "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes",
    "-o", "ControlMaster=no", "-o", "ControlPath=none",
    "-o", "UpdateHostKeys=no", "-o", "PermitLocalCommand=no",
    "-o", "ConnectTimeout=4", "-o", "ServerAliveInterval=3",
]
REMOTE = r'''
import json, math, os, re, sys, time, urllib.request
from pathlib import Path
MODEL="text-embedding-nomic-embed-text-v1.5"
MAX_RESP=3*1024*1024
def fail(reason):
 print(json.dumps({"status":"HOLD","reason":reason,"can_dispatch":False}))
 raise SystemExit(0)
def sample():
 d={}
 for line in Path("/proc/meminfo").read_text().splitlines():
  if line.startswith(("MemAvailable:","SwapFree:")):
   k,v=line.split(":",1);d[k]=int(v.split()[0])//1024
 for k in ("memory","io"):
  line=next(x for x in (Path("/proc/pressure")/k).read_text().splitlines()
            if x.startswith("full "))
  values=dict(y.split("=") for y in line.split()[1:])
  d[k+"full10"]=float(values["avg10"])
  d[k+"full60"]=float(values["avg60"])
 v=os.statvfs("/")
 d["rootGiB"]=v.f_bavail*v.f_frsize/1024**3
 return d
def safe(s):
 return (
  s.get("MemAvailable",0)>=2500 and s.get("SwapFree",0)>=1024
  and s.get("rootGiB",0)>=5 and s.get("memoryfull10",100)<1
  and s.get("iofull10",100)<5 and s.get("iofull60",100)<5
  and all(math.isfinite(float(x)) for x in s.values())
 )
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):fail("redirect_forbidden")
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def get_json(url, data=None):
 req=urllib.request.Request(url,data=data,
   headers={"Accept":"application/json","Content-Type":"application/json"},
   method="POST" if data is not None else "GET")
 with opener.open(req,timeout=13) as resp:
  if resp.status!=200:fail("model_endpoint_not_200")
  raw=resp.read(MAX_RESP+1)
 if not 0<len(raw)<=MAX_RESP:fail("model_json_size_invalid")
 def pairs_unique(pairs):
  obj={}
  for k,v in pairs:
   if k in obj:fail("model_json_duplicate_key")
   obj[k]=v
  return obj
 try:return json.loads(raw,object_pairs_hook=pairs_unique,
   parse_constant=lambda _:fail("model_json_nonfinite"))
 except (ValueError,UnicodeDecodeError):fail("model_json_invalid")
try:
 req=json.load(sys.stdin)
 if not isinstance(req,dict) or set(req)!={"nonce","titles","top_n"}:
  fail("request_schema_invalid")
 nonce=req["nonce"]
 titles=req["titles"]
 top_n=req["top_n"]
 if (not isinstance(nonce,str) or not re.fullmatch("[0-9a-f]{32}",nonce)
     or not isinstance(titles,list) or not 2<=len(titles)<=100
     or not isinstance(top_n,int) or isinstance(top_n,bool)
     or not 1<=top_n<=20):
  fail("request_values_invalid")
 if any(not isinstance(t,str) or not 5<=len(t)<=180 for t in titles):
  fail("title_invalid")
 a=sample();time.sleep(0.75);b=sample()
 if not safe(a) or not safe(b):fail("xwing_memory_swap_io_pressure_hold")
 cat=get_json("http://127.0.0.1:1234/api/v1/models")
 if not isinstance(cat,dict) or not isinstance(cat.get("models"),list):
  fail("native_model_catalog_invalid")
 nomic=[m for m in cat["models"] if isinstance(m,dict)
        and m.get("display_name","").startswith("Nomic Embed Text")]
 if len(nomic)!=1 or not isinstance(nomic[0].get("loaded_instances"),list):
  fail("nomic_instance_not_verified")
 instances=nomic[0]["loaded_instances"]
 if len(instances)!=1 or instances[0].get("id")!=MODEL:
  fail("nomic_not_loaded_once")
 start=time.monotonic()
 d=get_json("http://127.0.0.1:1234/v1/embeddings",
    json.dumps({"model":MODEL,"input":titles}).encode())
 data=d.get("data") if isinstance(d,dict) else None
 if not isinstance(data,list) or len(data)!=len(titles):
  fail("embedding_count_invalid")
 vectors=[None]*len(titles)
 dimensions=None
 for entry in data:
  if not isinstance(entry,dict) or type(entry.get("index")) is not int:
   fail("embedding_index_invalid")
  ix=entry["index"]
  if not 0<=ix<len(titles) or vectors[ix] is not None:
   fail("embedding_index_duplicate")
  v=entry.get("embedding")
  if not isinstance(v,list) or not 8<=len(v)<=4096:
   fail("embedding_dimensions_invalid")
  if dimensions is None:dimensions=len(v)
  if len(v)!=dimensions or any(type(x) not in (int,float)
           or not math.isfinite(x) for x in v):
   fail("embedding_values_invalid")
  vectors[ix]=v
 norms=[math.sqrt(sum(x*x for x in v)) for v in vectors]
 if any(not math.isfinite(n) or n<1e-9 for n in norms):
  fail("embedding_norm_invalid")
 pairs=[]
 for i in range(len(titles)):
  for j in range(i+1,len(titles)):
   dot=sum(x*y for x,y in zip(vectors[i],vectors[j]))
   score=dot/(norms[i]*norms[j])
   if not math.isfinite(score):fail("similarity_invalid")
   pairs.append([i,j,round(max(-1,min(1,score)),5)])
 pairs.sort(key=lambda x:(-x[2],x[0],x[1]))
 print(json.dumps({"status":"EMBEDDINGS_COMPLETE_RESEARCH_ONLY",
   "model":MODEL,"nonce":nonce,"count":len(titles),
   "dimensions":dimensions,"duration_seconds":round(time.monotonic()-start,3),
   "pairs":pairs[:min(len(pairs),max(150,top_n*12))],
   "reported_usage":d.get("usage") if isinstance(d.get("usage"),dict) else None,
   "can_dispatch":False},separators=(",",":")))
except (OSError,ValueError,TypeError,KeyError,StopIteration):
 fail("local_embedding_request_unavailable")
'''


class ResearchHold(ValueError):
    pass


def deny(reason: str) -> None:
    raise ResearchHold(reason)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def parse_input(raw: bytes) -> list[dict]:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_INPUT_BYTES:
        deny("input_size_invalid")

    def unique_pairs(items: list[tuple[str, Any]]) -> dict:
        out = {}
        for k, v in items:
            if k in out:
                deny("input_duplicate_json_field")
            out[k] = v
        return out

    try:
        source = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique_pairs,
            parse_constant=lambda _: deny("input_nonfinite_json"),
        )
    except ResearchHold:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResearchHold("input_json_invalid") from exc
    if not isinstance(source, dict) or set(source) != {"schema", "issues"}:
        deny("input_schema_invalid")
    if source["schema"] != INPUT_SCHEMA:
        deny("input_version_invalid")
    items = source["issues"]
    if not isinstance(items, list) or not 2 <= len(items) <= MAX_ISSUES:
        deny("issue_count_invalid")
    seen = set()
    for issue in items:
        if not isinstance(issue, dict) or set(issue) != {
            "repo", "number", "title", "source_url"
        }:
            deny("issue_record_schema_invalid")
        repo = issue["repo"]
        number = issue["number"]
        title = issue["title"]
        url = issue["source_url"]
        if (not isinstance(repo, str) or repo not in REPOS
                or type(number) is not int or not 1 <= number < 999999):
            deny("issue_identity_invalid")
        if not isinstance(title, str) or not 5 <= len(title) <= 180:
            deny("issue_title_invalid")
        if any(ord(x) < 32 or ord(x) == 127 for x in title):
            deny("issue_title_control_characters")
        if not isinstance(url, str) or url != (
            f"https://github.com/scottjoyner/{repo}/issues/{number}"
        ):
            deny("source_url_mismatch")
        key = (repo, number)
        if key in seen:
            deny("issue_duplicate")
        seen.add(key)
    return items


def token_set(title: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]{3,}", title.lower())
        if t not in SHARED_EXCLUDED and not t.isdigit()
    }


def validate_remote(reply: str, *, nonce: str, count: int) -> dict:
    if not isinstance(reply, str) or not 0 < len(reply) <= MAX_OUTPUT_BYTES:
        deny("remote_reply_size_invalid")
    def unique_pairs(items: list[tuple[str, Any]]) -> dict:
        out = {}
        for k, v in items:
            if k in out:
                deny("remote_duplicate_json_field")
            out[k] = v
        return out

    try:
        d = json.loads(
            reply, object_pairs_hook=unique_pairs,
            parse_constant=lambda _: deny("nonfinite_remote_json"),
        )
    except ResearchHold:
        raise
    except json.JSONDecodeError as exc:
        raise ResearchHold("remote_json_invalid") from exc
    if not isinstance(d, dict) or d.get("can_dispatch") is not False:
        deny("remote_authority_untrusted")
    if d.get("status") == "HOLD":
        deny("remote_residency_resource_or_embedding_hold")
    if d.get("status") != "EMBEDDINGS_COMPLETE_RESEARCH_ONLY":
        deny("remote_status_untrusted")
    if (d.get("model") != MODEL or d.get("nonce") != nonce
            or type(d.get("count")) is not int or d["count"] != count):
        deny("remote_model_nonce_or_count_untrusted")
    dimensions = d.get("dimensions")
    if type(dimensions) is not int or not 8 <= dimensions <= 4096:
        deny("remote_dimensions_invalid")
    duration = d.get("duration_seconds")
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
        deny("duration_invalid")
    pairs = d.get("pairs")
    if not isinstance(pairs, list) or not 0 < len(pairs) <= 240:
        deny("remote_pairs_invalid")
    seen = set()
    prev_score = 2.0
    for p in pairs:
        if not isinstance(p, list) or len(p) != 3:
            deny("remote_pair_shape_invalid")
        i, j, score = p
        if type(i) is not int or type(j) is not int or not 0 <= i < j < count:
            deny("remote_pair_indices_invalid")
        if type(score) not in (int, float) or not -1 <= score <= 1:
            deny("remote_similarity_invalid")
        if (i, j) in seen or score > prev_score + 1e-6:
            deny("remote_pair_order_invalid")
        seen.add((i, j))
        prev_score = score
    return d


def rank_links(items: list[dict], result: dict, limit: int = MAX_RESULTS) -> list[dict]:
    review = []
    for i, j, similarity in result["pairs"]:
        a, b = items[i], items[j]
        if a["repo"] == b["repo"]:
            continue
        common = sorted(token_set(a["title"]) & token_set(b["title"]))
        # Both cross-repository semantic and a concrete lexical anchor,
        # mitigating generic all-purpose model similarity.
        if similarity < 0.58 or len(common) < 1:
            continue
        review.append({
            "left": f"{a['repo']}#{a['number']}",
            "right": f"{b['repo']}#{b['number']}",
            "left_url": a["source_url"],
            "right_url": b["source_url"],
            "left_title": a["title"],
            "right_title": b["title"],
            "similarity": similarity,
            "common_subject_tokens": common[:6],
            "review_status": "HUMAN_REVIEW_ONLY",
            "duplicate_claim_verified": False,
            "production_action_authorized": False,
        })
        if len(review) >= limit:
            break
    return review


def request_embeddings(
    items: list[dict], *,
    runner: Callable = subprocess.run,
    nonce_factory: Callable = os.urandom,
) -> dict:
    nonce_bytes = nonce_factory(16)
    if not isinstance(nonce_bytes, bytes) or len(nonce_bytes) != 16:
        deny("nonce_unavailable")
    nonce = nonce_bytes.hex()
    request = {
        "nonce": nonce, "titles": [i["title"] for i in items],
        "top_n": MAX_RESULTS,
    }
    remote = ["ssh", *SSH_OPTIONS, HOST, "python3 -c " + shlex.quote(REMOTE)]
    try:
        completed = runner(
            remote, input=canonical(request).decode(), capture_output=True,
            text=True, timeout=29, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResearchHold("strict_ssh_or_model_timeout") from exc
    if completed.returncode != 0:
        deny("remote_probe_unavailable")
    return validate_remote(completed.stdout, nonce=nonce, count=len(items))


def build_report(items: list[dict], result: dict) -> dict:
    return {
        "schema": SCHEMA,
        "observed_utc": datetime.now(UTC).isoformat(),
        "input_sha256": hashlib.sha256(canonical(items)).hexdigest(),
        "source_issue_count": len(items),
        "source_repos": sorted({r["repo"] for r in items}),
        "local_model": MODEL,
        "model_reported_embedding_dimensions": result["dimensions"],
        "embedding_batch_seconds": result["duration_seconds"],
        "embedding_requests": 1,
        # Many providers report 0/absent usage for embeddings; do not
        # manufacture input tokens from bytes, words or vector counts.
        "embedding_token_count_proven": False,
        "verified_embedding_tokens": None,
        "review_candidates": rank_links(items, result),
        "review_only": True,
        "production_dispatch": False,
        "scheduled_research_authorized": False,
        "paid_api_calls": 0,
        "github_issue_mutations": 0,
        "production_graph_writes": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true",
        help="one bounded manual research batch, requires already-loaded model",
    )
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        items = parse_input(raw)
        if not args.execute:
            print(json.dumps({
                "schema": SCHEMA, "mode": "DRY_RUN", "issue_count": len(items),
                "inference_requests": 0, "github_issue_mutations": 0,
                "production_dispatch": False,
            }))
            return 0
        result = request_embeddings(items)
        print(json.dumps(build_report(items, result), sort_keys=True))
        return 0
    except ResearchHold as exc:
        print(json.dumps({
            "schema": SCHEMA, "status": "HOLD",
            "reason": str(exc), "production_dispatch": False,
        }))
        return 2


if __name__ == "__main__":
    sys.exit(main())
