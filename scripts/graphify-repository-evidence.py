#!/usr/bin/env python3
"""Generate repository-scale Graphify evidence for the current checkout."""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from assistx.repository_graph import RepositoryGraphProjection, normalize_graphify_graph
from assistx.repository_graph_query import neighborhood
from assistx.repository_graph_reranker import rank_graph_files
from assistx.repository_graph_scope_advisor import advise_scope


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def _expand_neighbors(seed: str, graph: dict[str, set[str]], depth: int) -> set[str]:
    seen = {seed}
    frontier = {seed}
    for _ in range(depth):
        frontier = {
            neighbor
            for current in frontier
            for neighbor in graph.get(current, set())
            if neighbor not in seen
        }
        seen.update(frontier)
    return seen - {seed}


def _metric_summary(recalls: list[float], precisions: list[float], zero: int, perfect: int) -> dict[str, object]:
    return {
        "mean_recall": statistics.mean(recalls) if recalls else 0.0,
        "median_recall": statistics.median(recalls) if recalls else 0.0,
        "mean_precision": statistics.mean(precisions) if precisions else 0.0,
        "median_precision": statistics.median(precisions) if precisions else 0.0,
        "zero_hit_seed_count": zero,
        "perfect_recall_seed_count": perfect,
    }


def _historical_change_coupling(
    *,
    projection: RepositoryGraphProjection,
    node_file: dict[str, str],
    represented_python_files: set[str],
    commit_limit: int = 200,
) -> dict[str, object]:
    """Retrospective proxy: recover recent co-changed files from graph neighborhoods."""
    file_neighbors: defaultdict[str, set[str]] = defaultdict(set)
    for edge in projection.edges:
        source_file = node_file.get(str(edge["source"]))
        target_file = node_file.get(str(edge["target"]))
        if (
            source_file
            and target_file
            and source_file != target_file
            and source_file.endswith(".py")
            and target_file.endswith(".py")
        ):
            file_neighbors[source_file].add(target_file)
            file_neighbors[target_file].add(source_file)

    commits = [
        value.strip()
        for value in _git("log", "--no-merges", f"-n{commit_limit}", "--format=%H").splitlines()
        if value.strip()
    ]
    metrics = {
        depth: {"recalls": [], "precisions": [], "zero": 0, "perfect": 0}
        for depth in (1, 2)
    }
    reranker_metrics = {
        limit: {"recalls": [], "precisions": [], "zero": 0, "perfect": 0}
        for limit in (5, 10, 20)
    }
    # Scope advice counts the seed against max_files. These configs therefore
    # mean total allowed-path budget + bounded depth-2 expansion budget.
    scope_configs = ((8, 0), (8, 2), (8, 4), (10, 2), (10, 4))
    scope_metrics = {
        config: {"recalls": [], "precisions": [], "zero": 0, "perfect": 0}
        for config in scope_configs
    }
    seed_rows: list[dict[str, object]] = []
    multi_file_commits = 0

    for commit in commits:
        changed = {
            path.strip()
            for path in _git("show", "--pretty=", "--name-only", "--diff-filter=AM", commit).splitlines()
            if path.strip().endswith(".py") and path.strip() in represented_python_files
        }
        if len(changed) < 2:
            continue
        multi_file_commits += 1
        task_text = _git("show", "-s", "--format=%s", commit)
        for seed in sorted(changed):
            targets = changed - {seed}
            row: dict[str, object] = {
                "commit": commit,
                "commit_subject": task_text,
                "seed": seed,
                "cochanged_targets": len(targets),
            }
            for depth in (1, 2):
                predicted = _expand_neighbors(seed, file_neighbors, depth)
                hits = targets & predicted
                recall = len(hits) / len(targets)
                precision = len(hits) / len(predicted) if predicted else 0.0
                values = metrics[depth]
                values["recalls"].append(recall)
                values["precisions"].append(precision)
                if not hits:
                    values["zero"] += 1
                if recall == 1.0:
                    values["perfect"] += 1
                row[f"depth{depth}"] = {
                    "predicted_neighbors": len(predicted),
                    "hits": sorted(hits),
                    "recall": recall,
                    "precision": precision,
                }

            ranked = rank_graph_files(
                projection,
                seed_file=seed,
                task_text=task_text,
                max_depth=2,
                limit=20,
            )
            ranked_paths = [candidate.path for candidate in ranked]
            for limit in (5, 10, 20):
                predicted = set(ranked_paths[:limit])
                hits = targets & predicted
                recall = len(hits) / len(targets)
                precision = len(hits) / len(predicted) if predicted else 0.0
                values = reranker_metrics[limit]
                values["recalls"].append(recall)
                values["precisions"].append(precision)
                if not hits:
                    values["zero"] += 1
                if recall == 1.0:
                    values["perfect"] += 1
                row[f"reranked_top{limit}"] = {
                    "predicted_neighbors": len(predicted),
                    "hits": sorted(hits),
                    "recall": recall,
                    "precision": precision,
                }

            for max_files, expansion_budget in scope_configs:
                advice = advise_scope(
                    projection,
                    seed_file=seed,
                    task_text=task_text,
                    max_files=max_files,
                    expansion_budget=expansion_budget,
                )
                predicted = set(advice.candidate_paths) - {seed}
                hits = targets & predicted
                recall = len(hits) / len(targets)
                precision = len(hits) / len(predicted) if predicted else 0.0
                values = scope_metrics[(max_files, expansion_budget)]
                values["recalls"].append(recall)
                values["precisions"].append(precision)
                if not hits:
                    values["zero"] += 1
                if recall == 1.0:
                    values["perfect"] += 1
                row[f"scope_{max_files}files_{expansion_budget}exp"] = {
                    "predicted_neighbors": len(predicted),
                    "primary": list(advice.primary_paths),
                    "expansion": list(advice.expansion_paths),
                    "hits": sorted(hits),
                    "recall": recall,
                    "precision": precision,
                }
            seed_rows.append(row)

    depth1 = _metric_summary(**metrics[1])
    depth2 = _metric_summary(**metrics[2])
    reranked = {
        f"top{limit}": _metric_summary(**reranker_metrics[limit])
        for limit in (5, 10, 20)
    }
    scope_advice = {
        f"{max_files}files_{expansion}exp": _metric_summary(**scope_metrics[(max_files, expansion)])
        for max_files, expansion in scope_configs
    }
    return {
        "historical_proxy_note": (
            "current graph vs recent non-merge commit co-change; retrospective, not causal or held-out"
        ),
        "historical_commits_considered": len(commits),
        "historical_multifile_python_commits": multi_file_commits,
        "historical_seed_evaluations": len(seed_rows),
        "historical_depth1": depth1,
        "historical_depth2": depth2,
        "historical_reranked_depth2": reranked,
        "historical_scope_advice": scope_advice,
        "mean_cochange_recall": depth1["mean_recall"],
        "median_cochange_recall": depth1["median_recall"],
        "mean_cochange_precision": depth1["mean_precision"],
        "median_cochange_precision": depth1["median_precision"],
        "zero_hit_seed_count": depth1["zero_hit_seed_count"],
        "perfect_recall_seed_count": depth1["perfect_recall_seed_count"],
        "historical_seed_samples": seed_rows[:50],
    }


