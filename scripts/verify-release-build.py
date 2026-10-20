#!/usr/bin/env python3
"""Conservative source-only build checks; does not certify image/runtime health."""
import re
import shlex
from pathlib import Path


def check(root: Path) -> list[str]:
    errors = []
    dockerfile = root / "Dockerfile"
    if not dockerfile.is_file():
        return ["Dockerfile is missing"]
    text = dockerfile.read_text()
    logical = re.sub(r"\\\n\s*", " ", text)
    for line in logical.splitlines():
        if line.startswith("RUN "):
            # The default /bin/sh has no pipefail. Reject pipelines altogether;
            # also reject explicit success masking on build commands.
            if re.search(r"(?<!\|)\|(?!\|)", line) or "||" in line:
                errors.append("RUN must not mask failures with pipes or OR lists")
        if line.startswith("COPY "):
            parts = shlex.split(line)[1:]
            if len(parts) < 2 or any(p.startswith("--") for p in parts):
                errors.append("Unsupported COPY syntax: extend verifier before using")
                continue
            for source in parts[:-1]:
                path = (root / source).resolve()
                if not path.is_relative_to(root.resolve()) or not path.exists():
                    errors.append(f"Missing or out-of-context COPY source: {source}")
    if not re.search(r"^RUN (?:python -m )?pip check\s*$", logical, re.M):
        errors.append("Final Python dependency consistency check is missing")
    install = logical.rfind("pip install")
    if logical.rfind("pip check") < install:
        errors.append("pip check must follow the last pip install")
    for name in ("scripts/deploy-e2e-canary.sh", "scripts/rollback-e2e-canary.sh"):
        script = root / name
        if not script.is_file() or "set -euo pipefail" not in script.read_text():
            errors.append(f"Missing fail-fast deployment script: {name}")
    return errors


if __name__ == "__main__":
    failures = check(Path(__file__).resolve().parents[1])
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        raise SystemExit(1)
    print("PASS: source build contract (image build and live acceptance remain unverified)")
