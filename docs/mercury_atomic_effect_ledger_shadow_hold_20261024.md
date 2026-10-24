# Mercury atomic synthetic effect ledger — DRAFT / HOLD

The prior check_effect is an advisory snapshot, so it cannot safely authorize
a later external action across a revocation. This additive test-only model
checks the exact epoch, node, seat, task, token, fixture timestamp and unique
effect ID inside the SAME SQLite BEGIN IMMEDIATE transaction as a local
synthetic marker. A resource-side rotation serializes with this insertion
when BOTH use the same database. Ambiguous duplicate acknowledgments quarantine.

The deliberately negative regressions show a deleted local journal suffix
looks healthy without an independently controlled high-water anchor, and
copying the *whole resource database* yields conflicting independent ledgers.

ALL responses explicitly contain dispatch_authorized=false, provider_calls=0
and real_effects=0. There are no real provider requests, resource effects,
Tailscale listeners, credentials, or deployed services. SQLite is copyable.
Fixture clocks, ownership, and rotation are not authenticated. A local
transaction cannot contain a remote vendor-side operation, and an already
streaming vendor request may continue after revocation. This is NOT a
production-safe admission or cancellation implementation.

Run with Python 3.12 stdlib in an isolated working directory, without
running any Mercury daemon. Keep PR #239 DRAFT and knowledge issue #57 OPEN.

