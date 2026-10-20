# AssistX unverified trusted-header fallback: opt-in Basic fence

Status: **research-only draft; not enabled or production approved** (2026-10-09).

## Evidence and threat model

`src/assistx/api.py::_auth_user_from_credentials` accepts a nonempty
`TRUSTED_AUTH_HEADER` value before evaluating Basic credentials. This is
safe only if every ingress strips client-supplied copies of that header and
injects identity after independent authentication. Current x1-370 read-only
inventory showed an AssistX Docker loopback listener at `127.0.0.1:8000`
and Tailscale HTTPS port `8443` forwarding multiple API paths to it.
That inventory alone does **not** attest header stripping, ingress identity,
peer access, alternate service roots, or operator roles.

[Issue #149](https://github.com/scottjoyner/auto-assist/issues/149) is OPEN.
[Issue #156](https://github.com/scottjoyner/auto-assist/issues/156) remains
an independent P0 because possible credential-bearing Git files were tracked
in a publicly visible repository.

## Source-only mitigation in this draft

`ASSISTX_REQUIRE_BASIC_AUTH` defaults to off, retaining existing client
compatibility. A future, separately approved deployment may set it to
`1` (also recognizes `true`, `yes`, `on`). When on, the application
ignores `TRUSTED_AUTH_HEADER` **for authentication** and only accepts a
valid `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` pair. If Basic is unset or
wrong, access is denied; there is no anonymous fallback.

This is an interim Basic-only boundary, **not** attested-proxy authentication,
not an identity/role authorization layer, and not a way to assert that
existing Tailscale/phone clients support Basic. It does not rotate any
credential or alter the proxy. This draft does not enable the option.

## Acceptance before any rollout

1. Obtain custody verification/rotation approval under #156; keep values out
   of public Git, logs and graph events.
2. Verify an operator-approved Basic credential store and recovery path
   without logging or printing values. Do not reuse possibly exposed keys.
3. Inventory **all** ingress/forwarding paths to port 8000, including
   Tailnet Serve, reverse proxies, local processes, and container bridges.
4. In disposable isolated staging, verify **401 before Neo4j** for anonymous
   and forged-header-only requests at `/fleet-dashboard`,
   `/api/fleet/dashboard` and trace GET routes available in the selected
   release head. Verify valid Basic, wrong Basic and header-plus-wrong-Basic.
5. Validate mobile/Tailscale clients separately. They may rely on trusted
   proxy identity and may fail once header-only auth is rejected.
6. Require operator-approved rollout/rollback for environment changes and
   authenticated browser verification; preserve #148 trace rate/concurrency
   gates and #145 full release checks.

**No activation is authorized by this document.**
