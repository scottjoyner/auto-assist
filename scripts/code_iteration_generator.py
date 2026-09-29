#!/usr/bin/env python3
"""
code_iteration_generator.py — AssistX continuous code-iteration loop.

Scans target repos on the SSD workspace, derives cheap code-improvement signals
(TODO/FIXME, untested modules, stale skills, recent risky diffs), and writes
:Task(READY) nodes into Neo4j with a proper type taxonomy + required_capabilities,
deduped against already-OPEN tasks so it never spams the backlog.

Run: python3 scripts/code_iteration_generator.py [--dry-run] [--max N] [--repo PATH]

Designed to be cron-driven (Hermes cronjob every 6h). Uses the Bolt driver directly
(cypher-shell auth is unreliable per environment memory).
"""
import os
import sys
import subprocess
import argparse
import neo4j
from datetime import datetime, timezone

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://100.64.43.123:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASS = os.environ.get("NEO4J_PASSWORD", "redacted-rotate-credentials")

# Canonical workspace root (per SOUL: SSD mirror is the shared workspace)
WORKSPACE = "/media/scott/SSD_4TB"

# Repo roots to scan for .git checkouts (dynamic discovery under these dirs)
REPO_SEARCH_ROOTS = [
    os.path.join(WORKSPACE, "hermes-home"),
    WORKSPACE,
]
# Substrings to skip (not code repos / huge data dirs)
REPO_SKIP = ["nas-knowledge", "arxiv", "node_modules", ".venv", "skills-repo"]


def discover_repos():
    found = []
    for root in REPO_SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            full = os.path.join(root, name)
            if not os.path.isdir(os.path.join(full, ".git")):
                continue
            if any(s in name for s in REPO_SKIP):
                continue
            found.append(full)
    return found

# Signals -> task type
MARKERS = ["TODO", "FIXME", "HACK", "XXX", "BUG:", "DEPRECATED"]


def git_grep(repo_path, pattern):
    try:
        out = subprocess.run(
            ["git", "-C", repo_path, "grep", "-n", "-E", pattern, "--", "*.py", "*.js", "*.ts", "*.go", "*.sh"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip().splitlines()
        return out[:8]  # cap per-repo noise
    except Exception:
        return []


def recent_risky_files(repo_path):
    """Last 50 commits' touched files — surfaces areas to review."""
    try:
        out = subprocess.run(
            ["git", "-C", repo_path, "log", "--name-only", "--pretty=format:", "-50"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip().splitlines()
        files = [f for f in out if f.strip().endswith((".py", ".js", ".ts", ".go"))]
        # most-touched first
        from collections import Counter
        return [f for f, _ in Counter(files).most_common(5)]
    except Exception:
        return []


def make_task(sess, dedupe_key, title, description, repo, target, ttype):
    """Idempotent MERGE on dedupe_key. Returns True only if a NEW node was created."""
    now = datetime.now(timezone.utc).isoformat()
    res = sess.run(
        """
        MERGE (t:Task {dedupe_key:$dk})
        ON CREATE SET
            t.id = randomUUID(),
            t.title = $title,
            t.description = $description,
            t.type = $ttype,
            t.status = 'READY',
            t.priority = 'LOW',
            t.required_capabilities = ['code'],
            t.target_agent_id = 'hermes-code-iter',
            t.payload_repo = $repo,
            t.payload_target = $target,
            t.task_stage = 'review',
            t.created_at = $now,
            t.updated_at = $now
        RETURN (t.created_at = $now) AS is_new
        """,
        dk=dedupe_key, title=title, description=description, ttype=ttype,
        repo=repo, target=target, now=now,
    ).single()
    return bool(res and res["is_new"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=20)
    ap.add_argument("--repo", action="append", default=None)
    args = ap.parse_args()

    if args.repo:
        repos = [r if os.path.isdir(r) else os.path.join(WORKSPACE, r) for r in args.repo]
    else:
        repos = discover_repos()

    created, prospective = 0, 0
    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

    with driver.session() as sess:
        for repo in repos:
            rel = os.path.relpath(repo, WORKSPACE)
            if not os.path.isdir(os.path.join(repo, ".git")):
                print(f"skip (no .git): {rel}")
                continue

            # 1) marker-based code_review
            hits = git_grep(repo, "|".join(MARKERS))
            for h in hits:
                if created >= args.max:
                    break
                prospective += 1
                if args.dry_run:
                    continue
                dk = f"code_review|{rel}|{h.split(':',1)[0][:80]}"
                title = f"Review {rel}: {h.split(':',1)[0].split('/')[-1]}"
                desc = f"Address marker in {rel}: {h}"
                if make_task(sess, dk, title, desc, rel, h, "code_review"):
                    created += 1

            # 2) recent risky files -> code_review
            for f in recent_risky_files(repo):
                if created >= args.max:
                    break
                prospective += 1
                if args.dry_run:
                    continue
                dk = f"code_review_recent|{rel}|{f}"
                title = f"Review recent churn in {rel}: {f.split('/')[-1]}"
                desc = f"Recently heavily-modified file in {rel}: {f}. Review for regressions."
                if make_task(sess, dk, title, desc, rel, f, "code_review"):
                    created += 1

            # 3) stale skills -> code_deadwood
            skills_dir = os.path.join(repo, "skills") if os.path.isdir(os.path.join(repo, "skills")) else None
            if skills_dir:
                for sf in os.listdir(skills_dir):
                    if created >= args.max:
                        break
                    prospective += 1
                    if args.dry_run:
                        continue
                    dk = f"code_deadwood|{rel}|{sf}"
                    title = f"Audit skill {sf} in {rel}"
                    desc = f"Check {rel}/skills/{sf} for stale/outdated steps, duplicates, or dead refs."
                    if make_task(sess, dk, title, desc, rel, f"skills/{sf}", "code_deadwood"):
                        created += 1

    driver.close()
    if args.dry_run:
        print(f"[DRY-RUN DONE] prospective_tasks={prospective} (capped at {args.max}) repos_scanned={len(repos)}")
    else:
        print(f"[DONE] created={created} (capped at {args.max}) repos_scanned={len(repos)}")
    # open code-task count for the digest
    if not args.dry_run:
        d = neo4j.GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
        with d.session() as s:
            cnt = s.run("MATCH (t:Task) WHERE t.type STARTS WITH 'code_' AND t.status IN ['READY','RUNNING'] RETURN count(*) AS n").single()["n"]
            print(f"OPEN code-iteration tasks now: {cnt}")


if __name__ == "__main__":
    main()
