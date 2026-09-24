#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-scottjoyner/auto-assist}"
RUNNER_NAME="${RUNNER_NAME:-x1-370-auto-assist-canary}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-auto-assist-canary}"
RUNNER_LABELS="${RUNNER_LABELS:-x1-370,assistx-canary}"
RUNNER_VERSION="${RUNNER_VERSION:-2.337.0}"
RUNNER_SHA256="${RUNNER_SHA256:-70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613}"

if [[ "$(hostname -s)" != "x1-370" ]]; then
  echo "refusing to register runner on host $(hostname -s); expected x1-370" >&2
  exit 2
fi

for cmd in curl tar gh; do
  command -v "$cmd" >/dev/null || {
    echo "missing required command: $cmd" >&2
    exit 2
  }
done

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is not authenticated; run 'gh auth login' first" >&2
  exit 2
fi

arch="$(uname -m)"
case "$arch" in
  x86_64) runner_arch="x64" ;;
  *) echo "unsupported runner architecture: $arch" >&2; exit 2 ;;
esac

os="$(uname -s)"
[[ "$os" == "Linux" ]] || {
  echo "unsupported runner OS: $os" >&2
  exit 2
}

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

archive="actions-runner-linux-${runner_arch}-${RUNNER_VERSION}.tar.gz"
url="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${archive}"

if [[ ! -x ./config.sh ]]; then
  curl -fL "$url" -o "$archive"
  printf '%s  %s\n' "$RUNNER_SHA256" "$archive" | sha256sum -c -
  tar xzf "$archive"
fi

token="$(
  gh api     --method POST     -H "Accept: application/vnd.github+json"     "/repos/${REPO}/actions/runners/registration-token"     --jq .token
)"
[[ -n "$token" && "$token" != "null" ]] || {
  echo "failed to obtain a runner registration token for $REPO" >&2
  exit 3
}

if [[ ! -f .runner ]]; then
  ./config.sh     --unattended     --replace     --url "https://github.com/${REPO}"     --token "$token"     --name "$RUNNER_NAME"     --labels "$RUNNER_LABELS"     --work "_work"
fi

if [[ "$(id -u)" -eq 0 ]]; then
  ./svc.sh install
  ./svc.sh start
  ./svc.sh status
elif command -v sudo >/dev/null; then
  sudo ./svc.sh install
  sudo ./svc.sh start
  sudo ./svc.sh status
else
  echo "runner configured but sudo is unavailable; run ./run.sh manually" >&2
  exit 4
fi
