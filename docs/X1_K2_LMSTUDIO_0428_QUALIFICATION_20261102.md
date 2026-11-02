# x1-370 K2-Horizon runtime qualification — November 2, 2026

## Why production is still HOLD

The deployed non-admitting K2 restorer is pinned to LM Studio 0.4.26+4,
but the installed application reports 0.4.28+2. Its refusal to change
model residency is correct. The physical model file matches its fixed
SHA-256 digest. The CLI binary matches the installed application; it
must be invoked with HOME=/home/scott, not the connector's isolated home.

The API watchdog separately restored /v1/models availability, but the API
advertises no loaded models. A healthy API cannot be counted as completed
inference. Canonical AssistX runtime projection generation 896 is expired,
so production dispatch remains NO-GO independently of API/model readiness.

## Non-admitting review evidence

Run from the pinned x1 host only:

    python3 -B scripts/check-x1-k2-compatibility.py --estimate-only

This computes the pinned model fingerprint; validates actual application,
CLI, available memory, root free space, and a bounded loopback catalog
GET; and optionally calls the exact pinned LM Studio CLI with
--estimate-only. It never issues a completion, loads the model,
changes the K2 restore script, writes to Neo4j, or renews approval.

As of November 2, this preflight observed:

- Installed app 0.4.28+2 versus reviewed 0.4.26+4: HOLD
- Pinned K2 artifact SHA-256: MATCH
- CLI binary and installed bundled CLI SHA-256: MATCH
- Memory and root-space safety guards: PASS
- LM Studio /v1/models: available, zero loaded models
- Context 64,000, parallel slots 4: estimated total 5.13 GiB
- Isolated decode canary: NOT RUN
- Signed runtime physical evidence / operator approval: NOT PRESENT

All observations are provisional and require a fresh time-bounded rerun
before any operational decision.

## Smallest safe progression

1. Review installed LM Studio 0.4.28+2 compatibility, pinned model identity,
   actual GPU/CPU placement and expected parallel/context settings.
2. In a separately approved maintenance test, run a **non-production
   synthetic-only decode canary**. Keep it isolated from router traffic and
   live traces; bound memory, time, token output and concurrency, record
   measured usage and a cleanup/unload result. An estimate-only result is
   not an adequate substitute.
3. Independently verify model artifact, process identity, version, admitted
   transport paths, capacity and a current operator-approved change record.
   Retain signed evidence. Reject any unverified or stale identity.
4. Generate a non-self-authorizing candidate targeting the current canonical
   generation with compare-and-swap fencing. Do **not** fabricate approval
   identity, extend an expired lease without attestation, or disable expiry.
5. Only after review of that evidence, consider the existing AssistX
   operator-controlled approval path and read back the signed projection.
   Then run a bounded real model invocation through an admitted route and
   reconcile provider-reported prompt/output tokens with immutable request ID.

No step is automated by the preflight. Passing every passive gate in the
script **always** yields HOLD_REQUIRES_OPERATOR_REVIEW.
