# Jev observer, October 8, 2026: prediction and observation

Before changes: 2 failing receipt tests due to a missing optional observer.
Prediction: an explicitly invoked observer can persist only a validated
non-authoritative schema/identity-bound receipt. An absent, malformed,
identity-mismatched, or disabled observation never produces a record or decision.

Observation: 17/17 tests pass locally (15 existing, 2 new). All outgoing
HTTP calls are synthetic monkeypatched responses. The observer is deliberately
NOT wired into _process_intent and cannot autonomously dispatch or spend tokens.
Enabling it in production is a separate approval gate.
