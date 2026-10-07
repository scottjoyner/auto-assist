# RTK sidecar adoption

RTK is opt-in output compaction only. It does not own command execution policy, routing, approval, or shell rewriting during the pilot.

The fleet pilot pin is rtk 0.51.0. Stage the upstream release artifact under a user-local tools directory and verify its published SHA-256 before use. Do not run global RTK init hooks in the first phase.

RTKAdapter executes the caller-approved argv directly exactly once, records raw stdout and stderr byte-for-byte, and only then pipes captured stdout through an explicitly selected RTK filter. This avoids repeating commands with side effects.

If RTK is missing, its version or digest is wrong, filtering times out, or the filter exits nonzero, the model-facing output falls back to the raw stdout. The underlying command is never retried by the adapter.

Promotion should compare context bytes or tokens saved against task quality while retaining the raw evidence artifacts for replay and audit.
