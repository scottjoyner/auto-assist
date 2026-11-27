# AssistX mobile traceparent boundary — observational correlation

Status: draft, stacked on mobile runtime catalog fail-closed fix PR #254.

W3C traceparent from a trusted or Basic-authenticated mobile request is treated exclusively as **opaque correlation metadata**. It is never a credential, Tailnet identity proof, approval, model handle or admission lease.

A strict version-00 validator accepts only 55 characters containing lowercase hex trace ID (32 digits), span ID (16) and flags (2), rejecting zero IDs, control characters, URL fragments and invalid formats. After the existing mobile authentication/allowlist logic runs, accepted headers:
- are echoed by the mobile Agent route at the HTTP boundary (this **does not** claim Hermes downstream spans);
- are echoed by the mobile Model route and forwarded to the approved fleet router over its existing authenticated client, without changing the model handle, identity or bearer.

No raw prompt/response, trace content, passwords or user identity is written to new logs. No autonomous trace upload is added. W3C downstream retention, router span storage, exporter consent and signed trace receipts remain future dependencies.

Test evidence: 25 mobile-focused pytest tests passing, including invalid-header rejection, Agent gateway response echo, authorized model router forwarding and unchanged Bearer admission.

Release: This branch depends on #254. Do not deploy the combined change before source reconciliation, CI, and approved rollout. The production canonical runtime approval may remain expired, and a trace ID never changes that gate.
