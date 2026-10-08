# Shared free-provider admission: pre-registered next experiment
Date: 2026-10-07 EDT. This section is **prospective**, specified before the
test run. The 49-session baseline was previously observed and is not
retrospectively treated as randomized evidence.

## Hypotheses
- H1: With a single configured provider slot, 20 simultaneous independent
  clients contending through one SQLite ledger produce at most one active
  unexpired lease. All other requests are denied before inference.
- H2: Idempotency retries with the same request key do not increase request
  or token reservations, and cannot resurrect a released lease.
- H3: A provider-scoped 429/503 suppresses new grants for at least 3600
  seconds, 401/402/403 for at least 86400 seconds; unrelated providers
  remain independent.
- H4: A lease expires, cannot renew after expiration, and is never counted
  active beyond expiry. Workers MUST enforce expiry themselves; this
  prototype cannot stop misbehaving clients.
- H5: Every permit, denial, circuit trip, release and renewal leaves a
  verifiable chained decision record; no generation API calls are made.

## Planned apparatus, metrics and pass/fail gate
Isolated temporary SQLite databases, multi-thread contention, injected
timestamps and malformed/stale inputs. Report number of admitted leases,
maximum overlapping active leases, duplicate-key behavior, cooldown
durations, decisions, audit chain verification and test pass/fail count.
No live providers, API credentials, real inference, or paid credits.

## Scope and constraints
This is a **candidate ledger**, not currently deployed as network
authority. No automatic rewrite of auto-router/admission.py or
Hermes/AutoClaw/OmniRoute. Local SQLite database on x1-370 only.
Do not run SQLite WAL on CIFS NAS. Remote clients require a reviewed
authenticated service with Tailscale ACLs, TLS/mTLS or mutually-authenticated
identities, replay protection, rate limit, resilience, health/metrics and
fail-closed client-side enforcement. Aggregators must resolve a verified
physical upstream key, not bypass limits by using a different alias.

## Observations and disposition (recorded after execution)
- Code: `scripts/shared_provider_leases.py`.
- Reproduction: `python3 scripts/experiment_shared_provider_leases.py`.
- Machine-readable result:
  `artifacts/shared-provider-lease-pilot-20261007.json`.
- Actual synthetic 20-thread trial: one granted, 19 denied; one maximum
  concurrently active slot; 24 hash-chained audit events; elapsed 1.1614s.
- A separately tested eight-**process** contention scenario also admitted
  one grant and denied the rest. The prospective concurrency requirement
  is met for this host and temporary SQLite files.
- Provider-local 429 circuit, idempotent requests, expiry rejection,
  quota-window reservations, tamper detection, and monotone renewal tested.
- Focused regression run: 56 tests passed, 1.64 seconds.
- Generated inference tokens: zero. No provider network requests.
- All synthetic checks passed. This does not measure upstream quota,
  distributed quorum correctness, real generation success, or failover.

SQLite IMMEDIATE semantics reference:
https://www.sqlite.org/lang_transaction.html
HTTP 429 / Retry-After reference:
https://www.rfc-editor.org/rfc/rfc6585

## Remaining blockers before use in production
The database remains prototype-only and is never put on CIFS NAS.
The audit chain is NOT immutable/notarized; unauthorized SQL modifications
can be made by a privileged actor. The current prototype does not log
malformed pre-transaction inputs or all unauthorized releases, nor can
it stop a model worker after a lost/expired lease. It is NOT a live
fleet-wide admission system. Authentication, lease fencing, telemetry
attribution to a physical upstream provider, trusted network service,
crash recovery, verified isolation, and failure-injection testing
remain required before clients are switched over.
