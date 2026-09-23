#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

SOURCE="docs/KIPNERTER_AGENT_AUTO_ACCEPTANCE.md"
[[ -f "$SOURCE" ]] || die "run from the auto-assist repository root; missing $SOURCE"

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
EXECUTION_LOG="$PROJECT_DIR/EXECUTION-LOG-2026-09-09-RC2-GATEWAY.md"
TARGET="$PROJECT_DIR/AGENT-AUTO-VALIDATION-AND-REPORTING.md"
[[ -d "$PROJECT_DIR" ]] || die "missing Kipnerter knowledge project: $PROJECT_DIR"
[[ -f "$EXECUTION_LOG" ]] || die "canonical gateway execution log is missing: $EXECUTION_LOG"

install -m 0644 "$SOURCE" "$TARGET"

SOURCE_SHA="$(git rev-parse HEAD)"
MARKER="<!-- agent-auto-validation-contract -->"
if ! grep -Fq "$MARKER" "$EXECUTION_LOG"; then
  cat >> "$EXECUTION_LOG" <<EOF

---

$MARKER
### Agent Auto validation/reporting contract

The canonical acceptance and reporting contract is mirrored at:

`$TARGET`

Source: auto-assist `docs/KIPNERTER_AGENT_AUTO_ACCEPTANCE.md`
Source revision at publication: `$SOURCE_SHA`

This contract separates repository validation, exact-source x1-370 deployment,
and live Tailnet/Hermes validation. Only an exact-SHA live PASS paired with an
accepted repository-validation classification permits the Agent Auto healthy
claim. Per-attempt results are published separately under
`20-Projects/kipnerter-ios/validation/`.
EOF
fi

printf 'Published contract: %s\n' "$TARGET"
printf 'Knowledge checkpoint: %s\n' "$EXECUTION_LOG"
