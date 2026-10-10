#!/usr/bin/env python3
"""Summarize current runtime gate from Neo4j without granting any authority.

Run within the existing AssistX API environment. No writes or credentials printed.
"""
from __future__ import annotations

import json

from assistx.api import _neo
from assistx.runtime_projection import _query_rows
from assistx.runtime_admission_diagnostics import summarize


def main() -> int:
    def read(query: str, params: dict[str, int]):
        return _query_rows(_neo, query, params)

    print(json.dumps(summarize(read), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
