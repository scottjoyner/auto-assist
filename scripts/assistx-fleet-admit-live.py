#!/usr/bin/env python3
"""Fail-closed live renewal for the x1/R9700 production runtime projection."""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

EXPECTED_APP_VERSION = "0.4.24+1"
EXPECTED_ORNITH_ID = "ornith-1.5-35b-a3b-apex-mtp"
EXPECTED_ORNITH_INDEX = (
    "mudler/Ornith-1.5-35B-A3B-APEX-MTP-GGUF/"
    "Ornith-1.5-35B-A3B-APEX-MTP-Quality.gguf"
)
EXPECTED_ORNITH_SIZE = 23_718_411_552
EXPECTED_ORNITH_SHA = "c83373e4c502c6d4929339406ffbb1f442598b153a3ade02b628752e00a282fd"
ORNITH_PATH = Path(
    "/home/scott/.lmstudio/models/mudler/Ornith-1.5-35B-A3B-APEX-MTP-GGUF/"
    "Ornith-1.5-35B-A3B-APEX-MTP-Quality.gguf"
)
APPROVE_SCRIPT = Path("/home/scott/git/auto-assist/scripts/approve-runtime-projection.py")
APPROVE_SCRIPT_SHA256 = "5df9efa67d2292d978193915e9e9b0a9d04d884d8ba37f758b3b0733c8f800f3"
LAN_BASE = "http://192.168.1.237:1234/v1"
TS_BASE = "http://100.64.43.123:1234/v1"
LMS_BIN = Path("/home/scott/.lmstudio/bin/lms")
STATE = Path("/home/scott/.local/state/assistx-live-admission")
STATE.mkdir(parents=True, exist_ok=True)


def fail(message: str) -> "NoReturn":
    print(f"FLEET_ADMISSION_BLOCKED: {message}", file=sys.stderr)
    raise SystemExit(2)


