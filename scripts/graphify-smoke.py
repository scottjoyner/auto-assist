#!/usr/bin/env python3
"""Generate and validate a real Graphify artifact on a tiny synthetic code corpus."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from assistx.repository_graph import normalize_graphify_graph
from assistx.repository_graph_query import neighborhood


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="assistx-graphify-") as tmp:
        root = Path(tmp)
        corpus = root / "corpus"
        output = root / "graphify-out"
        corpus.mkdir()
        (corpus / "service.py").write_text(
            "def normalize(value: str) -> str:\n"
            "    return value.strip().lower()\n\n"
            "def route(value: str) -> str:\n"
            "    return normalize(value)\n",
            encoding="utf-8",
        )
        (corpus / "worker.py").write_text(
            "from service import route\n\n"
            "def run(task: str) -> str:\n"
            "    return route(task)\n",
            encoding="utf-8",
        )

        run_env = dict(os.environ)
        run_env["GRAPHIFY_OUT"] = str(output)
        completed = subprocess.run(
            ["graphify", str(corpus)],
            cwd=root,
            env=run_env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        upstream_log = completed.stdout or ""

        graph_path = output / "graph.json"
        if not graph_path.exists():
            raise SystemExit(f"Graphify did not create {graph_path}\n{upstream_log[-2000:]}")
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        projection = normalize_graphify_graph(
            graph,
            repository="fixture/graphify-smoke",
            commit_sha="fixture-commit",
        )
        if not projection.nodes:
            raise SystemExit("Graphify produced no nodes")
        if not projection.edges:
            raise SystemExit("Graphify produced no edges")

        first_id = str(projection.nodes[0]["id"])
        scoped = neighborhood(projection, first_id, depth=1)
        print(
            json.dumps(
                {
                    "graphify_version": "0.9.46",
                    "nodes": len(projection.nodes),
                    "edges": len(projection.edges),
                    "first_neighborhood_size": len(scoped),
                    "projection_source": "graphify",
                    "graph_json_bytes": graph_path.stat().st_size,
                    "upstream_log_lines": len(upstream_log.splitlines()),
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
