#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"
command -v python3 >/dev/null
command -v tar >/dev/null
command -v zstd >/dev/null
command -v git >/dev/null

expected="fba0d51b9a73c9de5f4a69705b2c325b18417cd8e051400e8c30ec31f3bcdbfe"
actual="$(sha256sum baseline-trace-spool-capture.py | cut -d' ' -f1)"
test "$actual" = "$expected"
python3 -B sandbox_fixture.py > baseline-acceptance.log 2>&1
python3 -B test_candidate_collector.py > candidate-acceptance.log 2>&1

scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
cp baseline-trace-spool-capture.py "$scratch/trace-spool-capture.py"
cp baseline-trace-spool-capture.py sandbox_fixture.py test_candidate_collector.py "$scratch/"
(
  cd "$scratch"
  git apply --check "$here/collector-release-candidate.patch"
  git apply "$here/collector-release-candidate.patch"
  cmp trace-spool-capture.py "$here/trace-spool-capture.py"
  cmp cursor_window_guard.py "$here/cursor_window_guard.py"
  cmp seal_index.py "$here/seal_index.py"
  python3 -B test_candidate_collector.py > "$here/patch-replay-acceptance.log" 2>&1
)
test "$(sha256sum baseline-trace-spool-capture.py | cut -d' ' -f1)" = "$expected"
printf 'BASELINE: %s\n' "$(grep '^Ran ' baseline-acceptance.log | tail -1)"
printf 'CANDIDATE: %s\n' "$(grep '^Ran ' candidate-acceptance.log | tail -1)"
printf 'REPLAY: %s\n' "$(grep '^Ran ' patch-replay-acceptance.log | tail -1)"
echo "PASS: source unchanged, original suite, candidate suite, fresh patch replay."