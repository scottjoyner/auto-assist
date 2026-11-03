# K2 witness same-boot process replacement fencing — Nov 2, 2026

**DRAFT RESEARCH / NO EXTERNAL PROCESS IDENTITY SOURCE / NO PROD ADMISSION**

## Gap and reason

The K2 signed external witness verifier already has a valid-signature
check, independent (but unprovisioned) current boot identity, exact
LM Studio / socat binary digest pins, and a short freshness bound.
That excludes cross-boot replay, but it does not establish that the
specific processes described by the signed observation are STILL
running on the same boot when a receipt is verified.

A model server, LAN forwarder or Tailscale forwarder can exit and
restart within the 120-second receipt freshness window. Its boot ID
is unchanged. A previously signed observation of the former PID
and kernel process start time would still satisfy a boot-only check.

## Additive offline fail-closed contract

A new intentionally unconfigured verifier policy value is introduced:

    TRUSTED_CURRENT_PROCESS_IDENTITY = None

It is NOT accepted from the receipt, local model API, environment,
CLI argument, or this repository's test fixtures in production.
An authorized, separately administered witness mechanism would need
to supply a newly observed identity under a trusted control channel:

    {
      "node": "x1-370",
      "boot_id_sha256": "64 lowercase hex chars",
      "observed_at_ms": 1800000000000,
      "runtime": {"pid": 123, "start_ticks": 456},
      "proxies": {
        "lan": {"pid": 234, "start_ticks": 567},
        "tailscale": {"pid": 345, "start_ticks": 678}
      }
    }

All keys are exact. The independent inventory timestamp must occur
**after the signed receipt was issued**, no later than verification
time, and within 30 seconds of verification. It must match the
separately trusted current boot ID. The runtime PID/start ticks and
both distinct proxy PID/start ticks must exactly equal those from
the signed observation. The three PIDs must be distinct and all
values must be positive integers, never booleans or free-form strings.

On missing, stale, reordered, malformed or mismatched identity, refuse
with an explicit HOLD code; no fallback to the signed observation.
The receipt can be authentic yet stale if either proxy restarts.
Even full synthetic verification remains decision=HOLD,
can_dispatch=false and independent_host_custody_verified=false.

## Tests and limits

The combined isolated physical-observer and offline-signature suite
passes 74 tests. New synthetic adversarial cases include a same-boot
LM Studio process restart, each proxy PID/start tick replacement,
stale/future/pre-signature independent inventory, incorrect reference
boot, boolean process start ticks, missing reference inventory, and
duplicated runtime/proxy PID. The existing signature-tampering test
was adjusted to mutate a semantically valid model-count field,
ensuring it continues to exercise cryptographic signature rejection.

A trusted inventory cannot be created simply by copying process
identities from the signed observation under review. The current
research source has NO independently verified runtime-observer
transport, signer, replay ledger, operator custody or current-process
inventory provider. A process may also die immediately after an
otherwise fresh independent inventory is read. Production needs a
source-attested check bound to dispatch/execution time, monotonic
challenge replay fencing, operator-controlled identity authority and
canonical compare-and-swap approval.

The PR performs NO live host /proc privileged inspection, no systemd
or model changes, no inference, no trace or graph operations, no
cryptographic signing key creation, and no change to production
LM Studio/runtime approval. The real K2 model is not currently resident
on x1-370 and its canonical approval remains expired: NO-GO.
