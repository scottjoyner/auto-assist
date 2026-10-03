#!/bin/bash
# serve-qwen38.sh — Serve Qwen3.8 Flash via SGLang on R9700
#
# Two modes:
#   --small   : Qwen3.8-27B-NVFP4 (17GB, fits in 32GB VRAM, fast)
#   --large   : Qwen3.8-Flash-Next-131B-A6B (61GB, VRAM+RAM offload, big brain)
#
# Default: --large (user wants the bigger model)

set -euo pipefail

MODE="${1:---large}"
HOST="${SGLANG_HOST:-0.0.0.0}"
PORT="${SGLANG_PORT:-30001}"
LOG_DIR="/home/scott/git/auto-assist/logs/sglang"
mkdir -p "$LOG_DIR"

SMALL_GGUF="/home/scott/.lmstudio/models/esatapedico/Qwen3.8-27B-NVFP4-MTP-GGUF/Qwen3.8-27B-NVFP4-MTP-HIGH.gguf"
LARGE_GGUF="/home/scott/.lmstudio/models/Cyronius/Qwen3.8-Flash-Next-131B-A6B-GGUF/qwen38-keep1-Q3KXL.gguf"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

case "$MODE" in
  --small)
    log "=== Qwen3.8-27B-NVFP4 on R9700 (VRAM-only, 17GB) ==="
    if [ ! -f "$SMALL_GGUF" ]; then
      log "ERROR: GGUF not found at $SMALL_GGUF"
      exit 1
    fi
    exec sglang serve "$SMALL_GGUF" \
      --host "$HOST" \
      --port "$PORT" \
      --load-format gguf \
      --trust-remote-code \
      --dtype auto \
      --mem-fraction-static 0.90 \
      --context-length 32768 \
      --max-running-requests 8 \
      --disable-cuda-graph \
      --log-level info \
      2>&1 | tee -a "$LOG_DIR/qwen38-small.log"
    ;;

  --large)
    log "=== Qwen3.8-Flash-Next-131B-A6B on R9700 (VRAM+RAM, 61GB) ==="
    if [ ! -f "$LARGE_GGUF" ]; then
      log "ERROR: GGUF not found at $LARGE_GGUF"
      exit 1
    fi

    # 131B-A6B: 61GB total. 32GB VRAM, ~29GB offloaded to RAM.
    # R9700 has 58GB free RAM after lemonade kill.
    # mem-fraction-static controls how much VRAM is used for KV cache.
    # cpu-offload-gb controls how many GB of weights stay on CPU.
    OFFLOAD_GB=${QWEN38_OFFLOAD_GB:-29}
    log "Offloading ${OFFLOAD_GB}GB to CPU, keeping ~32GB on GPU"

    exec sglang serve "$LARGE_GGUF" \
      --host "$HOST" \
      --port "$PORT" \
      --load-format gguf \
      --trust-remote-code \
      --dtype auto \
      --cpu-offload-gb "$OFFLOAD_GB" \
      --mem-fraction-static 0.85 \
      --context-length 16384 \
      --max-running-requests 2 \
      --disable-cuda-graph \
      --log-level info \
      2>&1 | tee -a "$LOG_DIR/qwen38-large.log"
    ;;

  *)
    echo "Usage: $0 [--small|--large]"
    echo "  --small  : Qwen3.8-27B (17GB, VRAM-only, fast)"
    echo "  --large  : Qwen3.8-131B-A6B (61GB, VRAM+RAM, big)"
    exit 1
    ;;
esac