def main() -> int:
    repository_root = Path.cwd().resolve()
    repository = os.getenv("GITHUB_REPOSITORY", "scottjoyner/auto-assist")
    commit_sha = os.getenv("GITHUB_SHA") or _git("rev-parse", "HEAD")

    with tempfile.TemporaryDirectory(prefix="assistx-graphify-real-") as tmp:
        output = Path(tmp) / "graphify-out"
        run_env = dict(os.environ)
        run_env["GRAPHIFY_OUT"] = str(output)
        completed = subprocess.run(
            ["graphify", str(repository_root), "--code-only"],
            cwd=repository_root,
            env=run_env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        upstream_log = completed.stdout or ""
        if completed.returncode != 0:
            Path("graphify-repository-upstream.log").write_text(upstream_log, encoding="utf-8")
            raise SystemExit(
                "Graphify repository extraction failed with exit code "
                f"{completed.returncode}. Last output:\n{upstream_log[-8000:]}"
            )

        graph_path = output / "graph.json"
        if not graph_path.exists():
            raise SystemExit(f"Graphify did not create {graph_path}\n{upstream_log[-4000:]}")

        raw_graph = json.loads(graph_path.read_text(encoding="utf-8"))
        projection = normalize_graphify_graph(raw_graph, repository=repository, commit_sha=commit_sha)
        if not projection.nodes or not projection.edges:
            raise SystemExit("Repository-scale Graphify projection is unexpectedly empty")

        node_counts_by_file: Counter[str] = Counter()
        edge_counts_by_relation: Counter[str] = Counter()
        adjacency: defaultdict[str, int] = defaultdict(int)
        node_file: dict[str, str] = {}
        for node in projection.nodes:
            node_id = str(node["id"])
            source_file = str(node.get("source_file") or "").strip()
            if source_file:
                node_counts_by_file[source_file] += 1
                node_file[node_id] = source_file
        for edge in projection.edges:
            edge_counts_by_relation[str(edge.get("relation") or "related_to")] += 1
            adjacency[str(edge["source"])] += 1
            adjacency[str(edge["target"])] += 1

        sample_nodes = projection.nodes[: min(100, len(projection.nodes))]
        neighborhood_sizes = [
            len(neighborhood(projection, str(node["id"]), depth=1)) for node in sample_nodes
        ]
        isolated_nodes = sum(adjacency[str(node["id"])] == 0 for node in projection.nodes)
        tracked_python_files = [line for line in _git("ls-files", "*.py").splitlines() if line.strip()]
        represented_files = set(node_counts_by_file)
        represented_python_files = {path for path in represented_files if path.endswith(".py")}
        python_file_coverage = (
            len(represented_python_files) / len(tracked_python_files) if tracked_python_files else 0.0
        )
        coupling = _historical_change_coupling(
            projection=projection,
            node_file=node_file,
            represented_python_files=represented_python_files,
        )

        payload = {
            "schema_version": "assistx.graphify-repository-evidence.v6",
            "repository": repository,
            "commit_sha": commit_sha,
            "graphify_version": "0.9.46",
            "mode": "code-only",
            "nodes": len(projection.nodes),
            "edges": len(projection.edges),
            "isolated_nodes": isolated_nodes,
            "graph_json_bytes": graph_path.stat().st_size,
            "tracked_python_files": len(tracked_python_files),
            "represented_python_files": len(represented_python_files),
            "python_file_coverage": python_file_coverage,
            "sampled_neighborhoods": len(neighborhood_sizes),
            "mean_depth1_neighborhood_size": statistics.mean(neighborhood_sizes),
            "median_depth1_neighborhood_size": statistics.median(neighborhood_sizes),
            "max_depth1_neighborhood_size": max(neighborhood_sizes),
            "top_files_by_node_count": node_counts_by_file.most_common(20),
            "relations": edge_counts_by_relation.most_common(),
            "upstream_log_lines": len(upstream_log.splitlines()),
            **coupling,
            "authoritative_behavior_changed": False,
        }
        Path("graphify-repository-evidence.json").write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
