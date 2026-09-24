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

- auto-router: `dcf1da72684bdda1999e4445708b24cd52bc96db`
- lms: `c9ffcc2240191a656221ed45a39a597c76990d3e`

The Auto-Assist head is recorded by the cross-repository workflow as
`GITHUB_SHA` in the uploaded repository matrix.

## Signed artifact/process witness

For a runtime that should prove artifact identity, create one witness **after the
approved runtime process is live**. This hashes the model file once, proves that
the live process references that file, binds the result to the already-signed
runtime-canary/loadout evidence, and signs the compact witness with OpenSSH.

Example:

```bash
lms-runtime-witness \
  --loadout /path/to/exact-loadout.json \
  --canary-run-dir /path/to/signed-canary-run \
  --allowed-signers /path/to/allowed_signers \
  --canary-identity runtime-canary-operator \
  --pid "$RUNTIME_PID" \
  --runtime-url http://localhost:1235 \
  --runtime-kind llama_cpp \
  --provider-model k2-36b \
  --model-path /path/to/k2.gguf \
  --signing-key /path/to/operator-ed25519-key \
  --out /var/lib/lms/runtime-witnesses/k2.json
```

The builder rejects the witness unless:

- the runtime-canary attestation verifies and completed with successful rollback;
- the canary `loadout_fingerprint` equals the exact loadout;
- a one-time SHA-256 of the live model file equals
  `loadout.model.content_sha256`;
- the selected process currently references that exact model path through its
  command line or `/proc/<pid>/maps`;
- the signing key is a private operator key with safe permissions.

The signed witness binds:

- node/runtime endpoint and declared runtime kind;
- provider model ID;
- exact model-content SHA-256 and loadout fingerprint;
- boot ID, PID, process start ticks and executable SHA-256;
- model-file device/inode/size/mtime identity;
- successful canary provenance;
- `admission: {"admitted": false}`.

Configure the node reporter with the witness:

```bash
python fleet_node_reporter.py \
  --runtime-url http://localhost:1235 \
  --runtime-witness /var/lib/lms/runtime-witnesses/k2.json
```

The detached signature must remain beside the JSON as `k2.json.sig`.
`FLEET_RUNTIME_WITNESSES` may be used instead of repeated CLI flags.

The reporter does **not** hash the model again on every report. It cheaply
revalidates that the same boot/PID/start identity is alive, the model file still
has the signed device/inode/size/mtime identity, and the process still references
that file. A restart, replacement, file mutation, or process/model unbinding makes
continuity fail closed.

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
  --runtime-witness-allowed-signers /path/to/allowed_signers \
  --runtime-witness-identity runtime-witness-operator \
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

The output also records SHA-256 hashes of all input files, including the
runtime-witness allowed-signers file. Witness signatures are verified with the
`lms-runtime-identity-witness` OpenSSH namespace before any witness field is
trusted.

## Classification contract

### `projected`

Exactly one signed provider matches node + serving port + runtime kind, the
observation is fresh and ready, evidence was not truncated, the transport-observed
source IP positively matches one of the signed provider's literal IP access paths,
and every projected model group is represented by either its alias or provider-model
ID with no unexpected observed models.

Without a valid signed runtime witness, this means **endpoint and model-name
agreement only** and `artifact_identity_verified` remains false.

With a valid witness, reconciliation additionally requires:

- the witness signature to verify against the operator allowed-signers file;
- witness node, port, runtime kind and provider model to match the observation;
- the signed canary to have verified rollback provenance;
- live process continuity to match the signed boot/PID/start/executable identity;
- the same model file identity to remain bound to the process;
- `witness.model_content_sha256` to equal the matching signed projection
  `artifact_fingerprint`.

Only then does the report emit:

```json
{
  "artifact_identity_verified": true,
  "artifact_identity_reason": "signed_model_artifact_and_process_match",
  "identity_evidence_level": "signed_model_artifact_and_process"
}
```

A verified witness may also refine a generic `openai_compatible` observation to
its operator-signed runtime kind (for example `llama_cpp`), but it still cannot
grant admission or create a provider.

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
- preserves only a bounded signed runtime-witness envelope plus live continuity
  facts;
- never interprets that witness as routing/admission state;
- forces `admitted: false`;
- forces an empty model set to `ready: false`.

## Deterministic acceptance fixtures

The minimum exact-head acceptance matrix is:

| Fixture | Required result |
| --- | --- |
| Fresh K2 on signed node/port/kind with expected model, no witness | `projected`, artifact unverified |
| Fresh K2 + valid signed witness + live model/process continuity | `projected`, artifact verified |
| Signed witness artifact hash differs from projection | `model_drift` |
| Signed witness process/model continuity fails | `runtime_identity_mismatch` |
| Runtime witness signature does not verify | `runtime_identity_unverified` |
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

## Scope boundary

This slice deliberately avoids repeated multi-gigabyte model hashing. The model is
hashed once when the operator issues the witness. Continued validity is then tied
to the exact live process plus stable file identity and process→model binding.

A plain `projected` result without `artifact_identity_verified: true` is still
only endpoint/model-name agreement. Only the signed-witness path described above
is evidence of the projected model artifact bytes for that continuing process.

The witness remains evidence, not authority: changing or producing a witness does
not change AssistX admission, Auto-Router providers, capacity, approved access
paths, routing policy, or projection generation.
