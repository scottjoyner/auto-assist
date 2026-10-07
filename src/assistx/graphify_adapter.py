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


@dataclass(frozen=True)
class GraphifyProjection:
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def normalize_graphify_graph(
    graph: dict[str, Any], *, repository: str, commit_sha: str
) -> GraphifyProjection:
    """Normalize Graphify node-link JSON without restoring legacy graph authority."""
    if not isinstance(graph, dict):
        raise ValueError("graph must be an object")
    repository = _require_text(repository, "repository")
    commit_sha = _require_text(commit_sha, "commit_sha")
    raw_nodes = graph.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("graph.nodes must be a list")
    raw_edges = graph.get("edges", graph.get("links", []))
    if not isinstance(raw_edges, list):
        raise ValueError("graph edges/links must be a list")

    namespace = f"{repository}@{commit_sha}"
    known_ids: set[str] = set()
    nodes: list[dict[str, Any]] = []
    for index, node in enumerate(raw_nodes):
        if not isinstance(node, dict):
            raise ValueError(f"graph.nodes[{index}] must be an object")
        source_id = _require_text(node.get("id"), f"graph.nodes[{index}].id")
        if source_id in known_ids:
            raise ValueError(f"duplicate Graphify node id: {source_id}")
        known_ids.add(source_id)
        nodes.append(
            {
                "id": f"{namespace}:{source_id}",
                "source_id": source_id,
                "label": str(node.get("label") or source_id),
                "file_type": node.get("file_type"),
                "source_file": node.get("source_file"),
                "repository": repository,
                "commit_sha": commit_sha,
                "projection_source": "graphify",
            }
        )

    edges: list[dict[str, Any]] = []
    for index, edge in enumerate(raw_edges):
        if not isinstance(edge, dict):
            raise ValueError(f"graph.edges[{index}] must be an object")
        source = _require_text(edge.get("source"), f"graph.edges[{index}].source")
        target = _require_text(edge.get("target"), f"graph.edges[{index}].target")
        if source not in known_ids or target not in known_ids:
            raise ValueError(f"edge references unknown node: {source}->{target}")
        confidence = str(edge.get("confidence") or "EXTRACTED").upper()
        if confidence not in {"EXTRACTED", "INFERRED", "AMBIGUOUS"}:
            raise ValueError(f"unsupported edge confidence: {confidence}")
        edges.append(
            {
                "source": f"{namespace}:{source}",
                "target": f"{namespace}:{target}",
                "relation": str(edge.get("relation") or "related_to"),
                "confidence": confidence,
                "confidence_score": edge.get("confidence_score"),
                "source_file": edge.get("source_file"),
                "repository": repository,
                "commit_sha": commit_sha,
                "projection_source": "graphify",
            }
        )
    return GraphifyProjection(nodes=tuple(nodes), edges=tuple(edges))


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
