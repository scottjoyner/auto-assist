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


## Oct 24 single-use lease hardening

A unique effect ID alone does not stop a caller from spending a single lease
repeatedly with fresh IDs. The new single-use token-digest constraint plus
transactional denial binds each synthetic lease to **at most one** recorded
effect. The fixtures cover serial and concurrent distinct-ID reuse and show
that a genuinely fresh post-expiry lease can record another independent mock
effect. This is a **one-effect-per-lease test policy**, not provider token
stream accounting or a claim about zero-dollar upstream quota limits.

This constraint does not prevent a privileged DBA from forging effects or
cloning the resource DB. A real provider needs actual billing-unit admission
and enforceable resource-side account identity, not a SQLite mock.
