# Router administrator credential transport: never inherit proxy settings

Research draft / SOURCE-ONLY / production HOLD. Stacked directly on
Auto-Assist PR #288, authenticated provider-catalog client preparation.
No deployed services, route changes or Task authorization.

## Source finding

The authenticated client in src/assistx/control_room.py rejects redirects on
privileged router metadata GETs but constructs its opener with only
_RejectAdminRedirect(). Standard Python urllib.request.build_opener adds a
default ProxyHandler based on HTTP_PROXY and HTTPS_PROXY environment settings.
A credential-bearing X-Admin-Token request could reach an unintended proxy.
This is a POTENTIAL forwarding surface, not proof of any live credential leak.

Read-only runtime inspection found common proxy environment variables ABSENT
in the current running assistx-api container; no credential values were read.

## Fix

For requests carrying X-Admin-Token, explicitly use ProxyHandler({}) with the
existing redirect-denial handler. That disables inherited environment proxies
for the privileged request, even if environment settings later change.
Unprivileged health checks, bounded responses and timeouts are unchanged.

## Validation and limits

Synthetic HTTP and HTTPS proxy environment settings were injected; the
actual opener factory is inspected for an explicit empty proxy handler,
absence of any installed inherited proxy handler, and retained redirect deny.
Python optimizes the empty proxy handler out of the final installed handler
list after suppressing automatic proxy installation.

The existing Control Room and provider-inventory tests are also exercised.
Local exact-tree result: 38 tests passed plus 35 additional subtests.
Source lint, Python compilation and diff checks passed.
Hosted GitHub workflow outcome must be checked separately on the exact head.

This does not prove current router security. The deployed router still answers
HTTP 200 to an anonymous admin GET. Client-first staged promotion requires
approved image and credential ownership, remote anonymous 401 verification,
and rollback before Router PR #41 / optional #123 cutover.

Task dispatch remains HOLD on expired signed projection, zero slots,
machine identities, complete graph writer fences and independent custody.
No production service, model, graph, Task, route or credential was changed.
