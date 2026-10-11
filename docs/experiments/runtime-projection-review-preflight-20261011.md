# Runtime projection: evidence-freshness review preflight — October 11, 2026

**Status: source-only research tool + live *unsigned* operator review handoff; NO approval renewal or authority promotion.** See [Auto-Router P0 issue #52](https://github.com/scottjoyner/auto-router/issues/52), [draft #53](https://github.com/scottjoyner/auto-router/pull/53) and [draft #50](https://github.com/scottjoyner/auto-router/pull/50).

## Concrete bug class motivating this guard

Current official `scripts/approve-runtime-projection.py` has a **900-second maximum TTL**, manifest field checks, a generation compare-and-swap and a disabled-by-default `--apply` step. Those are useful. However, `validate_manifest()` checks that evidence references and approval text are present without independently verifying the observed times or source authenticity. During the authorized write, `_apply_transaction()` stamps new `observed_at_ts=$now_ms` for runtime and other source nodes. An operator mistakenly reusing an **old** evidence manifest could therefore make an old observation appear fresh. This is an identified **potential** stale-proof replay weakness, not a claim it has been exploited. **Validation-only reproduction:** without any database write, the original expired generation 643 manifest was copied *in memory*, its proposed generation changed to 644 with expected generation 643, and its reviewer/approval fields changed to placeholder `pending_operator_review`. The official `validate_manifest()` still returned PASS. That does **not** bypass the actual canonical CAS or prove production exploitation; it demonstrates that structural validation alone cannot be an approval security boundary.

This new **independent, non-mutating** tool deliberately does NOT change `approve-runtime-projection.py`, relax signatures, invent a reviewer ID, generate an actionable manifest or reissue an approval. It checks preconditions that a future **independently authenticated** source adapter should enforce in the actual approval path.

## New code

`scripts/runtime-projection-review-preflight.py`: strict, bounded **unsigned review-only** JSON contract, standard library only. Checks:
- Monotonic expected generation (currently canonical 643 → *candidate* 644), never creates that generation.
- Physical model observation age **≤5 minutes**; both LAN and Tailscale direct canary observation ages **≤3 minutes**, with timestamps no more than 5 seconds in the future.
- GGUF SHA256/bytes match reviewed expectations; exactly one process and one loaded inventory indicator, positive context and PID.
- Both private paths with HTTP 200, **exact marker** and ≤24 token output budgets.
- Declared capacity ≤ the observed process parallelism; capacity is still unverified admission and must remain constrained by a separate authority.
- Explicit rejection of any embedded approval ID, reviewer credential or claim of an independent host signature in this **unsigned** file.
- No unknown/raw fields and no admission token, private endpoint, model prompt or auth credential in outputs.

**Result is always REVIEW_BLOCKED and** `can_apply=false`, `can_auto_renew=false`, `can_dispatch=false`, `operator_approval_minted=false`, `source_attested=false` **even if all physical checks pass.** This protects against mistaking a local report for a trusted signer/owner receipt. A new authorization can only be granted by the separately reviewed official process.

Run standard-library tests with:
```sh
python3 -m unittest discover -s tests -p 'test_runtime_projection_review_preflight.py' -q
ruff check scripts/runtime-projection-review-preflight.py tests/test_runtime_projection_review_preflight.py
```

## Read-only physical check performed through Fleet Commander

After the initial time-limited **generation 643** properly restored AssistX and later expired at 17:16:38.815 UTC, a new independent read-only poll at approximately **2026-10-11T17:41 UTC** found canonical generation 643 still expired. Beelink was re-observed via its private Tailscale SSH path without uploading a persistent executable:

- Exact GGUF SHA256 `c40c8a9239dbc2d8874c5508110b9b1f3d325376eecffdba7bce2dcb49a0207c` matches prior reviewed LFM 1.2B Q8_0, file size 1,246,255,456 bytes.
- Precisely one physical llama-server process matched, with 8192 context, one loaded LM Studio catalog instance.
- Fresh private LAN canary: HTTP **200** with exact marker, approximately **346 ms**.
- Fresh private Tailscale canary: HTTP **200** with exact marker, approximately **267 ms**.
- The current approved canonical generation was **643 (expired)**; no new Neo4j nodes, approvals or runtime changes were made.
- Review-only proposal generation **644** has **no** new approval ID, reviewer or independent host signature. It was **NOT applied**. Review gate at capture time: `physical_and_path_evidence_fresh=true`, `status=REVIEW_BLOCKED`; blockers were `NEW_EXPLICIT_OPERATOR_APPROVAL_REQUIRED`, `CANONICAL_GENERATION_NOT_INDEPENDENTLY_ATTESTED`, and `TRUSTED_SOURCE_AND_PER_USER_RECEIPTS_OUTSTANDING`.

The unsigned private review document is stored only in an existing SSD-backed `/home/scott/.local/state/assistx-runtime-approvals/review-only-...` directory on x1, mode **0700**, single JSON file **0600**, size **1,198 bytes**, SHA256 **`905967f4e2ca6a415bd71bb06e32b8ca7a092671265d0b5f0864484ec25a2732`**. It is intentionally NOT included in the public repository or written to NAS. This short-lived evidence should be **re-observed** for an actual review, not taken as currently fresh after its 3–5 minute age windows.

## Exact safe operator handoff

1. The designated operator reviews and approves a **new** approval cycle, rather than treating the past 900-second approval as ongoing authorization. Reobserve actual current host model process, source hash, loaded status, capacity, and private-path canaries immediately before that review.
2. Independently attest authentic host/source identity, signer/reviewer and owner-bound request/reservation provenance. These remain incomplete; a local SHA256 is **not** an independently signed execution receipt.
3. Prepare a new official manifest with correctly renewed **reviewed evidence references** and unique operator approval ID, never recycle 643's approval ID. Obtain a clean dry run, then require explicit approval for the actual CAS-protected `--apply` command.
4. After any authorized new generation, read-only verify Neo4j current runtime/model/path/capacity rows, Authenticated AssistX projection 200, and router `fresh=true` for the same generation/expiry.
5. Handle the independent two-user receipt gate in Auto-Router issue #37, anonymous legacy admin endpoint in PR #41/AssistX #288 and longer-term durability through a source-backed, operator-authorized renewal process. **Never schedule automatic reapproval** simply because the time-to-expiry is low.

**NO-GO:** current preflight does not prove the operator has approved generation 644, does not grant owner admission, does not alter routing, does not issue public/private endpoint changes, does not fetch/use secrets, and does not write to Neo4j/NAS.
