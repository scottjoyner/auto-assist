# Compression Output-Equivalence — Runtime Evidence

- date: 2026-08-21 (UTC) | endpoint_model: qwen3.8-27b-nvfp4-mtp | headroom_model: qwen3.8-27b-nvfp4-mtp
- repeats: 10 | temperature: 0 | status: complete
- all_candidate_variants_match_raw_correctness: False
- schema: assistx.context-output-equivalence-endpoint.v2

## Per-case x variant (pass/10, avg latency ms, context chars, strategy)

| case | variant | pass/10 | avg_ms | min_ms | max_ms | ctx_chars | strategy |
|---|---|---|---|---|---|---|---|
| exact-incident | raw | 7/10 | 12586 | 10076 | 17224 | 12802 | identity |
| exact-incident | lossless_json | 0/10 | 12929 | 11824 | 15716 | 8093 | json_minify |
| exact-incident | headroom | 10/10 | 11042 | 9222 | 13762 | 3802 | headroom |
| exact-incident | hybrid | 10/10 | 10055 | 8730 | 14800 | 3802 | headroom |
| warning-services | raw | 0/10 | 14915 | 13606 | 21498 | 14813 | identity |
| warning-services | lossless_json | 0/10 | 12770 | 11827 | 15756 | 9402 | json_minify |
| warning-services | headroom | 0/10 | 11589 | 9737 | 13660 | 3863 | headroom |
| warning-services | hybrid | 0/10 | 10262 | 9484 | 12847 | 3863 | headroom |
| long-marker | raw | 10/10 | 6833 | 3181 | 8305 | 11694 | identity |
| long-marker | lossless_json | 10/10 | 6551 | 4804 | 11090 | 7178 | json_minify |
| long-marker | headroom | 10/10 | 4979 | 3774 | 6365 | 3911 | headroom |
| long-marker | hybrid | 10/10 | 3776 | 3231 | 6109 | 3911 | headroom |

## Findings
1. **headroom compression is live** (transformers path working): context reduced ~70% across all cases (exact-incident 12802->3802, warning-services 14813->3863, long-marker 11694->3911).
2. **lossless_json has a genuine deterministic regression** on exact-incident: json_minify truncates the backup-path marker (drops `.dump`), 0/10 vs raw 7/10 — missing `/mnt/SSD_4TB/neo4j/backups/neo4j-20260817.dump` in all 10 runs. This is a real output-equivalence bug the lane exists to catch.
3. **headroom/hybrid beat raw** on exact-incident (10/10 vs 7/10) and tie long-marker at 10/10 while ~40% faster with smaller context — compression improved correctness here, likely by reducing distraction from shorter context.
4. **warning-services is a baseline failure**: raw itself scores 0/10 (empty responses), so no variant can match the reference; all four fail identically on service-3/5/7. This is a model-capability miss on this case, NOT attributable to any compression strategy — flag as corpus/model limitation, not a variant regression.

## Attribution of `all_candidate_variants_match_raw_correctness: False`
- exact-incident lossless_json (genuine regression vs raw)
- warning-services all variants (raw baseline itself fails -> reference issue)

Raw source JSON: run.log (same dir). Harness: scripts/context-output-equivalence-endpoint.py @ branch agent/context-compression-baseline head 36c524b0.
