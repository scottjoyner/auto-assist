# HarnessRouter canary adoption

HarnessRouter is an execution-lifecycle adapter only. AssistX remains authoritative for scheduling, admission, node and model selection, approvals, leases, provider policy, and mutation authority.

The upstream contract is pinned to HarnessRouter v0.31.5 / UHP 2026-10-04. Discovery uses GET /v1/harnesses and GET /v1/models. Background canary work uses POST /v1/responses with metadata.harness_id, model, stream=false, and background=true. Polling uses GET /v1/responses/{id}; cancellation uses POST /v1/responses/{id}/cancel.

The adapter defaults to discovery-only. Submission or cancellation requires canary_enabled=true in config, explicit_opt_in=true on each call, and membership in the configured harness allowlist when one is present. These checks occur before any network request.

API keys are held only in runtime config and are excluded from dataclass repr/equality and from lifecycle receipts. Receipts normalize response id, session id, status, model, usage, previous response id, and error state without changing any routing decision.

Promotion should begin with one internal harness and one harmless background task. Do not make HarnessRouter a scheduler or provider router; it should remain a selected-harness transport and lifecycle normalization layer.
