#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any

import yaml


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_items(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    runtimes = payload.get("runtimes")
    if not isinstance(runtimes, list):
        return items
    for runtime_index, runtime in enumerate(runtimes):
        if not isinstance(runtime, dict):
            continue
        capacity = runtime.get("capacity")
        if isinstance(capacity, dict):
            items.append((f"runtimes[{runtime_index}].capacity", capacity))
        paths = runtime.get("access_paths")
        if isinstance(paths, list):
            for path_index, path in enumerate(paths):
                if isinstance(path, dict):
                    items.append(
                        (
                            f"runtimes[{runtime_index}].access_paths[{path_index}]",
                            path,
                        )
                    )
        models = runtime.get("models")
        if isinstance(models, list):
            for model_index, model in enumerate(models):
                if isinstance(model, dict):
                    items.append(
                        (
                            f"runtimes[{runtime_index}].models[{model_index}]",
                            model,
                        )
                    )
    return items


def validate(
    payload: dict[str, Any],
    *,
    root: pathlib.Path,
    require_files: bool = True,
) -> tuple[list[str], list[dict[str, Any]]]:
    failures: list[str] = []
    verified: list[dict[str, Any]] = []
    root = root.resolve()
    items = _evidence_items(payload)
    if not items:
        failures.append("runtime manifest contains no evidence-bearing items")
        return failures, verified

    for label, item in items:
        reference = str(item.get("evidence_ref") or "").strip()
        expected = str(item.get("evidence_sha256") or "").strip().lower()
        if not reference:
            failures.append(f"{label}.evidence_ref is required")
            continue
        if not SHA256_RE.fullmatch(expected):
            failures.append(f"{label}.evidence_sha256 must be 64 lowercase hex characters")
            continue

        relative = pathlib.PurePosixPath(reference)
        if relative.is_absolute() or ".." in relative.parts:
            failures.append(f"{label}.evidence_ref must be a safe relative path")
            continue
        if not relative.parts or relative.parts[0] != "artifacts":
            failures.append(f"{label}.evidence_ref must be under artifacts/")
            continue

        path = (root / pathlib.Path(*relative.parts)).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            failures.append(f"{label}.evidence_ref escapes the repository root")
            continue

        if not path.is_file():
            if require_files:
                failures.append(f"{label}.evidence_ref does not exist: {reference}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            failures.append(
                f"{label}.evidence_sha256 mismatch expected={expected} actual={actual}"
            )
            continue
        verified.append(
            {
                "label": label,
                "path": reference,
                "sha256": actual,
                "bytes": path.stat().st_size,
            }
        )

    return sorted(set(failures)), verified


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify every runtime projection evidence artifact and SHA-256."
    )
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()

    try:
        payload = yaml.safe_load(args.manifest.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"RUNTIME_EVIDENCE: BLOCKED {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("RUNTIME_EVIDENCE: BLOCKED manifest root must be a mapping", file=sys.stderr)
        return 2

    failures, verified = validate(payload, root=args.root, require_files=True)
    evidence = {
        "schema_version": 1,
        "manifest": str(args.manifest),
        "repository_root": str(args.root.resolve()),
        "status": "pass" if not failures else "blocked",
        "verified_count": len(verified),
        "verified": verified,
        "failures": failures,
    }
    serialized = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    evidence["evidence_sha256"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    output = args.output or args.manifest.with_suffix(".runtime-evidence.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if failures:
        print("RUNTIME_EVIDENCE: BLOCKED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"RUNTIME_EVIDENCE: PASS ({len(verified)} artifacts)")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
