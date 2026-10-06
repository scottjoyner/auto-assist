# Ripwire sidecar adoption

Ripwire is an optional, non-authoritative code-context sidecar. AssistX keeps all routing, scheduling, approval, execution, and mutation authority.

The fleet pilot pin is ripwire 0.6.5. Stage the upstream release artifact in a user-local tool directory, verify its published SHA-256 before extraction, and pass the exact binary path plus the pinned version and digest to RipwireAdapter. Do not install Ripwire hooks globally during the pilot.

The adapter exposes three read-only flows: orient uses a bounded task lens; post_change uses situational analysis against the current diff; test_gate names relevant tests and records Ripwire exit 4 as findings rather than an execution failure.

Every invocation writes raw stdout and stderr plus metadata containing the command, exact binary version and hash, exit status, duration, and content hashes. A missing or wrong binary, timeout, or unexpected exit never changes AssistX routing.

Pilot on one repository first. Promotion requires evidence that the sidecar reduces exploratory reads or improves change and test selection without changing authoritative execution decisions.
