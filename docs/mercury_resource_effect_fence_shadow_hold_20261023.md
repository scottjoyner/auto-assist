# Mercury simulated resource-side effect fence — NO PRODUCTION AUTHORITY

This additive shadow experiment extends draft [PR #239](https://github.com/scottjoyner/auto-assist/pull/239).
It is disconnected from AssistX, Mercury SDK, HTTP routing, any provider
credentials, NAS and runtime services. It imports Python stdlib only.

## Three distinct layers
1. **Copyable coordinators** may hold stale local lease receipts.
2. A **single external resource-side gate** stores an independent monotonic
   epoch, a leader record, a nonreplayable request ledger, and an atomic seat.
   Every simulated downstream effect rechecks exact live epoch, task, token,
   leader and TTL rather than trusting a coordinator-side success message.
3. A separately invoked **fixture admin rotation** revokes the old epoch and
   current seat. There is no authenticated administrative protocol; workers
   must never be offered this method.

The fixture returns `SHADOW_RESERVED_ONLY` or `SHADOW_EFFECT_ONLY` solely
as test assertions. Even these results contain `dispatch_authorized=false`
and `provider_calls=0`, and cause NO real side effect.

## Positive / negative testing
- Two copies of coordinator state cannot both obtain a slot from the same gate.
- Partitioned clients cannot obtain simulated effects, including already
  reserved tokens; stale token and stale epoch are refused after rotation.
- Wrong task, token, group, replayed nonce, expired TTL, clock rollback,
  malformed numbers and concurrent contenders are denied.
- Deliberate counterexample: **copy the resource-side gate SQLite itself**;
  both copies then independently grant the same slot. This continues to
  disprove distributed ownership / irreversible high-water proof.

No global quorum, nonrollbackable key custody, protected signer, trusted clock,
live provider-enforced spend fence, in-flight stream cancellation, or signed
restore witness exists. `sqlite3` FULL sync is a local durability setting,
NOT cryptographic or external permanence.

## Test
```bash
cd integrations/mercury/research
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q \
  test_shadow_resource_effect_gate test_shadow_shared_spend_witness \
  test_physical_lease_probe
```

**Decision:** research-only. Leave PR DRAFT and shared quota issue #57 OPEN.
Never activate production routing, model generation, or copied SQLite issuer
state on the strength of these synthetic checks.

