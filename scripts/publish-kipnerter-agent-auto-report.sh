#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

EVIDENCE_DIR="${1:-}"
[[ -n "$EVIDENCE_DIR" ]] || die "usage: $0 <evidence-dir>"
[[ -f "$EVIDENCE_DIR/validation-report.json" ]] || die "missing validation-report.json"
[[ -f "$EVIDENCE_DIR/knowledge-report.md" ]] || die "missing knowledge-report.md"

KNOWLEDGE_ROOT="${KNOWLEDGE_ROOT:-}"
if [[ -z "$KNOWLEDGE_ROOT" ]]; then
  for candidate in "$HOME/knowledge" /home/scott/knowledge /media/scott/SSD_4TB/knowledge; do
    if [[ -d "$candidate" ]]; then
      KNOWLEDGE_ROOT="$candidate"
      break
    fi
  done
fi
[[ -n "$KNOWLEDGE_ROOT" && -d "$KNOWLEDGE_ROOT" ]] || die "knowledge root not found; set KNOWLEDGE_ROOT"

PROJECT_DIR="$KNOWLEDGE_ROOT/20-Projects/kipnerter-ios"
VALIDATION_DIR="$PROJECT_DIR/validation"
EXECUTION_LOG="$PROJECT_DIR/EXECUTION-LOG-2026-09-09-RC2-GATEWAY.md"
[[ -f "$EXECUTION_LOG" ]] || die "canonical gateway execution log is missing: $EXECUTION_LOG"
mkdir -p "$VALIDATION_DIR"

STAMP="$(
  python - "$EVIDENCE_DIR/validation-report.json" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
stamp = str(data.get("timestamp_utc") or "").strip()
if not stamp:
    raise SystemExit("validation report has no timestamp_utc")
print(stamp)
PY
)"
TARGET="$VALIDATION_DIR/AGENT-AUTO-LIVE-$STAMP.md"
[[ ! -e "$TARGET" ]] || die "knowledge record already exists: $TARGET"

install -m 0644 "$EVIDENCE_DIR/knowledge-report.md" "$TARGET"

python - "$EVIDENCE_DIR/validation-report.json" "$EXECUTION_LOG" "$TARGET" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys

report_path = Path(sys.argv[1])
log_path = Path(sys.argv[2])
target = Path(sys.argv[3])
data = json.loads(report_path.read_text(encoding="utf-8"))

result = str(data.get("result") or "UNKNOWN")
source_sha = str(data.get("source_sha") or "unknown")
stamp = str(data.get("timestamp_utc") or "unknown")
stage = str(data.get("last_stage") or "unknown")
failure = data.get("failure_reason") or "none"
evidence = report_path.parent

entry = f"""

---

### {stamp} — Agent Auto live validation checkpoint

- Result: **{result}**
- Exact auto-assist SHA: `{source_sha}`
- Last completed stage: `{stage}`
- Failure reason: {failure}
- Evidence bundle: `{evidence}`
- Published validation record: `{target}`
- Serve/authority rule: no Serve reconfiguration, no client/router-admin credential
  expansion, no direct Auto-Router probe, and no runtime-catalog widening by the
  verifier.
- Reporting rule: repository CI/baseline exceptions are recorded separately;
  only a live `PASS` permits the Agent Auto healthy claim.
"""
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(entry)
PY

printf '%s\n' "$TARGET" > "$EVIDENCE_DIR/knowledge-published-path.txt"
printf 'Published knowledge record: %s\n' "$TARGET"
printf 'Updated gateway execution log: %s\n' "$EXECUTION_LOG"
