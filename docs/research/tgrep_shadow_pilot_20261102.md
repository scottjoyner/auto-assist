# tgrep starred-repo intake — source freshness and verified discovery

Status: **research-only; not installed as a fleet daemon and no production authority**.

Source: microsoft/tgrep v1.2.6, SHA-256 release archive
`582737741d93c1281802188da6b7207d4a5f16b1dbe010badff12f03414d99c4`,
MIT license. Public benchmark checkout: google-research/harness2
`6868817518876de8fd170c6a8354daca273b6191` (Apache-2.0).

## Reproduced results on x1-370, 2026-11-02

A disposable, verified release binary searched the pinned public code tree
using its ordinary *local index*, its scan mode, and existing `rg`. No daemon,
system changes, model calls, live/private repo indexing or production API calls.

- 8 fixed-string patterns: matching **sets of relative filenames** agreed
  between the three tools on **all 8** queries.
- Median of 3 repeats per pattern, then median over 8 queries:
  indexed tgrep **3.04 ms**, `rg` **4.13 ms**, tgrep's forced scanner **7.50 ms**.
  This is a small tree, not a fleet-wide performance guarantee. One broad query
  (`harness`) was *slower* with the index than with `rg`.
- On the pinned checkout, initial index size ~2 MiB; builder reported
  around 1.3 MiB peak internal memory on a later bounded run. These do not
  include a perpetual daemon or all process RSS and caches.
- Real guarded adapter integration: lookup `run_domain` matched **5** files
  with `rg`, the tgrep index, and forced live scan. It returned no paths,
  contents, arguments, credentials, or source text; its authorization fields
  were false and `production_gate` was `HOLD`.
- Separate disposable Git repo: a new literal was added *after* index creation;
  the stale local index returned **0**, while `rg` and tgrep `--no-index`
  returned **1**. Therefore, indexed suggestions can miss newly changed files.

These runs were local, source-pinned and temporary. The cache, binary and
checkout were removed on exit.

## Guarded adapter

`scripts/experiments/tgrep_shadow_scan_parity.py` accepts an existing,
**externally created** index and a SHA-256-pinned tgrep binary. It does not
build or refresh an index, launch a server or change a repository. With a
single bounded literal search it compares:

1. Existing ripgrep's current-filesystem result set;
2. Fast tgrep local indexed result set;
3. tgrep's forced current-filesystem `--no-index` result set.

It rejects inconsistent malformed results, non-NUL-terminated paths,
out-of-root paths, symlink results and oversized output. A mismatch sets
`stale_or_divergent_index=true` and `index_matches_live_scan=false`.
The scanner's current-file results are the reference. If the two scanning
engines disagree, the CLI exits nonzero. Only numeric counts, parity flags
and local timing are output.

**No speedup is claimed for the guarded adapter itself**, because it runs
three searches; its role is independently checking an experimental index,
not replacing the authoritative scan. It provides no runtime/model/claim
identity or approval.

## Fleet candidate adoption gates

- Start with public/synthetic repositories and explicit index storage limits.
- Do not use a persistent server, automatic agent hooks, shared global
  indexes or production data until resource and secrets review is complete.
- Never use a stale index as the only input to a security, code review,
  CI, claim/recovery, or release correctness decision.
- Benchmark cold and warm queries and include index-build cost, RSS,
  disk consumption, and write/change invalidation over a representative
  *authorized* repo; do not report upstream vendor results as our own.
- Any adoption should remain an *optional discovery hint* in Hermes/PI,
  with scanned-file correctness before making claims or decisions.
- No merge, router/AssistX production dispatch or SensorGraft custody change
  is implied by a successful trial.

## Focused validation

```bash
python3 -m unittest discover -s tests -p test_tgrep_shadow_scan_parity.py -v
```
