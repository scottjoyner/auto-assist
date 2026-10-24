# Mercury failure-recovery deny-mock — synthetic acceptance, no dispatch

Owner: [knowledge issue #57](https://github.com/scottjoyner/knowledge/issues/57).
Related but separate from the merged deny-only admission mock #133, its
reason-accuracy correction draft #444 and Mercury split-brain research #239.

## What is proved here

A pure, no-network `project_failure` function classifies *synthetic* provider
observations while unconditionally denying any retry, fallback or dispatch.
No provider, actual HTTP request, model/embedding call, daemon, worker or
user/system service is imported or invoked.

- 401, 402 and 403 flag an owner-reviewed credential/billing hold, not a retry.
- 429 computes only a *suggested* minimum 15-minute cooldown; 503 suggests
  at least 5 minutes. Caller-provided Retry-After is bounded; invalid or
  over-24-hour values require manual review, never automatic retry.
- Timeout, disconnection, network errors and unknown ACKs quarantine the
  unknown spend outcome. A later 200 or cooling expiration never becomes a
  permission.
- A fallback alias in the same upstream group does not manufacture a second
  free quota; a different group is also denied without separately proven spend
  authority. Both cases are tested.
- Invalid identifiers are redacted; reasons, inert flags and an SHA256 hash
  cover only whitelisted, sanitized receipt metadata.
- Prior cooling deadlines never decrease; clock rollback, invalid integers
  and overflow do not permit attempts.

The signed/verified API user's identity, upstream quota owner, true provider
Retry-After semantics, authenticated shared alias mappings, exact zero-dollar
price/usage, resource-side epoch fencing, in-flight cancellation and signed
off-host trace custody are **not implemented** here. Even synthetically valid
observations return `decision=DENY`, `provider_calls=0` and
`dispatch_authorized=false`. Its cooldown is **advisory only**.

## Reproduce without provider access

    PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover \
      -s tests -p 'test_mercury_failure_recovery_mock.py' -v

Only Python's standard library is required. No live provider error can be
inferred from passing these fixture tests. Do not add a real retry, fallback,
network, router dispatch or timer to this research lane.

## Gate that remains

Real release requires authenticated shared upstream-group ownership and
noncopyable monotonic resource-side lease/attempt/spend authority; an
independently witnessed revocation and provider-stream cancellation; strict
account-price/usage proof; end-to-end redacted request/retry/fallback/cost
traces with independent historical restore. Keep issue #57 OPEN and all fleet
autonomous free-provider execution on HOLD.
