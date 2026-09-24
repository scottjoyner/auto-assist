# Runtime Observation Reconciliation

This is the read-only operator path for comparing live fleet runtime observations
with the signed AssistX runtime projection.

It does **not** admit a runtime, write Neo4j, create a projection generation, add
an Auto-Router provider, or authorize a newly observed model.

## Authority boundary

The evidence flow is intentionally one-way:

```text
LMS runtime observation
        |
        v
Auto-Router bounded/sanitized fleet evidence
        |
        v
AssistX reconciliation
        |
        +----> operator review only
        |
        X no admission/routing mutation
```

Signed AssistX projection remains the only source of runtime/model admission.
Auto-Router remains the consumer of that signed projection.

Runtime observations always remain `admitted: false`.

## Exact cross-repository dependency heads

The PR contract currently pins:

- auto-router: `251cdc4532041f1d0616bd95c0f939bd99be0bb9`
- lms: `4243b24394dc6746e65c2a3716ad7823dc4b03c0`

The Auto-Assist head is recorded by the cross-repository workflow as
`GITHUB_SHA` in the uploaded repository matrix.

## Smallest operator evidence package

Capture four read-only inputs at approximately the same time:

1. `nodes.json` — Auto-Router `GET /api/fleet/nodes`.
2. `projection.json` — the schema-v2 Ed25519 AssistX runtime projection.
3. `router-status.json` — Auto-Router `GET /admin/runtime-projection`.
4. The configured Ed25519 public verification key.

Then run:

```bash
python scripts/reconcile-runtime-observations.py \
  --nodes evidence/nodes.json \
  --projection evidence/projection.json \
  --router-status evidence/router-status.json \
  --verify-key-file /path/to/runtime-projection-public.pem \
  --expected-key-id assistx-runtime-projection-v1 \
  --max-observation-age-seconds 180 \
  --output evidence/reconciliation.json
```

Use the deployed key ID rather than the example value above if it differs.

The command fails before reconciliation when:

- the projection is not schema-v2 Ed25519;
- its checksum/signature is invalid;
- the signing key ID is not the expected operator key;
- the projection is expired or has invalid timestamps;
- Auto-Router has no active/fresh projection;
- Auto-Router's active generation, revision, or checksum differs from the
  verified projection.

The output also records SHA-256 hashes of all input files.

## Classification contract

### `projected`

Exactly one signed provider matches node + serving port + runtime kind, the
observation is fresh and ready, evidence was not truncated, the transport-observed
source IP positively matches one of the signed provider's literal IP access paths,
and every projected model group is represented by either its alias or provider-model
ID with no unexpected observed models.

This means **endpoint and model-name agreement only**.

It does **not** prove the currently loaded bytes equal the signed
`artifact_fingerprint`. The report therefore always records:

```json
{
  "artifact_identity_verified": false,
  "identity_evidence_level": "endpoint_and_model_names"
}
```

Artifact continuity remains an independent operator/canary evidence requirement.

### `unprojected_runtime`

No signed provider matches the observed node + port + runtime kind and there is no
signed candidate on the same node + port.

The report enumerates the existing admission evidence requirements. Observation
alone must never satisfy those gates.

### `model_drift`

A single signed runtime matches, but either:

- an observed model is absent from the signed provider; or
- a projected model is no longer observed.

Comparison is symmetric. Alias/provider-model alternatives for one projected
model are treated as the same logical model.

### `ambiguous_projection_match`

More than one signed provider matches node + port + runtime kind.

No candidate is selected heuristically.

### `runtime_identity_mismatch`

Either:

- a signed provider exists on the same node + port under a different runtime
  kind; or
- the transport-observed source IP conflicts with the signed provider's literal
  IP access paths.

This is identity review, not a request to create a new admission.

### `runtime_identity_unverified`

The report does not provide a transport-observed IP that can be positively
matched to one of the signed provider's literal IP access paths.

A sender-supplied hostname or `reported_ip` is not enough to produce
`projected`.

### `runtime_not_ready`

The runtime reports `ready: false` or has no observed served models.

LMS now treats a parseable but empty `/v1/models` response as not ready.

### `stale_observation`

The runtime observation timestamp or Auto-Router receipt timestamp is missing,
older than the configured maximum age, or materially in the future.

### `incomplete_observation`

Auto-Router had to truncate either the runtime list or one runtime's model list.

Incomplete evidence is never accepted as `projected`.

## Evidence provenance

Auto-Router preserves the socket-observed source separately:

- `source_ip`: transport peer observed by Auto-Router;
- `reported_ip`: any sender-supplied IP;
- `ip`: backward-compatible effective IP.

Reconciliation uses only `source_ip` for the signed-access-path comparison.
A sender-supplied IP cannot satisfy node-source matching.

The runtime sanitizer still:

- accepts only `fleet-runtime-observation.v1`;
- bounds runtime/model counts and string lengths;
- rejects credential-bearing URLs;
- strips unknown artifact/token fields;
- forces `admitted: false`;
- forces an empty model set to `ready: false`.

## Deterministic acceptance fixtures

The minimum exact-head acceptance matrix is:

| Fixture | Required result |
| --- | --- |
| Fresh K2 on signed node/port/kind with expected model | `projected` |
| Ternary Bonsai on a genuinely unsigned port | `unprojected_runtime` |
| Extra observed model | `model_drift` |
| Missing projected model | `model_drift` |
| Duplicate signed physical match | `ambiguous_projection_match` |
| Same signed node/port, different runtime kind | `runtime_identity_mismatch` |
| Source IP contradicts signed literal IP paths | `runtime_identity_mismatch` |
| Source IP is missing/unverifiable | `runtime_identity_unverified` |
| Empty/unready model list | `runtime_not_ready` |
| Old receipt/observation | `stale_observation` |
| Truncated model evidence | `incomplete_observation` |
| Observation claims `admitted:true` | ignored |

All results must retain:

```json
{
  "mutating": false,
  "admission_authority": false
}
```

## Remaining deliberate gap

This slice does not attempt to hash multi-gigabyte model weights on every fleet
report. The next artifact-identity step should consume an already-produced,
operator-verifiable loadout/canary fingerprint or another bounded runtime witness
and compare it with the signed `ModelArtifact.artifact_fingerprint`.

Until that exists, a `projected` reconciliation result must not be described as
proof of artifact equivalence.
