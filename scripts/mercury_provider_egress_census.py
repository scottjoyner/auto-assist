#!/usr/bin/env python3
"""Research-only source census for pinned Mercury upstream; no runtime admission."""
import argparse
import json
import re
import subprocess
from pathlib import Path

PIN = "1be98293ace567092b961346d95363284f6e71cf"
EXPECTED = {"ai_sdk_call": 20, "provider_object_call": 5, "provider_raw_fetch": 2}
RULES = {
    "ai_sdk_call": re.compile(r"\b(?:generateText|streamText|generateObject|streamObject)\s*\(\s*\{"),
    "provider_object_call": re.compile(r"\b(?:input\.)?provider\.(?:generateText|streamText)\s*\("),
    "provider_raw_fetch": re.compile(r"\b(?:globalThis\.)?fetch\s*\("),
}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True, timeout=8).stdout.strip()


def scan(root):
    entries = []
    for path in sorted((Path(root) / "src").rglob("*")):
        if not path.is_file() or path.suffix not in {".ts", ".tsx", ".js", ".mjs"}:
            continue
        if ".test." in path.name or ".spec." in path.name or path.name.endswith(".d.ts"):
            continue
        for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            snippet = text.strip()
            if not snippet or snippet.startswith(("//", "*", "#")):
                continue
            for kind, pattern in RULES.items():
                if kind == "provider_raw_fetch" and path.parent.name != "providers":
                    continue
                if pattern.search(text):
                    entries.append({"kind": kind, "path": path.relative_to(root).as_posix(),
                                    "line": line, "expression": snippet})
    return sorted(entries, key=lambda x: (x["kind"], x["path"], x["line"]))


def manifest(root, head):
    if git(root, "rev-parse", "HEAD") != head or git(root, "status", "--porcelain", "--", "src"):
        raise ValueError("source_identity_unverified")
    entries = scan(root)
    counts = {kind: sum(e["kind"] == kind for e in entries) for kind in RULES}
    if not entries:
        raise ValueError("unexpected_empty_egress_inventory")
    return {"schema": 1, "upstream_commit": head, "counts": counts,
            "callsites": entries, "classifier": "partial_source_only_census",
            "provider_execution_authorized": False}


def check(root, expected):
    try:
        if expected.get("schema") != 1 or not isinstance(expected.get("upstream_commit"), str):
            raise ValueError("invalid_manifest")
        observed = manifest(root, expected["upstream_commit"])
        if observed != expected:
            raise ValueError("callsite_drift")
        return {"state": "SOURCE_MATCH_RESEARCH_HOLD", "counts": observed["counts"],
                "provider_execution_authorized": False, "complete_egress_verified": False}
    except (OSError, ValueError, subprocess.SubprocessError, UnicodeError, TypeError):
        return {"state": "SOURCE_MISMATCH_DENY", "provider_execution_authorized": False,
                "complete_egress_verified": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--emit-pinned-manifest", action="store_true")
    modes.add_argument("--check-manifest", type=Path)
    a = p.parse_args()
    try:
        if a.emit_pinned_manifest:
            print(json.dumps(manifest(a.root, PIN), indent=2, sort_keys=True))
            return 0
        observed = check(a.root, json.loads(a.check_manifest.read_text()))
        print(json.dumps(observed, sort_keys=True))
        return 0 if observed["state"] == "SOURCE_MATCH_RESEARCH_HOLD" else 78
    except (OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"state": "SOURCE_MISMATCH_DENY",
                          "provider_execution_authorized": False}))
        return 78


if __name__ == "__main__":
    raise SystemExit(main())

