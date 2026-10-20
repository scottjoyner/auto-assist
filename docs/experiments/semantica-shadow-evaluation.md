# Semantica Shadow Context Experiment

Status: design-only / no production integration  
Tracking: #113  
Upstream: https://github.com/semantica-agi/semantica

## Question

Does Semantica add enough **graph-aware, cross-session context recovery** and **decision provenance** to justify a read-only shadow sidecar beside AssistX, without weakening Neo4j authority or adding unacceptable latency, memory, disk, or operational risk?

This experiment is intentionally narrower than "evaluate Semantica." It tests one concrete fleet use case:

> Given historical task, execution, node/model, approval, recovery, and knowledge-note evidence, can a future Hermes/OpenCode/operator query recover the right prior context more reliably than the current exact-graph / flat-retrieval path, while preserving source identity and surviving process restarts?

## Hypotheses

### H1 — retrieval value

For held-out multi-hop queries, Semantica graph-aware retrieval will improve evidence recall over a flat semantic/vector baseline by at least **10 percentage points Recall@5** or **0.10 MRR**, without reducing provenance completeness below 100%.

### H2 — decision precedent value

For held-out operator-decision scenarios, Semantica decision/precedent lookup will return the known relevant prior decision in the top 3 for at least **80%** of cases.

### H3 — persistence

A save -> process exit -> clean reload must reproduce the same top-5 result identities for at least **95%** of deterministic test queries. A deliberately killed process may lose only the unsaved tail; it must not corrupt or mutate any canonical AssistX data.

### H4 — acceptable cost

On the fixed evaluation corpus, the Semantica shadow path must add no more than:

- **250 ms p95** retrieval overhead relative to the flat local baseline for ordinary top-k retrieval,
- **1.5 GiB** incremental RSS at steady state,
- **5x corpus bytes** on disk for persisted evaluation state.

These are evaluation thresholds, not production SLOs. A useful retrieval gain may justify revisiting them explicitly.

## Non-goals

This experiment does **not**:

- replace Neo4j,
- import the live production graph,
- grant Semantica task, routing, admission, claim, approval, recovery, or promotion authority,
- write back to AssistX,
- run hosted LLM providers,
- enable optional GPU extras,
- evaluate natural-language answer quality from an LLM,
- auto-enforce policy outcomes,
- become a long-running production service.

## Safety / authority boundary

The data flow is one-way:

```
sanitized fixture export
        |
        +--> baseline exact graph lookup
        |
        +--> baseline flat semantic retrieval
        |
        +--> disposable Semantica store
                    |
                    +--> vector-only retrieval
                    +--> graph-aware retrieval
                    +--> decision precedent queries
                    +--> MCP read-path checks
```

The experiment adapter must have:

- no production Neo4j credentials,
- no write-capable AssistX API credentials,
- no production NAS canonical-data path,
- no service-management capability,
- no routing/admission/claim/approval tool access.

All state lives beneath one disposable evaluation root such as:

`$TMPDIR/assistx-semantica-eval/<run-id>/`

or another explicitly non-canonical path.

## Corpus

Build a deterministic fixture from synthetic or sanitized copies of **200 evidence objects**:

- 50 tasks / subtask ancestry records,
- 50 execution attempts / traces,
- 30 node + model observations,
- 25 operator approvals / rejections / promotion decisions,
- 25 recovery / incident events,
- 20 knowledge-repo notes.

Every record gets stable IDs and explicit source metadata:

```json
{
  "source_id": "trace:attempt-0042",
  "source_type": "execution_attempt",
  "timestamp": "2026-10-01T18:22:31Z",
  "subject_ids": ["task:T-17", "node:x1-370", "model:k2-horizon-0.9b"],
  "content": "Attempt T-17 on x1-370 failed admission because ...",
  "relations": [
    {"type": "ATTEMPT_OF", "target": "task:T-17"},
    {"type": "RAN_ON", "target": "node:x1-370"}
  ]
}
```

No secrets, tokens, raw credentials, personal data, or canonical storage paths may appear in the fixture.

## Query set

Create **40 held-out queries** before running the systems. The answer key must be fixed and stored separately from retrieval code.

### 1. Exact factual queries — 10

Designed to favor neither graph nor vector search.

Examples:

- Which node executed attempt A?
- Which model was recorded for task B?
- What approval state belongs to promotion candidate C?

### 2. Semantic single-hop queries — 10

Require wording variation but not graph traversal.

Example:

- Find the prior attempt where a provider returned unusable empty/question-mark output.

### 3. Multi-hop context queries — 10

Require connecting at least two relationships.

Examples:

- Find the earlier task that used the same model and node as this failed attempt and ended with an operator rejection.
- Which recovery event followed the crash of the node that owned the affected task?
- What previous decision involved the same subsystem and failure class as this new incident?

### 4. Decision precedent queries — 10

Each has one known relevant historical decision and at least two plausible distractors.

Examples:

- We have a candidate context service that duplicates some Neo4j concepts. What prior decision established the authority boundary for sidecars?
- A worker produced strong benchmark evidence but lacked deployment authority. Find the closest prior promotion decision.

## Retrieval arms

Run every applicable query through the same corpus snapshot.

### A — exact AssistX/Neo4j-style lookup

Deterministic lookup/traversal over fixture IDs and relationships. This is the structural baseline.

### B — flat semantic baseline

Local vector retrieval over fixture `content`, with graph expansion disabled.

### C — Semantica vector-only

`AgentContext.retrieve(..., use_graph=False)` or equivalent version-pinned API.

### D — Semantica graph-aware

`AgentContext.retrieve(..., use_graph=True)` with a fixed configuration:

