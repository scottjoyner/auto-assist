# x1-370 K2 synthetic decode evidence (2026-11-02)

Status: LOCAL SYNTHETIC CANARY PASSED; PRODUCTION ADMISSION HOLD.

The model is K2-Horizon-1B-BF16.gguf (exact pinned SHA-256:
371010db1807bb07b62e738422ee0de26c1e15a347f31108ed2c6e219095a8b8),
temporarily loaded by the existing LM Studio application 0.4.28+2.
The currently reviewed K2 restorer requires 0.4.26+4 and remains unchanged.
AssistX canonical approval generation 896 is expired; Auto-Router fails closed.

## Boundaries

The canary runs only on x1-370 with a fixed synthetic prompt and existing
loopback API, using the exact K2 64,000 context and 4 parallel load slots.
It caps output at 192 tokens and uses a 90-second idle TTL, then explicitly
unloads and verifies the model absent. It uses the same restorer lock and
fails closed on unexpected models, app version, model/CLI identity, API
readiness, resource gates, estimate failure, or uncertain cleanup.

NETWORK ISOLATION IS NOT PROVEN. The existing shared LM Studio API has LAN and
Tailscale proxies. The canary does not alter their settings, firewall rules,
signed AssistX authority, router policy or production deployment. The
test must not be represented as a fully network-isolated canary.

No prompts, generated text, reasoning contents, credentials, live trace
bodies or raw API responses are persisted. Only SHA-256 of synthetic output
and provider-returned usage metadata are retained.

## Results (three bounded local attempts, Nov 2 2026)

| UTC | Cap | Provider prompt | Provider output | Outcome | Cleanup |
| --- | ---: | ---: | ---: | --- | --- |
| 22:18:43 | 12 | unknown | unknown | incomplete response; usage not captured | unloaded |
| 22:20:34 | 48 | 27 | 48 | finish_reason=length, reasoning-only, no visible answer | unloaded |
| 22:21:14 | 192 | 27 | 99 | finish_reason=stop, visible answer, PASS | unloaded |

The successful request took 3.414 seconds end-to-end, yielding 29.0
provider-reported generated tokens/second. The earlier 48-token
reasoning-only response took 1.596 seconds (30.08 output tokens/second)
but did not satisfy visible answer qualification.

Together the last two attempts have 54 measured prompt and 147 measured
output tokens (201 total). The first attempt has UNKNOWN usage, not zero.
These are synthetic-only experimental calls, NOT fleet-wide 24-hour totals.

Immutable local evidence is under the read-only audit store:
 /home/scott/.local/state/assistx-admission-preflight/

- k2-synthetic-canary-20261102T221843Z.json
- k2-synthetic-canary-20261102T222034Z.json
- k2-synthetic-canary-20261102T222114Z.json

## Remaining gates

1. Review compatibility evidence; do not weaken the deployed version pin.
2. Independently qualify network-isolated multi-request stability,
   resource ceilings and cleanup. A single completion is not a soak.
3. Attest the actual process, exact model artifact, LAN/Tailscale paths
   and runtime capacity with separately approved physical evidence.
4. Use the original signed operator approval and compare-and-swap
   generation renewal procedure. Never fabricate or auto-extend approval.
5. Verify actual admitted router dispatch and provider-reported usage
   separately after authorizing the new generation.

No production deployment, merge, version change or graph authority change
is included in this work.
