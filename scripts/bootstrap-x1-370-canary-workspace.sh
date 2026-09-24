#!/usr/bin/env bash
set -euo pipefail

EXPECTED_HOST="${EXPECTED_HOST:-x1-370}"
REPO="${REPO:-scottjoyner/auto-assist}"
REGISTER_RUNNER="${REGISTER_RUNNER:-1}"
GIT_ROOT="${GIT_ROOT:-$HOME/git}"

AUTO_ASSIST_URL="${AUTO_ASSIST_URL:-https://github.com/scottjoyner/auto-assist.git}"
AUTO_ROUTER_URL="${AUTO_ROUTER_URL:-https://github.com/scottjoyner/auto-router.git}"

AUTO_ASSIST_REPO="${AUTO_ASSIST_REPO:-$GIT_ROOT/auto-assist}"
AUTO_ROUTER_REPO="${AUTO_ROUTER_REPO:-$GIT_ROOT/auto-router}"

OPS_BRANCH="${OPS_BRANCH:-agent/kipnerter-model-handle-resolution}"
ASSISTX_RUNTIME_SHA="${ASSISTX_RUNTIME_SHA:-aad657bbf4fff5bec2080ada07f5a4ad02292743}"
AUTO_ROUTER_RUNTIME_SHA="${AUTO_ROUTER_RUNTIME_SHA:-1fbb9726de46a30c0c91616c65c04dc1f35845a6}"

OPS_WORKTREE="${OPS_WORKTREE:-$GIT_ROOT/auto-assist-canary-ops}"
ASSISTX_RUNTIME_WORKTREE="${ASSISTX_RUNTIME_WORKTREE:-$GIT_ROOT/auto-assist-canary-runtime}"
AUTO_ROUTER_RUNTIME_WORKTREE="${AUTO_ROUTER_RUNTIME_WORKTREE:-$GIT_ROOT/auto-router-canary-runtime}"

if [[ "$(hostname -s)" != "$EXPECTED_HOST" ]]; then
  echo "refusing canary bootstrap on host $(hostname -s); expected $EXPECTED_HOST" >&2
  exit 2
fi

for cmd in git curl; do
  command -v "$cmd" >/dev/null || {
    echo "missing required command: $cmd" >&2
    exit 2
  }
done

mkdir -p "$GIT_ROOT"

ensure_clone() {
  local url="$1"
  local repo="$2"

  if [[ ! -e "$repo" ]]; then
    echo "Cloning $url -> $repo"
    git clone "$url" "$repo"
  elif [[ ! -d "$repo/.git" ]]; then
    echo "$repo exists but is not a normal Git clone; refusing to touch it" >&2
    exit 3
  fi

  git -C "$repo" remote get-url origin >/dev/null
  git -C "$repo" fetch origin --prune --tags
}

ensure_detached_worktree() {
  local repo="$1"
  local path="$2"
  local commit="$3"

  git -C "$repo" cat-file -e "$commit^{commit}"

  if [[ -e "$path" ]]; then
    git -C "$path" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
      echo "$path exists but is not a Git worktree; refusing to touch it" >&2
      exit 4
    }
    if [[ -n "$(git -C "$path" status --porcelain)" ]]; then
      echo "$path is dirty; refusing to overwrite canary evidence workspace" >&2
      git -C "$path" status --short >&2
      exit 4
    fi
    git -C "$path" checkout --detach "$commit"
  else
    git -C "$repo" worktree add --detach "$path" "$commit"
  fi

  test "$(git -C "$path" rev-parse HEAD)" = "$commit"
  test -z "$(git -C "$path" status --porcelain)"
}

ensure_branch_worktree() {
  local repo="$1"
  local path="$2"
  local remote_ref="$3"
  local commit

  commit="$(git -C "$repo" rev-parse "$remote_ref")"

  if [[ -e "$path" ]]; then
    git -C "$path" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
      echo "$path exists but is not a Git worktree; refusing to touch it" >&2
      exit 5
    }
    if [[ -n "$(git -C "$path" status --porcelain)" ]]; then
      echo "$path is dirty; refusing to overwrite canary ops workspace" >&2
      git -C "$path" status --short >&2
      exit 5
    fi
    git -C "$path" checkout --detach "$commit"
  else
    git -C "$repo" worktree add --detach "$path" "$commit"
  fi

  test "$(git -C "$path" rev-parse HEAD)" = "$commit"
}

ensure_clone "$AUTO_ASSIST_URL" "$AUTO_ASSIST_REPO"
ensure_clone "$AUTO_ROUTER_URL" "$AUTO_ROUTER_REPO"

git -C "$AUTO_ASSIST_REPO" fetch origin   "$OPS_BRANCH"   "$ASSISTX_RUNTIME_SHA"
git -C "$AUTO_ROUTER_REPO" fetch origin "$AUTO_ROUTER_RUNTIME_SHA"

ensure_branch_worktree   "$AUTO_ASSIST_REPO"   "$OPS_WORKTREE"   "origin/$OPS_BRANCH"

ensure_detached_worktree   "$AUTO_ASSIST_REPO"   "$ASSISTX_RUNTIME_WORKTREE"   "$ASSISTX_RUNTIME_SHA"

ensure_detached_worktree   "$AUTO_ROUTER_REPO"   "$AUTO_ROUTER_RUNTIME_WORKTREE"   "$AUTO_ROUTER_RUNTIME_SHA"

cat > "$OPS_WORKTREE/.canary-runtime.env" <<EOF
ASSISTX_RUNTIME_SHA=$ASSISTX_RUNTIME_SHA
AUTO_ROUTER_RUNTIME_SHA=$AUTO_ROUTER_RUNTIME_SHA
ASSISTX_RUNTIME_WORKTREE=$ASSISTX_RUNTIME_WORKTREE
AUTO_ROUTER_RUNTIME_WORKTREE=$AUTO_ROUTER_RUNTIME_WORKTREE
EOF

echo
echo "Pinned canary workspaces:"
echo "  ops:            $OPS_WORKTREE @ $(git -C "$OPS_WORKTREE" rev-parse HEAD)"
echo "  AssistX runtime: $ASSISTX_RUNTIME_WORKTREE @ $ASSISTX_RUNTIME_SHA"
echo "  Auto-Router:     $AUTO_ROUTER_RUNTIME_WORKTREE @ $AUTO_ROUTER_RUNTIME_SHA"
echo

echo "Running the live read-only authority/topology preflight..."
cd "$OPS_WORKTREE"
bash scripts/run-x1-370-live-fleet-preflight.sh

if [[ "$REGISTER_RUNNER" == "1" ]]; then
  echo
  echo "Attempting to register/start the x1-370 canary Actions runner..."
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    if ! bash scripts/bootstrap-x1-370-canary-runner.sh; then
      echo "warning: GitHub runner registration failed; local canary flow will continue" >&2
    fi
  else
    echo "warning: gh is unavailable or unauthenticated; skipping optional Actions runner registration" >&2
  fi
fi

echo
echo "Live preflight passed. Capturing the replicated before state..."
exec bash scripts/run-x1-370-fleet-canary-before.sh
