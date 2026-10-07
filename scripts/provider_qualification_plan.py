#!/usr/bin/env python3
"""Emit a no-network independent free-provider qualification plan."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from assistx.provider_qualification import qualification_plan_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    doc = qualification_plan_document()
    if args.compact:
        print(json.dumps(doc, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(doc, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