def output(command: list[str], *, text: bool = True) -> str:
    try:
        return subprocess.check_output(command, text=text, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc:
        fail(f"command failed: {command[0]} rc={exc.returncode}")


def current_deployment() -> tuple[Path, Path]:
    raw = json.loads(output(["docker", "inspect", "assistx-api"]))[0]
    mounts = {m["Destination"]: Path(m["Source"]) for m in raw.get("Mounts", [])}
    artifacts = mounts.get("/app/artifacts")
    src = mounts.get("/app/src")
    if artifacts is None or src is None:
        fail("assistx-api deployment mounts are not discoverable")
    repo = src.parent
    if repo / "artifacts" != artifacts:
        fail("assistx-api source/artifact mounts do not share one worktree")
    return repo, artifacts
def sha256_cached(path: Path) -> str:
    st = path.stat()
    cache_path = STATE / "ornith-quality-sha256.json"
    key = {"inode": st.st_ino, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        if cached.get("key") == key and cached.get("sha256") == EXPECTED_ORNITH_SHA:
            return EXPECTED_ORNITH_SHA
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(block)
    digest = h.hexdigest()
    if digest != EXPECTED_ORNITH_SHA:
        fail(f"Ornith Quality artifact hash changed: {digest}")
    cache_path.write_text(json.dumps({"key": key, "sha256": digest}, sort_keys=True))
    return digest


def probe_models(base: str) -> list[str]:
    with urllib.request.urlopen(base + "/models", timeout=4) as response:
        if response.status != 200:
            fail(f"{base} returned HTTP {response.status}")
        data = json.loads(response.read())
    ids = [str(row.get("id")) for row in data.get("data", []) if isinstance(row, dict)]
    required = {EXPECTED_ORNITH_ID}
    if not required.issubset(set(ids)):
        fail(f"{base} missing required resident models: {sorted(required - set(ids))}")
    return ids


def completion_canary(base: str) -> dict:
    payload = json.dumps({
        "model": EXPECTED_ORNITH_ID,
        "stream": False,
        "temperature": 0,
        "max_tokens": 768,
        "messages": [{
            "role": "user",
            "content": "Synthetic runtime-admission canary only. Do not call tools or change state. Reply with the single word OK.",
        }],
    }).encode()
    request = urllib.request.Request(
        base + "/chat/completions", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 200:
            fail(f"completion canary returned HTTP {response.status}")
        doc = json.loads(response.read())
    choice = (doc.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = str(message.get("content") or "").strip()
    if choice.get("finish_reason") != "stop" or content != "OK":
        fail("completion canary did not return exact OK/stop")
    reasoning = str(message.get("reasoning_content") or "")
    return {
        "http_status": 200,
        "finish_reason": "stop",
        "output_length": len(content),
        "output_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "reasoning_length": len(reasoning),
    }


def write_evidence(path: Path, document: dict) -> str:
    raw = json.dumps(document, indent=2, sort_keys=True) + "\n"
    path.write_text(raw)
    return hashlib.sha256(raw.encode()).hexdigest()


lock = (STATE / "renew.lock").open("w")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    fail("another live admission renewal is already running")

if not LMS_BIN.is_file() or not LMS_BIN.stat().st_mode & 0o111:
    fail(f"LM Studio CLI missing or not executable: {LMS_BIN}")

repo, artifacts_root = current_deployment()
approve_script = APPROVE_SCRIPT
if not approve_script.is_file():
    fail(f"pinned approval script missing: {approve_script}")
if hashlib.sha256(approve_script.read_bytes()).hexdigest() != APPROVE_SCRIPT_SHA256:
    fail("approval script checksum drifted; operator review required")

app_meta = json.loads(Path("/opt/LM-Studio/resources/app/package.json").read_text())
if app_meta.get("version") != EXPECTED_APP_VERSION:
    fail(f"LM Studio version drift: {app_meta.get('version')!r}")

ps = json.loads(output([str(LMS_BIN), "ps", "--json"]))
ls_rows = json.loads(output([str(LMS_BIN), "ls", "--json"]))
orn_matches = [
    x for x in ps
    if x.get("modelKey") == EXPECTED_ORNITH_ID
    and x.get("indexedModelIdentifier") == EXPECTED_ORNITH_INDEX
]
if len(orn_matches) != 1:
    fail(f"expected exactly one resident Ornith Quality instance, found {len(orn_matches)}")
orn = orn_matches[0]
orn_index_matches = [
    x for x in ls_rows
    if x.get("modelKey") == EXPECTED_ORNITH_ID
    and x.get("indexedModelIdentifier") == EXPECTED_ORNITH_INDEX
]
if len(orn_index_matches) != 1:
    fail("Ornith Quality indexed checkpoint identity is not unique")
orn_index = orn_index_matches[0]
if int(orn_index.get("sizeBytes") or 0) != EXPECTED_ORNITH_SIZE:
    fail("Ornith Quality checkpoint size drifted")
if int(orn.get("contextLength") or 0) != 262144 or int(orn.get("parallel") or 0) != 16:
    fail("Ornith Quality context/parallel configuration drifted")
if not ORNITH_PATH.is_file() or ORNITH_PATH.stat().st_size != EXPECTED_ORNITH_SIZE:
    fail("Ornith Quality checkpoint path/size drifted")
orn_checkpoint_sha = sha256_cached(ORNITH_PATH)

lan_models = probe_models(LAN_BASE)
ts_models = probe_models(TS_BASE)
canary = completion_canary(LAN_BASE)
pid = int(output(["pgrep", "-o", "-f", "/opt/LM-Studio/lm-studio --run-as-service"]))
stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
capture = dt.datetime.now(dt.timezone.utc).isoformat()
run_dir = artifacts_root / "runtime" / "live-keepalive" / stamp
run_dir.mkdir(parents=True, exist_ok=False)
runtime_sha = write_evidence(run_dir / "x1-runtime.json", {
    "captured_at": capture, "node_id": "x1-370", "runtime_kind": "lmstudio",
    "runtime_pid": pid, "lm_studio_app_version": EXPECTED_APP_VERSION,
    "models": [EXPECTED_ORNITH_ID], "canaries": {EXPECTED_ORNITH_ID: canary},
    "evidence_only": True,
})
capacity_sha = write_evidence(run_dir / "x1-capacity.json", {
    "captured_at": capture, "parallel_slots": 1, "queue_limit": 0,
    "queue_timeout_seconds": 0, "runtime_parallel": 16,
    "ornith_context": 262144, "basis": "conservative single-slot lease over exact live Quality instance",
    "evidence_only": True,
})
lan_sha = write_evidence(run_dir / "x1-lan.json", {
    "captured_at": capture, "base_url": LAN_BASE, "models": lan_models, "evidence_only": True,
})
ts_sha = write_evidence(run_dir / "x1-tailscale.json", {
    "captured_at": capture, "base_url": TS_BASE, "models": ts_models, "evidence_only": True,
})
orn_sha = write_evidence(run_dir / "x1-ornith-quality.json", {
    "captured_at": capture, "model_key": EXPECTED_ORNITH_ID,
    "indexed_model_identifier": EXPECTED_ORNITH_INDEX, "size_bytes": EXPECTED_ORNITH_SIZE,
    "checkpoint_sha256": orn_checkpoint_sha, "quantization": "lmstudio-quality-custom",
    "context_length": 262144, "parallel": 16, "evidence_only": True,
})
generation_code = (
    "from neo4j import GraphDatabase; import os; "
    "d=GraphDatabase.driver(os.environ['NEO4J_URI'],auth=(os.environ['NEO4J_USER'],os.environ['NEO4J_PASSWORD'])); "
    "s=d.session(database=os.environ.get('NEO4J_DATABASE','assistx')); "
    "r=s.run(\"MATCH (x:FleetProjectionState {name:'canonical'}) RETURN coalesce(x.generation,0) AS g\").single(); "
    "print(r['g'] if r else 0); s.close(); d.close()"
)
generation = int(output(["docker", "exec", "assistx-api", "python", "-c", generation_code]))
next_generation = generation + 1
rel = run_dir.relative_to(artifacts_root)
manifest = run_dir / f"runtime-projection-generation-{next_generation}.yaml"
orn_fp = f"sha256:{orn_checkpoint_sha}"
manifest.write_text(f"""schema_version: 1
generation: {next_generation}
expected_current_generation: {generation}
revision: fleet-live-x1-main-{stamp}
approved_by: scott
approval_id: chat-directive-20261002-fleet-takeover-x1-main-{stamp}
ttl_seconds: 900
require_lan_and_tailscale: true
runtimes:
  - runtime_instance_id: x1-lmstudio-1234
    node_id: x1-370
    runtime_kind: lmstudio
    runtime_version: lm-studio/{EXPECTED_APP_VERSION}
    headless: false
    process_id: {pid}
    capacity:
      parallel_slots: 1
      queue_limit: 0
      queue_timeout_seconds: 0
      evidence_ref: artifacts/{rel}/x1-capacity.json
      evidence_sha256: {capacity_sha}
""")
with manifest.open("a") as handle:
    handle.write(f"""    access_paths:
      - base_url: {LAN_BASE}
        transport: lan
        preference: 10
        evidence_ref: artifacts/{rel}/x1-lan.json
        evidence_sha256: {lan_sha}
      - base_url: {TS_BASE}
        transport: tailscale
        preference: 20
        evidence_ref: artifacts/{rel}/x1-tailscale.json
        evidence_sha256: {ts_sha}
    models:
      - model_instance_id: model-x1-ornith-quality-c83373e4
        model_key: {EXPECTED_ORNITH_ID}
        provider_model: {EXPECTED_ORNITH_ID}
        artifact_fingerprint: "{orn_fp}"
        quantization: lmstudio-quality-custom
        context_length: 262144
        capabilities: [chat, streaming, code, reasoning, tool_use, local_only, hermes_worker]
        evidence_ref: artifacts/{rel}/x1-ornith-quality.json
        evidence_sha256: {orn_sha}
""")
container_manifest = "/app/artifacts/" + str(manifest.relative_to(artifacts_root))
if os.getenv("DRY_RUN") == "1":
    print(
        f"FLEET_ADMISSION_DRY_RUN generation={next_generation} "
        f"runtime=x1-lmstudio-1234 manifest={manifest}"
    )
    raise SystemExit(0)

approval = subprocess.run(
    ["docker", "exec", "-i", "assistx-api", "python", "-", container_manifest, "--apply"],
    input=approve_script.read_bytes(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
if approval.returncode != 0:
    fail("approval CAS/validation failed: " + approval.stdout.decode(errors="replace")[-1200:])

signed = output([
    "docker", "exec", "assistx-api", "python", "-c",
    "from assistx.runtime_projection_v2 import build_runtime_projection; "
    "from assistx.neo4j_client import Neo4jClient; import json; "
    "print(json.dumps(build_runtime_projection(Neo4jClient,ttl_seconds=900)))",
])
validation = subprocess.run(
    ["docker", "exec", "-i", "git-router-1", "python", "-c",
     "import json,sys; from auto_router.runtime_projection_v2 import validate_projection_document; "
     "d,_=validate_projection_document(json.load(sys.stdin)); print('VALID',d.generation)"],
    input=signed.encode(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
if validation.returncode != 0 or b"VALID" not in validation.stdout:
    fail("router signature validation failed")

time.sleep(6)
health = json.loads(urllib.request.urlopen("http://127.0.0.1:8088/health", timeout=4).read())
if not health.get("ok") or int(health.get("providers_enabled") or 0) < 1:
    fail("router did not activate the renewed projection")
print(f"FLEET_ADMISSION_REFRESHED generation={next_generation} nodes=1 lease_seconds=900 runtime=x1-lmstudio-1234")
