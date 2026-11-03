# x1-370 K2 process/socket/model physical witness — Nov 2, 2026

Research-only, read-only and **always non-admitting**. This local diagnostic
is neither a signed attestation nor a production approval token.

## Why this is needed

An HTTP 200 model catalog and open TCP listener cannot prove which
physical process serves a loaded model or which executable is running.

The new inspector attempts four independent checks:

1. A unique LM Studio service process, kernel boot digest, PID start ticks,
   proc-executable image hash and matching installed executable.
2. Socket-inode ownership of EACH exact IPv4 listener under the actual
   two-tier topology:
   - LM Studio or one of its bounded descendants owns 127.0.0.1:1234.
   - The separate, exact-command socat LAN forwarder owns
     192.168.1.237:1234, forwarding to 127.0.0.1:1234.
   - The separate, exact-command socat Tailscale forwarder owns
     100.64.43.123:1234, forwarding to 127.0.0.1:1234.
   Each proxy also needs its running executable and start tick checked.
   A healthy proxy service or its declared command alone is not proof of
   socket ownership.
3. A regular, nonsymlinked 2,159,424,896-byte K2 GGUF matching pinned
   SHA-256 371010db1807bb07b62e738422ee0de26c1e15a347f31108ed2c6e219095a8b8,
   plus a model mapping observed in the runtime process tree.
4. Exact model identifier visible from bounded HTTP GETs on loopback, LAN
   and Tailscale, with proxies and redirects disabled.

The process start tick is checked AGAIN after the API probes to reject
PID reuse. Even a fully satisfied local diagnostic remains gate HOLD,
dispatch false, with no signed witness or operator approval issued.

These observations are not atomic: a mapped GGUF alone does not prove
successful inference or immutable process custody. Do NOT use this
diagnostic as a substitute for a signed independent attestation.

## Actual live x1-370 result

- Exact on-disk GGUF SHA-256 and size: PASS
- LM Studio application installed: 0.4.28+2
- Reviewed resident recovery pin: 0.4.26+4 (mismatch)
- LM Studio service root PID: 3525; original start ticks: 1761
- Observed process tree: eight processes
- Observed separate LAN/Tailscale socat proxy processes: PIDs 8991 and
  8992; the host proxy is PID 8978. These are independent of the
  LM Studio process descendants and must be attested separately.
- Actual proc-executable inspection: permission denied
- Server and proxy executable/socket ownership: UNVERIFIED
- All loopback/LAN/Tailscale model API paths reachable, each has 0
  loaded K2 instances
- Cryptographic witness and operator approval: ABSENT
- Decision: HOLD, can_dispatch false

The host /proc permission boundary must NOT be bypassed using
unsupported privileges, kernel changes or assumptions from ps/ss
output. A separate, authorized independently signed witness is needed.

## Test and release boundaries

The tool performs only local filesystem and proc reads, SHA-256 of an
exact model file, and bounded private HTTP GETs. No model generation,
loading/unloading, runtime restarts, Neo4j writes, live trace access,
paid APIs, file creation, or router promotion.

Thirteen synthetic adversarial tests cover exact socket inodes, PID reuse,
GGUF symlinks, model absence, missing memory mapping, unreadable proc
metadata, ambiguous processes, redirects and fail-closed admission.
Even a complete synthetic local chain NEVER produces can_dispatch true.

To progress: review this diagnostic and its limits; build an authorized
signed host attestation service with exact executable, socket and model
identity; qualify the current LM Studio build for 64k/four-slot use in a
separate sandbox; then obtain independently reviewed physical dual-path
runtime proof and signed canonical compare-and-swap approval. Do not
automatically renew expired leases or relax the LM Studio version pin.

This draft performs no deployment, admission or production change.
