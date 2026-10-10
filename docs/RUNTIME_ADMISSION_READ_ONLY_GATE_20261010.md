# Runtime admission: read-only diagnostic gate (2026-10-10)

The iPhone's Agent Auto status is intentionally driven by **signed** runtime admission,
not raw Tailnet peer connectivity or individual model listeners.
A healthy HTTPS gateway and an HTTP 200 with a **zero-runtime catalog** are not proof
that any executor is currently authorized.

## Run boundary

Use `scripts/diagnose-runtime-admission.py` **inside the existing authenticated
AssistX API environment**, where `assistx.api._neo` already has its normal read role.
It executes five static Cypher MATCH/RETURN queries. It does not modify the graph,
renew approvals, mint model handles, load a model, or send inference requests.

Output is an operator-only JSON report. Do not expose it as a public endpoint:
revision and current evidence counts are operational metadata.

## Independent acceptance on x1-370

At approximately 11:56 EDT on October 10:
- Canonical generation 642 was approved but expired; its revision belonged to a
  different workflow, so the existing renewal timer correctly held.
- Runtimes: 20 total / 1 approved / **0 current approved evidence**.
- Loaded model instances: 44 / 1 / **0**.
- Access paths: 35 / 2 / **0**.
- Capacity observations: 2451 / 1 / **0**.
- Status: blocked with five explicit reasons. Five isolated unit tests passed.

These counts describe graph records, **not** simultaneously runnable capacity.
`candidate_evidence_present_not_verified` is deliberately not an admission decision.
Signed projection generation, fresh identity/evidence, cross-layer relationships,
completion canaries and independent approval remain authoritative.

## Next acceptance

1. Renew/reconcile the foreign canonical revision only through its owning workflow,
   without changing approval identity, generation ownership, or downstream fencing.
2. Independently acquire fresh runtime/model, LAN/Tailnet access and capacity evidence.
3. Require a fresh signed projection plus a real traced completion before presenting
   Agent Auto as ready; verify stale handles continue failing closed.
4. Re-run this read-only report immediately before any iOS/TestFlight acceptance.
