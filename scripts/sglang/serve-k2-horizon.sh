#!/bin/bash
# serve-k2-horizon.sh — Serve K2-Horizon-MoVA-36B-A4B via llama.cpp on R9700 32GB
#
# Uses MBZUAI-IFM llama.cpp fork with ROCm 7.1 GPU support.
# Build requirements: cmake -DGGML_HIP=ON -DCMAKE_HIP_COMPILER=/opt/rocm-7.1.0/lib/llvm/bin/amdclang++
#   -DCMAKE_HIP_COMPILER_ROCM_ROOT=/opt/rocm-7.1.0 -DCMAKE_HIP_ARCHITECTURES="gfx1201"

set -euo pipefail

MODEL_NAME="K2-Horizon-MoVA-36B-A4B"
GGUF_PATH="${K2HORIZON_GGUF:-/home/scott/.lmstudio/models/NANI-Nithin/K2-Horizon-MoVA-36B-A4B-GGUF/K2-Horizon-MoVA-36B-A4B-MXFP4_MOE.gguf}"
HOST="${K2HORIZON_HOST:-0.0.0.0}"
PORT="${K2HORIZON_PORT:-30000}"
NGL="${K2HORIZON_NGL:-999}"
CTX="${K2HORIZON_CTX:-65536}"
SLOTS="${K2HORIZON_SLOTS:-2}"
DEVICE="${K2HORIZON_DEVICE:-ROCm0}"
LOG_DIR="/home/scott/git/auto-assist/logs/llama"
mkdir -p "$LOG_DIR"

LLAMA_SERVER="${LLAMA_SERVER:-/home/scott/src/llama.cpp-k2horizon/build/bin/llama-server}"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

# Validate
if [ ! -f "$LLAMA_SERVER" ]; then
  log "ERROR: llama-server not found at $LLAMA_SERVER"
  exit 1
fi

if [ ! -f "$GGUF_PATH" ]; then
  log "ERROR: GGUF not found at $GGUF_PATH"
  exit 1
fi

log "=== Starting K2-Horizon-MoVA-36B-A4B serving layer ==="
log "Model: $GGUF_PATH"
log "Target: $HOST:$PORT (device: $DEVICE, GPU layers: $NGL, ctx: $CTX, slots: $NP)"

exec "$LLAMA_SERVER" \
  -m "$GGUF_PATH" \
  -ngl "$NGL" \
  --ctx-size "$CTX" \
  --parallel "$SLOTS" \
  --host "$HOST" \
  --port "$PORT" \
  --device "$DEVICE" \
  --reasoning-preserve \
  --metrics \
  --log-disable \
  2>&1 | tee -a "$LOG_DIR/k2-horizon-gpu.log"