- top-k: 5,
- max hops: 2,
- fixed hybrid/proximity weighting,
- no per-query tuning.

### E — Semantica precedent lookup

For decision cases, use the version-pinned decision intelligence API such as `find_precedents`.

Do not compare generated answer prose. Compare **retrieved evidence IDs**, rank, provenance, latency, and resource cost.

## Semantica configuration to pin

Record the exact upstream commit and package version in every receipt.

Initial candidate configuration:

- base install only,
- Python supported by upstream,
- local FAISS/vector backend,
- `ContextGraph(advanced_analytics=False)` unless a test explicitly needs analytics,
- graph expansion enabled only for arm D,
- decision tracking enabled for arm E,
- no hosted LLM,
- no GPU extras,
- explicit save/load path under the run root.

The upstream docs state that persistence is explicit and the MCP server starts with an empty graph unless `SEMANTICA_KG_PATH` is configured. Treat persistence as something to prove, not assume.

## Metrics

### Retrieval quality

For each query / arm:

- Recall@1
- Recall@3
- Recall@5
- Mean Reciprocal Rank
- irrelevant-result count in top 5
- expected source IDs found / missing

Primary comparison:

`D graph-aware - C vector-only`

Secondary comparisons:

- D vs B,
- D vs A on multi-hop cases,
- E decision-precedent accuracy.

### Provenance

For every returned result:

- source ID present,
- source type present,
- source identity matches fixture,
- no invented/untraceable evidence.

**Gate: 100% provenance completeness.**

### Latency

After 5 warm-up queries, run each query at least 10 times and record:

- p50,
- p95,
- max,
- cold-start time separately.

Do not combine ingestion/build time with retrieval latency.

### Resource footprint

Record:

- install/venv bytes,
- persisted store bytes,
- peak and steady RSS,
- ingest/build time,
- save time,
- reload time.

### Stability

Run the full query set:

1. immediately after build,
2. after explicit save + clean process restart,
3. after a second clean restart.

Compare top-5 source-ID sets and ranks.

## Crash/recovery slice

Use only disposable state.

1. Build corpus.
2. Save snapshot S1.
3. Add 10 additional synthetic memories/decisions.
4. Do **not** save.
5. SIGKILL the process.
6. Restart from S1.
7. Verify:
   - S1 is readable,
   - the saved 200-record corpus is intact,
   - the unsaved 10-record tail is absent or explicitly recoverable,
   - no malformed partial state is accepted silently,
   - the fixture can be rebuilt from source.

Then repeat with an explicit save after the extra 10 records and confirm all 210 survive restart.

## MCP slice

Run the upstream stdio MCP server against an **independent disposable graph path**.

Required checks:

1. `initialize` succeeds.
2. `tools/list` exposes the expected tool surface.
3. Read-only calls such as graph summary/query work.
4. The process survives 25 sequential harmless read calls.
5. stdout contains only valid JSON-RPC frames; logs remain on stderr.
6. Restart with `SEMANTICA_KG_PATH` restores the expected graph.
7. Mutating MCP tools are **not exposed to the agent under test** in the AssistX adapter.

The upstream server has mutation-capable tools. The AssistX-facing experiment must expose an allowlisted read-only wrapper rather than blindly forwarding all Semantica MCP tools.

## Blind evaluation rule

The query author fixes:

- query text,
- relevant source IDs,
- allowed equivalent answers,
- query category

before retrieval runs are collected.

No changing hybrid weights, graph hops, chunking, or answer keys after seeing individual query failures. If tuning is desired, split the 40 queries into:

- 10 tuning/development queries,
- 30 locked evaluation queries.

Report both, but promotion decisions use only the locked set.

## Result receipt

Each run writes one immutable JSON receipt containing at least:

```json
{
  "run_id": "semantica-shadow-...",
  "semantica_version": "0.7.0",
  "semantica_commit": "<sha>",
  "fixture_sha256": "<sha256>",
  "query_set_sha256": "<sha256>",
  "config": {},
  "metrics": {
    "baseline_flat": {},
    "semantica_vector": {},
    "semantica_graph": {},
    "semantica_precedent": {}
  },
  "persistence": {},
  "crash_recovery": {},
  "mcp": {},
  "authority_boundary": {
    "production_credentials_present": false,
    "writeback_capability_present": false,
    "canonical_paths_touched": false
  }
}
```

## Promotion decision

### Promote to `shadow_context_sidecar` only if

- graph-aware Recall@5 or MRR clears H1,
- decision precedent top-3 accuracy >= 80%,
- provenance completeness = 100%,
- clean restart stability >= 95% top-5 identity agreement,
- crash test preserves last saved snapshot,
- resource and latency gates pass or are explicitly reviewed,
- the AssistX adapter proves read-only authority isolation.

### Promote to `mcp_context_provider_candidate` only if

all shadow-sidecar gates pass **and** the read-only MCP wrapper demonstrates:

- stable stdio operation,
- explicit tool allowlisting,
- no mutation tools visible to Hermes/OpenCode,
- deterministic access to the same persisted fixture.

### Otherwise

Classify the result as one of:

- `keep_for_research`
- `decision_audit_projection_candidate`
- `reject`

A failure to beat the flat retrieval baseline is a valid and useful result.

## Suggested execution order

1. Pin upstream commit/version.
2. Generate synthetic/sanitized 200-record fixture.
3. Freeze 40-query answer key.
4. Run A/B baselines.
5. Run Semantica C/D/E.
6. Run persistence tests.
7. Run crash/recovery test.
8. Run read-only MCP wrapper acceptance.
9. Produce one JSON receipt + concise Markdown report.
10. Review promotion gate; do not auto-deploy.
