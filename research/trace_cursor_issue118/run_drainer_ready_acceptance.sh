#!/usr/bin/env bash
# Research-only: verify frozen owner source and replay patch in disposable scratch.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
original="${1:-/home/scott/git/ssd4tb-nas-offload/trace_drainer_safe.py}"
expected="c2759e93f8d4e201153e92c04e93933b621ed41d153c128f79472c7cc63f726b"
actual="$(sha256sum "$original" | cut -d' ' -f1)"
if [ "$actual" != "$expected" ]; then
  echo "HOLD: source hash changed; do not apply patch" >&2
  exit 2
fi
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
mkdir -p "$scratch/tests"
cp -- "$original" "$scratch/trace_drainer_safe.py"
cp -- "$here/test_trace_drainer_ready_receipt.py" "$scratch/tests/test_trace_drainer_safe.py"
cd "$scratch"
git apply --check "$here/drainer-three-part-ready.patch"
git apply "$here/drainer-three-part-ready.patch"
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
echo "PASS: fresh patch replay; original untouched; production activation NOT authorized"
