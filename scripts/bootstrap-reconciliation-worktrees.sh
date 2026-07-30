#!/usr/bin/env bash
# Prepare isolated reconciliation worktrees without changing production services.
# Default mode is a dry run. Pass --apply to fetch/create worktrees.
set -euo pipefail

APPLY=false
ALLOW_CLONE=false
GIT_ROOT="${GIT_ROOT:-/home/scott/git}"
RECON_ROOT="${RECON_ROOT:-$GIT_ROOT/reconciliation-20260730}"
BRANCH="full-auto-reconciliation-20260730"

usage() {
  cat <<'EOF'
Usage: bootstrap-reconciliation-worktrees.sh [--apply] [--allow-clone]

Environment:
  GIT_ROOT    Existing repository root (default /home/scott/git)
  RECON_ROOT  Reconciliation worktree root

The script never stops services or changes deployed checkouts. Missing repositories
are blockers unless --allow-clone is supplied with --apply.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) APPLY=true ;;
    --allow-clone) ALLOW_CLONE=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

repos=(
  auto-assist
  auto-router
  auto-assign
  hermes-agent
  fleet-llm-profiles
  fleet-inference-configs
  fleet-resilience
  lms
  ai-research-vault
)

printf 'mode=%s\n' "$([ "$APPLY" = true ] && echo apply || echo dry-run)"
printf 'git_root=%s\nrecon_root=%s\nbranch=%s\n' "$GIT_ROOT" "$RECON_ROOT" "$BRANCH"

if [ "$APPLY" = true ]; then
  mkdir -p "$RECON_ROOT"
fi

blockers=0
for repo in "${repos[@]}"; do
  source="$GIT_ROOT/$repo"
  target="$RECON_ROOT/$repo"
  remote="git@github.com:scottjoyner/$repo.git"

  printf '\n[%s]\n' "$repo"
  if ! git -C "$source" rev-parse --git-dir >/dev/null 2>&1; then
    if [ "$APPLY" = true ] && [ "$ALLOW_CLONE" = true ]; then
      echo "cloning missing repository to $source"
      git clone "$remote" "$source"
    else
      echo "BLOCKED: source repository missing or not Git: $source"
      blockers=$((blockers + 1))
      continue
    fi
  fi

  status="$(git -C "$source" status --porcelain --untracked-files=no)"
  if [ -n "$status" ]; then
    echo "BLOCKED: tracked changes exist in source checkout: $source"
    git -C "$source" status -sb
    blockers=$((blockers + 1))
    continue
  fi

  if [ -e "$target/.git" ] || [ -f "$target/.git" ]; then
    current="$(git -C "$target" branch --show-current || true)"
    head="$(git -C "$target" rev-parse HEAD || true)"
    echo "existing worktree: $target"
    echo "branch=$current head=$head"
    if [ "$current" != "$BRANCH" ]; then
      echo "BLOCKED: existing worktree is on '$current', expected '$BRANCH'"
      blockers=$((blockers + 1))
    fi
    continue
  fi

  echo "would fetch origin/$BRANCH and create $target"
  if [ "$APPLY" != true ]; then
    continue
  fi

  git -C "$source" fetch --prune origin "$BRANCH"
  if git -C "$source" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git -C "$source" worktree add "$target" "$BRANCH"
  else
    git -C "$source" worktree add -b "$BRANCH" "$target" "origin/$BRANCH"
  fi

  actual="$(git -C "$target" branch --show-current)"
  [ "$actual" = "$BRANCH" ] || {
    echo "BLOCKED: created worktree branch '$actual' does not match '$BRANCH'"
    blockers=$((blockers + 1))
  }
done

if [ "$blockers" -ne 0 ]; then
  echo "BLOCKED: $blockers repository/worktree issue(s) require review" >&2
  exit 1
fi

if [ "$APPLY" = true ]; then
  echo "PASS: reconciliation worktrees are present"
else
  echo "DRY RUN PASS: rerun with --apply after reviewing the plan"
fi
