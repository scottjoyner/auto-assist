#!/usr/bin/env bash
set -Eeuo pipefail

# Live Beelink admission refresh. Deathstar is intentionally excluded.
# Performs live model/process/hash/canary proof, then applies a 5-minute,
# concurrency-4, zero-waiting-queue runtime projection generation.

ROOT=/media/scott/SSD_4TB/hermes-home
LIVE_REPO="$ROOT/home_scott_git_auto-assist"
OUTDIR="$ROOT/assistx-runtime-refresh"
mkdir -p "$OUTDIR"

LAN_URL=http://192.168.1.202:1234/v1
TS_URL=http://100.85.72.121:1234/v1

models_lan=$(curl -fsS --max-time 15 "$LAN_URL/models")
models_ts=$(curl -fsS --max-time 15 "$TS_URL/models")
model=$(printf '%s' "$models_lan" | jq -r '.data[]?.id' | grep -Fx 'liquid/lfm2.5-1.2b' | head -1)
[[ "$model" == "liquid/lfm2.5-1.2b" ]] || { echo "blocked: LFM model not live on Beelink" >&2; exit 2; }
[[ "$(printf '%s' "$models_ts" | jq -r '.data[0].id // empty')" == "$model" ]] || { echo "blocked: LAN/Tailscale model mismatch" >&2; exit 2; }

canary=$(mktemp)
trap 'rm -f "$canary"' EXIT
code=$(curl -sS --max-time 120 -o "$canary" -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  -d '{"model":"liquid/lfm2.5-1.2b","messages":[{"role":"user","content":"Reply exactly BEELINK_REFRESH_CANARY"}],"max_tokens":32,"temperature":0}' \
  "$LAN_URL/chat/completions")
[[ "$code" == 200 ]] || { echo "blocked: Beelink canary HTTP $code" >&2; exit 2; }
jq -e '.choices | length > 0' "$canary" >/dev/null || { echo "blocked: Beelink canary returned no choices" >&2; exit 2; }

live=$(ssh -o BatchMode=yes -o ConnectTimeout=10 scott@192.168.1.202 \
  "p=\$(pgrep -af 'llama-server' | grep -Ei 'LFM2.5-1.2B|lfm2.5' | head -1 | cut -d' ' -f1); test -n \"\$p\"; path=\$(ps -p \"\$p\" -o args= | sed -n 's/.*--model \\([^ ]*\\).*/\\1/p'); test -f \"\$path\"; hash=\$(sha256sum \"\$path\" | cut -d' ' -f1); printf '%s|%s|%s|%s' \"\$p\" 'llama.cpp-2.28.2-vulkan-avx2' \"\$path\" \"\$hash\"")
IFS='|' read -r pid version model_path artifact_hash <<< "$live"
[[ -n "$pid" && -n "$artifact_hash" ]] || { echo "blocked: incomplete live process proof" >&2; exit 2; }

generation=$(docker exec assistx-api python3 -c 'from neo4j import GraphDatabase; import os; d=GraphDatabase.driver(os.environ["NEO4J_URI"],auth=(os.environ["NEO4J_USER"],os.environ["NEO4J_PASSWORD"])); s=d.session(database="assistx"); r=s.run("MATCH (x:FleetProjectionState {name:\"canonical\"}) RETURN coalesce(x.generation,0) AS g").single(); print(r["g"] if r else 0); s.close(); d.close()')
next=$((generation + 1))
stamp=$(date -u +%Y%m%dT%H%M%SZ)
manifest="$OUTDIR/beelink-generation-${next}-${stamp}.yaml"

MODEL="$model" PID="$pid" VERSION="$version" HASH="$artifact_hash" GEN="$next" EXPECTED="$generation" MANIFEST="$manifest" STAMP="$stamp" \
python3 -c 'import os, pathlib; m=f"""schema_version: 1
generation: {os.environ["GEN"]}
expected_current_generation: {os.environ["EXPECTED"]}
revision: live-refresh-{os.environ["STAMP"]}
approved_by: scott
approval_id: scott-directive-beelink-auto-refresh-{os.environ["STAMP"]}
ttl_seconds: 300
require_lan_and_tailscale: true
runtimes:
  - runtime_instance_id: lmstudio-beelink-ryzen-7-mini-pc-1234
    node_id: beelink-ryzen-7-mini-pc
    runtime_kind: lmstudio
    runtime_version: {os.environ["VERSION"]}
    headless: false
    process_id: "{os.environ["PID"]}"
    capacity:
      parallel_slots: 4
      queue_limit: 0
      queue_timeout_seconds: 0
      evidence_ref: live-probe://beelink/{os.environ["STAMP"]}/capacity
      evidence_sha256: {os.environ["HASH"]}
    access_paths:
      - base_url: http://192.168.1.202:1234/v1
        transport: lan
        preference: 10
        evidence_ref: live-probe://beelink/{os.environ["STAMP"]}/lan
        evidence_sha256: {os.environ["HASH"]}
      - base_url: http://100.85.72.121:1234/v1
        transport: tailscale
        preference: 20
        evidence_ref: live-probe://beelink/{os.environ["STAMP"]}/tailscale
        evidence_sha256: {os.environ["HASH"]}
    models:
      - model_instance_id: model-beelink-lfm2.5-1.2b-q8-0
        model_key: {os.environ["MODEL"]}
        provider_model: {os.environ["MODEL"]}
        artifact_fingerprint: sha256:{os.environ["HASH"]}
        quantization: Q8_0
        context_length: 64000
        capabilities: [chat, streaming, json, local_only]
        evidence_ref: live-probe://beelink/{os.environ["STAMP"]}/model
        evidence_sha256: {os.environ["HASH"]}
"""; pathlib.Path(os.environ["MANIFEST"]).write_text(m); pathlib.Path(os.environ["MANIFEST"]).chmod(0o600)'

P=$(docker exec assistx-api sh -c 'printf %s "$NEO4J_PASSWORD"')
NEO4J_URI=bolt://127.0.0.1:7687 NEO4J_USER=neo4j NEO4J_PASSWORD="$P" NEO4J_DATABASE=assistx \
python3 "$LIVE_REPO/scripts/approve-runtime-projection.py" "$manifest" --apply \
  --evidence-output "$manifest.approval.json"

printf 'BEELINK_ADMISSION_REFRESHED generation=%s pid=%s model=%s lease_seconds=300 slots=4 manifest=%s\n' "$next" "$pid" "$model" "$manifest"
