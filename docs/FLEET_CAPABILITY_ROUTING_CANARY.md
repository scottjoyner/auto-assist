# Fleet capability routing canary packet

## Positive canaries

1. Import a matrix containing at least three discovered tailnet nodes.
2. Verify every node is present in `/api/fleet/routing-matrix`.
3. Verify observer-only peers appear in `/api/router/context-projection` with a
   blocked lane.
4. Verify an admitted auxiliary model can receive a summarization task.
5. Verify an admitted auxiliary model can receive a compression task when its
   quality floor passes.
6. Verify coding selects only a full-agent or code-agent node with code execution
   explicitly allowed.
7. Verify every completed route records task, claim, agent, projection generation,
   physical runtime, and loaded model identity.

## Negative canaries

1. A two-node partial census fails the configured discovery-count gate.
2. An unlisted phone or miscellaneous Tailscale peer is visible but not placeable.
3. An auxiliary-only node is rejected for coding and unrestricted tool execution.
4. A measured quality-floor failure cannot beat qualified evidence on speed.
5. An offline node remains visible but is not placeable.
6. A routing matrix cannot create a runtime provider or loaded model.
7. An expired runtime projection is rejected.
8. A stale claim is rejected before provider dispatch.
9. A terminated worker does not release capacity until termination is proven.
10. Duplicate completion with an old claim ID is rejected.

## Evidence to retain

- Tailscale status JSON used for the matrix;
- reviewed role policy fingerprint;
- exact-loadout comparison artifacts;
- matrix fingerprint and import timestamp;
- AssistX context and runtime projection snapshots;
- allocation recommendations for each family;
- auto-router route decisions and claim-status rechecks;
- completion and negative-canary records.
