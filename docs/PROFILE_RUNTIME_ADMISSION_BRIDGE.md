# Profile-to-runtime admission bridge

`fleet-llm-profiles` deliberately emits non-admitting profiles. AssistX remains the authority that issues a short-lived runtime projection lease and auto-router remains the enforcement point.

The bridge command converts an attested, canary-qualified profile into the existing `approve-runtime-projection.py` manifest format without mutating Neo4j or enabling the profile itself.

## Safety contract

The builder fails closed unless all of the following are true:

- `admission.enabled` is exactly `false`;
- all external runtime gates are declared;
- the source observation has not expired;
- the signed runtime canary succeeded;
- the soak and rollback gates passed;
- canary evidence remains explicitly non-admitting;
- runtime and model identities are resolved;
- capacity is positive;
- the generation advances exactly by one;
- LAN is preferred and Tailscale is available as fallback by default.

The generated document includes a canonical source-profile fingerprint and the canary signer, signing-key, manifest, and attestation fingerprints. It is only an approval candidate. Admission still requires the existing compare-and-swap apply command.

## Build a candidate

```bash
python scripts/build-runtime-projection-candidate.py \
  /path/to/fleet-runtime-profile.json \
  --generation 8 \
  --expected-current-generation 7 \
  --approved-by scott \
  --approval-id change-2026-08-05-x1-370 \
  --process-id 4242 \
  --runtime-version b6000 \
  --provider-model exact-server-model-id \
  --output /tmp/runtime-projection-generation-8.yaml
```

Use `--capability` repeatedly to replace the default `chat`, `streaming`, and `local_only` capability set. `local_only` is always retained.

`--allow-non-dual-path` exists for isolated development and recovery cases. It should not be used for normal production admission.

## Validate and apply

Dry-run the existing approval path first:

```bash
python scripts/approve-runtime-projection.py \
  /tmp/runtime-projection-generation-8.yaml \
  --dry-run
```

Apply only after the live process ID, runtime version, exact provider model, access paths, and evidence references have been reviewed:

```bash
python scripts/approve-runtime-projection.py \
  /tmp/runtime-projection-generation-8.yaml
```

The existing apply transaction retires prior approvals, advances the canonical generation with a compare-and-swap fence, writes the approved runtime/model/path/capacity records, and bounds them by the manifest TTL. AssistX then signs the projection consumed by auto-router. Expiry or a stale generation fails closed before provider dispatch.

## Boundary

This bridge does not contact a node, start or stop a runtime, load or unload a model, grant admission by itself, or prove a physical rollout. Those operations remain part of the signed physical observation, qualification, canary, and operator-approval sequence.
