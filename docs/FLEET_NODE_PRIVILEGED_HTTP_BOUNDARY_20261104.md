# Fleet node-agent privileged HTTP transport — current-main source-only security research

**Draft; production deployment HOLD.** The currently running fleet-node agents poll legacy `/api/agent/tasks`, which requires operator Basic auth, while current per-node credentials received HTTP 401 in prior physical checks. Changing an HTTP transport does NOT grant machine Task admission. Current signed supply remains expired/zero.

## New verified credential-forwarding defect

The current main `fleet_node_agent._http` builds `Authorization: Basic` or receives locally scoped LM Studio bearer / `X-Fleet-Node-Token` headers, then calls default `urllib.request.urlopen`. Python's default HTTPRedirectHandler **forwards both Basic and X-Fleet-Node-Token headers to a synthetic different-origin HTTP 302 URL**; a pure-stdlib proof reproduces the behavior. This is an actual code/security vulnerability, not evidence of an observed live credential leak.

## Minimal guard

When the request has Basic or an explicitly privileged header, use an opener with empty proxy settings and a redirect handler that rejects every redirect. Preserve existing uncredentialed request behavior; never retry without credentials. Read privileged responses with a **1MiB hard capture limit**, read error bodies with a **64KiB limit**, and return sanitized error categories rather than exception strings that could include a privileged URL. Keep LM Studio required-mode credential validation, physical node capability discovery, all Task status/audience/signed-capacity rules, and worker scheduling unchanged. No live service restart or credential migration.

## Gates

- Exact source compilation and pure stdlib socketless regression tests: synthetic default redirect demonstrably forwards secrets, hardened handler rejects it, Basic/node/bearer API request proxies disabled, no unprivileged behavioral change, oversized response/error denied, errors sanitized.
- Full current-main hosted CI, recovery canary and runtime-image build; independent release review of all caller behavior. Missing optional node-policy credentials remain a hard deny.
- When release authorized: client-first staged deploy on one node, validate real redirect denial (including cross-origin), HTTP error classifications, no credential leakage, Task 401 stays blocked until scoped server auth, rollback to exact prepatch image.

**No production task authorization, token, model, service, database, router or firewall changes.**
