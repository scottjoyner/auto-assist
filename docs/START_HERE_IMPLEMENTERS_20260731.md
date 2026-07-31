# Start Here: July 31 Recovery Implementers

This is the required entrypoint. It defines the order of work and the command-safety policy. The detailed procedures are:

1. `docs/IMPLEMENTER_HANDOFF_20260731.md` in Auto-assist.
2. `recovery-island/IMPLEMENTER_RUNBOOK_20260731.md` in Fleet-resilience.
3. `deploy/reconciliation/implementer-evidence.example.json` as the evidence record.
4. `scripts/validate-implementer-evidence.py` as the gate validator.

## Required execution order

Do not reorder these phases:

```text
1. Assign roles and change ID.
2. Create the evidence workspace.
3. Verify both frozen source SHAs.
4. Integrate onto the newer branches.
5. Resolve every conflict with a written decision and test.
6. Pass the complete Auto-assist suite and both recovery canaries.
7. Pass both Fleet-resilience workflows and render every Compose tier.
8. Build and independently verify the digest-pinned offline image bundle.
9. Install the Beelink in warm, zero-capacity mode.
10. Prove HTTP 423 for projection reads, claims, and delegation before activation.
11. Verify signed projection replication and the Neo4j backup chain.
12. Obtain an independent witness or documented break-glass fence.
13. Sign and hash a fresh activation envelope with a monotonic epoch.
14. Activate Tier 0 and prove heartbeat-qualified private delegation.
15. Prove finalizations remain PENDING_DURABLE_COMMIT without Neo4j.
16. Prove deterministic memory shedding.
17. Restore Neo4j in isolated shadow mode.
18. Replay the journal exactly once.
19. Return leadership and reach RELINQUISHED with remaining = 0.
20. Rehearse rollback and validate the evidence manifest.
```

A failure blocks the next numbered phase.

## Command-safety policy

### Never expand a secret environment file into a command line

Do not use commands shaped like:

```bash
env $(grep ... | xargs) <command>
```

They may expose passwords or HMAC material through process arguments or shell history. For snapshot replication, the only approved real-host invocation is:

```bash
sudo systemctl start assistx-recovery-snapshot.service
sudo systemctl status --no-pager assistx-recovery-snapshot.service
sudo journalctl -u assistx-recovery-snapshot.service -n 100 --no-pager
```

The systemd unit reads the protected environment file.

### Never put signing secrets in shell arguments

Read signing material from a mode-`0600` file inside a short-lived process. Do not use `--secret <value>`, command substitution, shell tracing, or evidence logs containing the secret.

### Never post activation with an implicit or hand-edited wrapper

Create the request body as a protected file, inspect its non-secret fields, hash it, then post it.

```bash
umask 077
export ACTIVATION_OUTPUT=<SIGNED-ACTIVATION-JSON>
export ACTIVATION_REQUEST=<PROTECTED-ACTIVATION-REQUEST-JSON>

python3 - <<'PY'
import json
import os
from pathlib import Path

activation = json.loads(Path(os.environ["ACTIVATION_OUTPUT"]).read_text())
Path(os.environ["ACTIVATION_REQUEST"]).write_text(
    json.dumps({"activation": activation}, indent=2, sort_keys=True) + "\n"
)
PY
chmod 0600 "$ACTIVATION_REQUEST"
sha256sum "$ACTIVATION_OUTPUT" "$ACTIVATION_REQUEST"

HTTP_CODE=$(curl --silent --show-error \
  -o "$EVIDENCE_ROOT/activation-response.json" \
  -w '%{http_code}' \
  -u "$RECOVERY_API_USER:$RECOVERY_API_PASS" \
  -H 'Content-Type: application/json' \
  --data-binary "@$ACTIVATION_REQUEST" \
  "$RECOVERY_API/api/degraded/activate")
test "$HTTP_CODE" = 200
```

### Never use a mutable image tag as deployment identity

Every configured image must be `repository@sha256:<digest>`, must be present in the verified offline tar bundle, and must load into the recovery account's rootless Docker daemon without a pull.

### Never infer leadership from health

These facts do not authorize coordination writes:

- containers are healthy;
- snapshot replication succeeded;
- router is listening;
- Neo4j backup exists;
- the primary appears unreachable.

Only the verified activation envelope plus the independent fence opens Tier 0 writes.

### Never promote two tiers at once

Promote in order:

```text
warm Tier 0
-> active Tier 0
-> Neo4j shadow
-> AssistX worker
-> Hermes executor
```

Each transition requires its own proof and stop condition.

## What implementers are authorized to change

During integration they may:

- resolve code and workflow conflicts;
- update tests to prove both newer and recovery behavior;
- populate environment placeholders with approved values;
- pin immutable image digests;
- install reviewed router configuration;
- create an isolated rehearsal evidence package.

They may not:

- weaken route or activation fences;
- add public providers;
- enable model autoloading;
- make FalkorDB a durable authority;
- mark pending outcomes complete without Neo4j;
- add autonomous SSH deployment;
- activate production;
- overwrite the primary Neo4j database from the recovery volume;
- report `NOT_RUN` as `PASS`.

## Completion commands

After source integration:

```bash
python scripts/validate-implementer-evidence.py \
  <COMPLETED-MANIFEST.json> \
  --stage integration
```

After the full physical rehearsal:

```bash
python scripts/validate-implementer-evidence.py \
  <COMPLETED-MANIFEST.json> \
  --stage rehearsal
```

A zero exit status is necessary but not sufficient. The named validation reviewer must also recompute the evidence manifest hash and sign the handoff statement.