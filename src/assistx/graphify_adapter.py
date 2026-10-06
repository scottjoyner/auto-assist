"""Dry-run Graphify code-graph extraction adapter.

Only local code-only extraction is supported. Graphify never writes to Neo4j,
installs hooks, or becomes the canonical repository graph through this adapter.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from assistx.repository_graph import normalize_graphify_graph

_VERSION_RE = re.compile(r"\bgraphify\s+(?P<version>\d+\.\d+\.\d+)\b")


@dataclass(frozen=True)
class GraphifyBinary:
    path: str
    version: str
    sha256: str


@dataclass(frozen=True)
class GraphifyPreview:
    repository: str
    commit_sha: str
    dirty: bool
    graph_path: str
    preview_path: str
    stdout_path: str
    stderr_path: str
    metadata_path: str
    node_count: int
    edge_count: int
    extracted_edges: int
    inferred_edges: int
    binary: GraphifyBinary
    status: str


class GraphifyAdapter:
    def __init__(self, binary_path: str, *, expected_version: str | None = None,
                 expected_sha256: str | None = None, timeout_seconds: int = 120) -> None:
        self.binary_path = str(Path(binary_path).expanduser())
        self.expected_version = expected_version
        self.expected_sha256 = expected_sha256
        self.timeout_seconds = int(timeout_seconds)

    def verify_binary(self) -> GraphifyBinary:
        path = Path(self.binary_path)
        if not path.is_file():
            raise RuntimeError(f"Graphify binary not found: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if self.expected_sha256 and digest != self.expected_sha256:
            raise RuntimeError("Graphify binary SHA-256 does not match pinned digest")
        result = subprocess.run(
            [str(path), "--version"], text=True, capture_output=True,
            timeout=10, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Graphify --version failed with exit {result.returncode}")
        match = _VERSION_RE.search(result.stdout + "\n" + result.stderr)
        if not match:
            raise RuntimeError("Unable to parse Graphify version")
        version = match.group("version")
        if self.expected_version and version != self.expected_version:
            raise RuntimeError(
                f"Graphify version mismatch: expected {self.expected_version}, got {version}"
            )
        return GraphifyBinary(path=str(path), version=version, sha256=digest)

    def extract_code(self, repository: str, *, artifact_dir: str,
                     require_clean: bool = False) -> GraphifyPreview:
        repo = Path(repository).resolve()
        if not repo.is_dir():
            raise ValueError(f"repository does not exist: {repo}")
        commit_sha = self._git(repo, "rev-parse", "HEAD").strip()
        dirty = bool(self._git(repo, "status", "--porcelain").strip())
        if require_clean and dirty:
            raise RuntimeError("repository is dirty; refusing provenance-pinned extraction")
        binary = self.verify_binary()
        root = Path(artifact_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        command = [
            binary.path, "extract", str(repo), "--code-only", "--no-cluster",
            "--out", str(root),
        ]
        started = time.monotonic()
        result = subprocess.run(
            command, capture_output=True, timeout=self.timeout_seconds, check=False,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout_path = root / "graphify.stdout"
        stderr_path = root / "graphify.stderr"
        metadata_path = root / "graphify-metadata.json"
        preview_path = root / "graphify-preview.json"
        stdout_path.write_bytes(result.stdout)
        stderr_path.write_bytes(result.stderr)
        if result.returncode != 0:
            raise RuntimeError(
                f"Graphify extraction failed with exit {result.returncode}; see {stderr_path}"
            )
        graph_path = root / "graphify-out" / "graph.json"
        if not graph_path.is_file():
            raise RuntimeError("Graphify did not produce graphify-out/graph.json")
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        projection = normalize_graphify_graph(
            graph, repository=str(repo), commit_sha=commit_sha,
        )
        extracted = sum(1 for edge in projection.edges if edge["confidence"] == "EXTRACTED")
        inferred = sum(1 for edge in projection.edges if edge["confidence"] == "INFERRED")
        preview_payload: dict[str, Any] = {
            "schema_version": 1,
            "repository": str(repo),
            "commit_sha": commit_sha,
            "dirty": dirty,
            "authority": "preview-only",
            "neo4j_write_allowed": False,
            "nodes": list(projection.nodes),
            "edges": list(projection.edges),
        }
        preview_path.write_text(
            json.dumps(preview_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        preview = GraphifyPreview(
            repository=str(repo), commit_sha=commit_sha, dirty=dirty,
            graph_path=str(graph_path), preview_path=str(preview_path),
            stdout_path=str(stdout_path), stderr_path=str(stderr_path),
            metadata_path=str(metadata_path), node_count=len(projection.nodes),
            edge_count=len(projection.edges), extracted_edges=extracted,
            inferred_edges=inferred, binary=binary, status="ok",
        )
        metadata_path.write_text(
            json.dumps(
                {**asdict(preview), "duration_ms": duration_ms,
                 "graph_sha256": hashlib.sha256(graph_path.read_bytes()).hexdigest()},
                indent=2, sort_keys=True, default=str,
            ) + "\n",
            encoding="utf-8",
        )
        return preview

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], text=True,
            capture_output=True, timeout=10, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout
