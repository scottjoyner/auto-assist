# HarnessRouter canary adoption

HarnessRouter is an execution-lifecycle adapter only. AssistX remains authoritative for scheduling, admission, node and model selection, approvals, leases, provider policy, and mutation authority.

The upstream contract is pinned to HarnessRouter v0.31.5 / UHP 2026-10-04. For Community Edition, configure the console root (for example `http://127.0.0.1:3000`) or the explicit `/api/harness` base; the adapter normalizes the console root to that API prefix. Discovery then uses GET /v1/harnesses and GET /v1/models. Background canary work uses POST /v1/responses with metadata.harness_id, model, stream=false, and background=true. Polling uses GET /v1/responses/{id}; cancellation uses POST /v1/responses/{id}/cancel.

The adapter defaults to discovery-only. Submission or cancellation requires canary_enabled=true in config, explicit_opt_in=true on each call, and membership in the configured harness allowlist when one is present. These checks occur before any network request.

API keys are held only in runtime config and are excluded from dataclass repr/equality and from lifecycle receipts. Receipts normalize response id, session id, status, model, usage, previous response id, and error state without changing any routing decision.

Promotion should begin with one internal harness and one harmless background task. Do not make HarnessRouter a scheduler or provider router; it should remain a selected-harness transport and lifecycle normalization layer.


## Newer UHP research-only safeguards (November 2026)

The older v0.31.5 / UHP 2026-10-04 pin above remains the **existing
integration reference**. The newer [UHP 2026-10-19](https://github.com/HarnessRouter/harnessrouter/tree/f6592b27e0d46478f895100a20827d60b538acf4/protocol/versions/2026-10-19) is a draft and is **not**
an established deployment or compatibility guarantee.

The draft research hardening in
[UHP harness adapter guard evidence](research/uhp_harnessrouter_canary_guards_20261102.md)
requires explicit nonempty harness-ID allowlists, process-local
response ownership for polling/cancellation, canonical response
identifiers, strict lifecycle and usage receipts, explicit model
fallback attribution, and safe error formatting. The new read-only
`discover_protocol()` exposes advertised server features **without
claiming conformance**. Because task ownership is process-local,
older canary task IDs must **not** be silently recovered or
cancelled from a new adapter instance.

Even when a response acknowledges cancellation, only a separate
terminal lifecycle observation proves that a task stopped. Full
UHP conformance requires the published 101-check suite (including
real agent tasks) and is not established by these offline tests.

Do not merge or deploy research adapters solely on the basis of
synthetic fixtures; existing AssistX routing authority and production
NO-GO gates remain unchanged.
