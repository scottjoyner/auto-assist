import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from assistx.graphify_adapter import GraphifyAdapter, normalize_graphify_graph


def _fake_graphify(tmp_path: Path) -> Path:
    binary = tmp_path / "graphify"
    binary.write_text(
        '''#!/usr/bin/env python3
import json, pathlib, sys
if sys.argv[1:] == ["--version"]:
    print("graphify 0.9.77")
    raise SystemExit(0)
if len(sys.argv) > 2 and sys.argv[1] == "extract":
    out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1]) / "graphify-out"
    out.mkdir(parents=True, exist_ok=True)
    graph = {
      "nodes": [{"id":"a","label":"A"},{"id":"b","label":"B"}],
      "links": [{"source":"a","target":"b","relation":"calls","confidence":"EXTRACTED"}]
    }
    (out / "graph.json").write_text(json.dumps(graph))
    print("ok")
    raise SystemExit(0)
raise SystemExit(2)
'''
    )
    binary.chmod(0o755)
    return binary


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.com"], check=True)
    (repo / "app.py").write_text("def a(): return 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    return repo


def test_code_only_extraction_produces_non_authoritative_preview(tmp_path):
    binary = _fake_graphify(tmp_path)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    repo = _git_repo(tmp_path)
    preview = GraphifyAdapter(
        str(binary), expected_version="0.9.77", expected_sha256=digest
    ).extract_code(str(repo), artifact_dir=str(tmp_path / "artifacts"), require_clean=True)

    assert preview.status == "ok"
    assert preview.node_count == 2
    assert preview.edge_count == 1
    assert preview.extracted_edges == 1
    assert preview.inferred_edges == 0
    payload = json.loads(Path(preview.preview_path).read_text())
    assert payload["authority"] == "preview-only"
    assert payload["neo4j_write_allowed"] is False
    assert payload["commit_sha"] == subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()


def test_dirty_repository_can_be_required_clean(tmp_path):
    binary = _fake_graphify(tmp_path)
    repo = _git_repo(tmp_path)
    (repo / "app.py").write_text("changed\n")
    with pytest.raises(RuntimeError, match="dirty"):
        GraphifyAdapter(str(binary)).extract_code(
            str(repo), artifact_dir=str(tmp_path / "artifacts"), require_clean=True
        )


def test_graphify_digest_mismatch_fails_closed(tmp_path):
    binary = _fake_graphify(tmp_path)
    with pytest.raises(RuntimeError, match="SHA-256"):
        GraphifyAdapter(str(binary), expected_sha256="0" * 64).verify_binary()


def test_normalizer_rejects_duplicate_node_ids():
    graph = {"nodes": [{"id": "a"}, {"id": "a"}], "links": []}
    with pytest.raises(ValueError, match="duplicate Graphify node id"):
        normalize_graphify_graph(graph, repository="repo", commit_sha="abc")


def test_normalizer_rejects_edges_to_unknown_nodes():
    graph = {
        "nodes": [{"id": "a"}],
        "links": [{"source": "a", "target": "missing", "relation": "calls"}],
    }
    with pytest.raises(ValueError, match="unknown node"):
        normalize_graphify_graph(graph, repository="repo", commit_sha="abc")


def test_normalizer_rejects_unknown_confidence_values():
    graph = {
        "nodes": [{"id": "a"}, {"id": "b"}],
        "links": [{"source": "a", "target": "b", "confidence": "guess"}],
    }
    with pytest.raises(ValueError, match="unsupported edge confidence"):
        normalize_graphify_graph(graph, repository="repo", commit_sha="abc")
