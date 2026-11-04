# Focused authenticated provider catalog port onto current main (research)

**Draft. No deploy, router restart, Task admission or credential change.**

The original [AssistX PR #288](https://github.com/scottjoyner/auto-assist/pull/288) is no longer directly mergeable with current main and its historical full CI failed (77 unit failures, one recovery canary). It also modifies unrelated API, JS and historical CI compositions. This branch ports only its self-contained authenticated inventory parser and a narrow Control Room callsite to **current main**. It deliberately retains the newer existing `router_admin_transport.py` and API implementation.

The effective in-memory catalog is preferred. The legacy endpoint is only used after a genuine HTTP 404 from the effective route and **still requires the admin token**. Wrong credentials, redirects, oversized responses, wrong origin overrides and malformed schemas return an empty catalog, never an anonymous fallback. The catalog proves configured/available aliases **not loaded models, service reachability or production Task authority**. Provider identity and hostname are bounded; model spelling remains case-sensitive.

Privileged `_http_json` reads are now capped at **4 MiB during capture**, rather than after unbounded allocation. Existing public health reads remain unchanged. The existing main test expecting a literal legacy endpoint override is updated to provide the matching explicit `AUTO_ROUTER_BASE_URL`, which is now required to anchor admin-credential origin.

## Proof / boundaries

Run the portable exact-module suite, the current-main Control Room integration tests, the direct full-unit and recovery-canary GitHub Actions gates, and realistic pinned Router #41 synthetic producer contract before considering merge. No CI job replaces or disables another. Neither a passing synthetic test nor this draft authorizes a production deployment. The live Router continues to expose anonymous /admin/providers HTTP 200; actual client-first cutover requires owner-approved credential custody and release/rollback plan plus remote anonymous 401 and authenticated sanitized 200 proof. Signed Task capacity stays zero.
