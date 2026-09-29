# Graphify Projection PoC — 2026-08-17

Issue: `#27`
Branch: `agent/graphify-projection-poc`

## Hypothesis

AssistX can consume Graphify's deterministic `graph.json` artifact through a narrow, versionable adapter without making Graphify a runtime dependency or allowing generated repository graphs to become canonical operational state.

## Upstream facts used by this PoC

Graphify documents `graph.json` as NetworkX node-link data. Nodes include stable IDs, labels, file type, and source file. Edges include source/target IDs, relation, confidence (`EXTRACTED`, `INFERRED`, `AMBIGUOUS`), optional confidence score, and source file.

The upstream code graph path is local/tree-sitter based and does not require an LLM for code-only corpora. The repository is Apache-2.0 licensed.

## Implemented boundary

`assistx.repository_graph.normalize_graphify_graph()`:

- accepts node-link JSON with either `links` or `edges` for NetworkX compatibility;
- validates nodes and edge references;
- preserves relation/confidence/source-file metadata;
- namespaces every projected node by `repository@commit_sha`;
- stores repository, commit SHA, and `projection_source=graphify` on every normalized object;
- rejects duplicate node IDs, dangling edges, and unknown confidence tags.

No Neo4j mutation is included in this first stage. The output is an in-memory disposable projection suitable for later benchmark/import adapters.

## Tests

The first tests cover:
- commit-pinned namespacing;
- `links` and `edges` compatibility;
- dangling-edge rejection;
- duplicate-node rejection;
- invalid-confidence rejection.

## Why this shape

Repository graphs are derived artifacts. They should be deletable and rebuildable from a pinned source commit. Namespacing prevents symbols from different repositories or revisions from silently colliding in Neo4j.

## Next experiment

1. Generate Graphify artifacts for pinned commits of `auto-assist`, `auto-router`, and `auto-ingest` on a machine with Graphify available.
2. Record Graphify version/commit and artifact hashes.
3. Normalize each artifact through this adapter.
4. Add read-only queries for neighborhood, callers/callees, imports, and likely blast radius.
5. Compare coding-agent tasks with graph context on/off using #24 traces:
   - files read;
   - search/grep calls;
   - input tokens;
   - time to first correct plan;
   - relevant test identification;
   - task success.
6. Compare GitNexus separately as a reference implementation, while respecting its PolyForm Noncommercial licensing constraint.

## Promotion gate

Graphify earns a persistent integration only if commit-pinned graph context materially reduces context/tool churn or improves impact/test selection without creating stale/colliding graph state. Neo4j remains canonical; Graphify data remains a rebuildable projection.
