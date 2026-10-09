#!/usr/bin/env bash
# Research-only gate. Pure synthetic tests: never opens operational spools.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"
for suite in test_guard_standalone.py test_source_history_coverage.py test_orphan_integrity_review.py; do
  echo "Running $suite (isolated synthetic inputs)"
  PYTHONDONTWRITEBYTECODE=1 python3 -B "$suite"
done
echo "PASS: research-only standalone gates. No production authorization."
