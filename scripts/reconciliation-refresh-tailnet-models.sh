#!/usr/bin/env bash
# Operator-invoked, read-only observation refresh. No service installation.
set -euo pipefail
umask 077

usage() {
  echo "usage: $0 --output /private/path/witness.json [--input candidates.json] [--ports csv] [--port-map json]" >&2
  exit 64
}
out="" input="" ports="1234,1235,1236" port_map=""
while (($#)); do
  case "$1" in
    --output) (($# >= 2)) || usage; out="$2"; shift 2 ;;
    --input) (($# >= 2)) || usage; input="$2"; shift 2 ;;
    --ports) (($# >= 2)) || usage; ports="$2"; shift 2 ;;
    --port-map) (($# >= 2)) || usage; port_map="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$out" == /* ]] || usage
[[ ! -L "$out" ]] || { echo "refuse symlink output" >&2; exit 65; }
home="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
parent="$(dirname "$out")"
mkdir -p "$parent"
# The witness names private endpoints and model IDs; do not create it in a
# shared directory even when the file itself is owner-readable only.
if [[ "$(stat -c %a "$parent")" != 700 ]]; then
  echo "private output directory must be mode 0700: $parent" >&2
  exit 65
fi
exec 9>"$parent/.observation-refresh.lock"
if ! flock -n 9; then
  echo "refresh already running; leave previous witness intact" >&2
  exit 75
fi

scratch="$(mktemp -d "$parent/.observation-XXXXXX")"
trap 'rm -rf -- "$scratch"' EXIT
if [[ -n "$input" ]]; then
  python3 "$home/reconciliation-discover-tailnet.py" --input "$input" --output "$scratch/candidates.json" >/dev/null
else
  python3 "$home/reconciliation-discover-tailnet.py" --output "$scratch/candidates.json" >/dev/null
fi
args=(--input "$scratch/candidates.json" --output "$out" --ports "$ports"
      --max-nodes 32 --workers 6 --timeout 1.0)
if [[ -n "$port_map" ]]; then args+=(--port-map "$port_map"); fi
python3 "$home/reconciliation-probe-tailnet-models.py" "${args[@]}"
(cd "$parent" && sha256sum --status -c "$(basename "$out").sha256")
echo "fresh observer receipt validated; no admission was performed"