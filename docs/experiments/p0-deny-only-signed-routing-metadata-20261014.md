# P0 deny-only routing metadata on signed existing runtime projections

October 14, 2026 — Fleet Commander x1-370, draft-only stacked research.

## Source boundary

The v2 runtime projection inherits its *admitted*, live, lease-bound
providers and models from `runtime_projection.build_runtime_projection`.
It now attaches read-only `FleetNode` roles and benchmark summaries
through `fleet_routing_projection` to those **existing** rows, before
computing v2 checksum/Ed25519 signature. Missing role policy defaults to
`observer_only`; unknown or unbenchmarked model IDs receive no scores.
A profile for another node or alias cannot add a model or provider.

**Neither role nor benchmark metadata grants permissions:** both provider
and model `allow_agent_runtime` and `allow_code_execution` remain
explicitly false, even where untrusted policy fields say true. A failed
graph read denies the whole signed projection rather than issuing partly
attributed metadata. No DB writes or source model loads are performed.

## Evidence and limits

- 27/27 local synthetic checks pass across routing-matrix, v1/v2 signed
  projections, mobile-runtime catalog, including three new negative
  signature/graph-read tests; Ruff and diff checks clean.
- Live signing-key custody, benchmark witness provenance, administrator
  independence, production graph writer isolation, immutable high-water
  and post-readback fencing are not established.
- The graph read is only a research-side code path at this stage; no
  deployed API/worker reroutes, schema/grants, models or approval renewals.
- Exact-head hosted CI, independent review, and B01/B02/B06 release
  acceptance are still necessary. Fail closed on unavailable policy source.
- In particular, adding graph metadata does **not** upgrade its trust
  from independently verified evidence. P0 production admission/failover
  remains HOLD.
