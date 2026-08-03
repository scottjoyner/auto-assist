# Implementer Handoff: Degraded Recovery Control Plane

This runbook is the executable handoff for the July 31 degraded recovery checkpoint. It is written for an implementer who has no prior architectural context.

The instructions are intentionally repetitive about safety boundaries. Do not infer authority from a service being healthy. Do not skip a gate because a later gate appears to work.

## 1. Immutable inputs

Use these exact source revisions until a later revision receives a separate attestation:

| Repository | Frozen branch | Exact SHA | Existing attestation |
|---|---|---|---|
| `scottjoyner/auto-assist` | `resilient-control-plane-20260731-attested` | `74a9f9c386de93e99e7c3f8488db868a31be6db6` | Actions run `30643920630` |
| `scottjoyner/fleet-resilience` | `resilient-control-plane-20260731-attested` | `f59002a5d91f89e670fe4cd0fe08f08703b08fe2` | Actions run `30642695246` |

The attestation applies only to those two SHAs. Any merge conflict resolution, configuration change, image rebuild, dependency change, or commit after those SHAs creates a new candidate that must be retested.

## 2. Non-negotiable architecture

The implementer must preserve all of the following:

1. Neo4j is the only durable authority for fleet identity, approvals, configuration, final task outcomes, and audit state.
2. FalkorDB is temporary operational state only. Its records are TTL-bounded and reconstructable.
3. Redis is queue transport only and uses `noeviction`.
4. Auto-router is the only inference gateway exposed to AssistX or Hermes.
5. Hermes remains an executor. It must not become a fleet registry, scheduler, model loader, or durable authority.
6. Warm standby is not active leadership. Healthy containers do not authorize claims or routing.
7. A separately signed activation envelope and a valid fence proof are required before degraded coordination writes are accepted.
8. No public inference endpoint is permitted.
9. The degraded controller may delegate only to already-approved private LM Studio-compatible endpoints with a fresh heartbeat and available headroom.
10. Outcomes produced while Neo4j is unavailable remain `PENDING_DURABLE_COMMIT` until journal replay succeeds.
11. Autonomous SSH deployment is out of scope.
12. Production activation is out of scope until an isolated physical rehearsal is complete and reviewed.

Stop immediately if an integration changes any of these statements.

## 3. Required roles

Assign people before running commands. One person may hold more than one role during an isolated rehearsal, but every action must still be recorded under the role that authorized it.

| Role | Responsibility | May not do |
|---|---|---|
| Integration implementer | Merge the attested SHAs, resolve conflicts, run tests, produce candidate SHAs | Authorize degraded activation |
| Appliance implementer | Prepare the Beelink, rootless Docker, files, services, and offline images | Invent or sign a fence proof |
| Primary operator | Prepare signed runtime projection access and Neo4j backup artifacts | Activate the recovery island alone |
| Witness or break-glass approver | Confirm exclusive control and issue the fence proof | Modify code or evidence after approval |
| Validation reviewer | Review evidence, test output, resource use, and rollback proof | Approve their own unreviewed exception |

Record assignments in the evidence directory:

```bash
cat > "$EVIDENCE_ROOT/roles.txt" <<'EOF'
change_id=<CHANGE-ID>
integration_implementer=<NAME>
appliance_implementer=<NAME>
primary_operator=<NAME>
witness_or_break_glass_approver=<NAME>
validation_reviewer=<NAME>
EOF
```

Do not continue with blank values.

## 4. Create an evidence workspace

Run this on the integration workstation before modifying either repository:

```bash
set -euo pipefail
umask 077

export CHANGE_ID="recovery-rehearsal-$(date -u +%Y%m%dT%H%M%SZ)"
export WORK_ROOT="$HOME/assistx-recovery-work/$CHANGE_ID"
export EVIDENCE_ROOT="$WORK_ROOT/evidence"
mkdir -p "$WORK_ROOT" "$EVIDENCE_ROOT"

printf '%s\n' "$CHANGE_ID" > "$EVIDENCE_ROOT/change-id.txt"
date -u +%Y-%m-%dT%H:%M:%SZ > "$EVIDENCE_ROOT/started-at.txt"
uname -a > "$EVIDENCE_ROOT/integration-host-uname.txt"
git --version > "$EVIDENCE_ROOT/git-version.txt"
python3 --version > "$EVIDENCE_ROOT/python-version.txt"
```

Evidence rules:

- Save command output, checksums, service status, HTTP status codes, and test reports.
- Never save passwords, HMAC secrets, activation secrets, tokens, private keys, or complete environment files in evidence.
- Hash sensitive configuration files instead of copying them.
- Use UTC timestamps.
- Never edit evidence after signing or hashing it. Add a superseding file instead.

## 5. Integrate the exact Auto-assist revision

### 5.1 Clone and verify the source object

```bash
cd "$WORK_ROOT"
git clone git@github.com:scottjoyner/auto-assist.git
cd auto-assist

export AUTO_ASSIST_ATTESTED_SHA=74a9f9c386de93e99e7c3f8488db868a31be6db6
git fetch --prune origin

git cat-file -e "${AUTO_ASSIST_ATTESTED_SHA}^{commit}"
test "$(git rev-parse "$AUTO_ASSIST_ATTESTED_SHA")" = "$AUTO_ASSIST_ATTESTED_SHA"
git show --no-patch --format=fuller "$AUTO_ASSIST_ATTESTED_SHA" \
  | tee "$EVIDENCE_ROOT/auto-assist-attested-commit.txt"
```

Pass condition: `git cat-file` exits zero and `git rev-parse` prints the exact 40-character SHA.

Stop condition: Git cannot resolve the object or the SHA differs.

### 5.2 Create the integration branch

Set `TARGET_BASE` to the branch the other agent wants to integrate onto. Do not use a dirty working tree.

```bash
export TARGET_BASE=origin/<NEWER-INTEGRATION-BRANCH>
git status --porcelain | tee "$EVIDENCE_ROOT/auto-assist-premerge-status.txt"
test ! -s "$EVIDENCE_ROOT/auto-assist-premerge-status.txt"

git switch --create "integrate/$CHANGE_ID-auto-assist" "$TARGET_BASE"
export AUTO_ASSIST_BASE_SHA=$(git rev-parse HEAD)
printf '%s\n' "$AUTO_ASSIST_BASE_SHA" > "$EVIDENCE_ROOT/auto-assist-base-sha.txt"

git merge --no-ff --no-commit "$AUTO_ASSIST_ATTESTED_SHA"
```

If there are no conflicts, inspect the staged merge before committing:

```bash
git status --short | tee "$EVIDENCE_ROOT/auto-assist-merge-status.txt"
git diff --cached --check
git diff --cached --stat | tee "$EVIDENCE_ROOT/auto-assist-merge-stat.txt"
```

Then commit:

```bash
git commit -m "Integrate attested degraded recovery control plane"
export AUTO_ASSIST_INTEGRATION_SHA=$(git rev-parse HEAD)
printf '%s\n' "$AUTO_ASSIST_INTEGRATION_SHA" \
  > "$EVIDENCE_ROOT/auto-assist-integration-sha.txt"
```

### 5.3 Conflict policy

Do not resolve conflicts by selecting all of one side.

For every conflict:

1. Save the conflict list:

   ```bash
   git diff --name-only --diff-filter=U \
     | tee "$EVIDENCE_ROOT/auto-assist-conflicts.txt"
   ```

2. Classify the file:

   - **Recovery safety path:** preserve the July 31 behavior unless the newer branch has a demonstrably stronger equivalent.
   - **Newer unrelated feature:** preserve the newer branch and add only the recovery hook required by the attested change.
   - **Shared API or schema:** manually reconcile both behaviors and add or update tests.

3. Recovery safety paths include at minimum:

   ```text
   src/assistx/operational_state.py
   src/assistx/operational_journal.py
   src/assistx/degraded_control_plane.py
   src/assistx/degraded_activation.py
   src/assistx/degraded_control_hardening.py
   src/assistx/degraded_router_gate.py
   src/assistx/recovery_snapshot.py
   src/assistx/recovery_memory_guard.py
   src/assistx/neo4j_backup_verification.py
   src/assistx/api_router.py
   scripts/verify-neo4j-backup.py
   scripts/replicate-recovery-snapshot.py
   scripts/recovery-memory-guard.py
   tests/test_degraded_disaster_canary.py
   ```

4. For each resolved conflict, add a row to `conflict-decisions.tsv`:

   ```text
   path<TAB>newer_behavior_preserved<TAB>attested_behavior_preserved<TAB>test_that_proves_resolution<TAB>implementer
   ```

5. Never delete the degraded route fence, activation fence, private-path validation, journal replay, heartbeat requirement, or emergency memory gate to make a conflict easier.

6. After all resolutions:

   ```bash
   git diff --check
   git status --short
   git commit
   ```

Any conflict resolution invalidates the original attestation. The resulting integration SHA is a new candidate and must pass every test in Section 6.

## 6. Validate Auto-assist integration

Run from the Auto-assist repository root.

### 6.1 Create an isolated Python environment

```bash
python3 -m venv .venv-recovery-validation
. .venv-recovery-validation/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip freeze --all > "$EVIDENCE_ROOT/auto-assist-python-freeze.txt"
```

### 6.2 Compile and lint

```bash
python -m py_compile $(find src tests scripts -name '*.py' -type f) \
  2>&1 | tee "$EVIDENCE_ROOT/auto-assist-pycompile.log"
ruff check src tests scripts \
  2>&1 | tee "$EVIDENCE_ROOT/auto-assist-ruff.log"
```

If the repository intentionally carries legacy Ruff exceptions, run the exact blocking command from `.github/workflows/ci.yml` instead of weakening it locally. Record the command used.

### 6.3 Run the complete non-integration suite

```bash
export BASIC_AUTH_USER=neo4j
export BASIC_AUTH_PASS=livelongandprosper
pytest -q -m 'not integration' --ignore=tests/integration \
  --junitxml="$EVIDENCE_ROOT/auto-assist-unit.xml" \
  2>&1 | tee "$EVIDENCE_ROOT/auto-assist-unit.log"
```

Expected result: zero failures.

### 6.4 Run the original recovery lifecycle canary

```bash
pytest -q \
  tests/test_recovery_canary.py \
  tests/test_recovery_island.py \
  tests/test_recovery_island_hardening.py \
  --junitxml="$EVIDENCE_ROOT/auto-assist-recovery-canary.xml" \
  2>&1 | tee "$EVIDENCE_ROOT/auto-assist-recovery-canary.log"
```

Expected result: zero failures.

### 6.5 Run the degraded disaster canary

```bash
pytest -q \
  tests/test_operational_state.py \
  tests/test_operational_journal.py \
  tests/test_degraded_control_plane.py \
  tests/test_degraded_activation.py \
  tests/test_degraded_disaster_canary.py \
  tests/test_recovery_snapshot.py \
  tests/test_recovery_memory_guard.py \
  tests/test_neo4j_backup_verification.py \
  --junitxml="$EVIDENCE_ROOT/auto-assist-degraded-canary.xml" \
  2>&1 | tee "$EVIDENCE_ROOT/auto-assist-degraded-canary.log"
```

Expected result: zero failures. This canary proves the following sequence:

```text
signed projection cached
-> standby remains locked
-> signed fenced activation
-> fresh LM Studio heartbeat
-> bounded delegation
-> pending durable finalization
-> memory emergency block
-> Neo4j replay exactly once
-> degraded leadership relinquished
```

### 6.6 Record source and test checksums

```bash
git status --short | tee "$EVIDENCE_ROOT/auto-assist-posttest-status.txt"
git rev-parse HEAD > "$EVIDENCE_ROOT/auto-assist-tested-sha.txt"
sha256sum \
  "$EVIDENCE_ROOT"/auto-assist-*.log \
  "$EVIDENCE_ROOT"/auto-assist-*.xml \
  > "$EVIDENCE_ROOT/auto-assist-test-checksums.txt"
```

Pass condition: all test commands exit zero and the tested SHA equals the integration SHA.

Stop condition: any failure, dirty source file, or mismatch between tested and proposed SHA.

## 7. Integrate and validate Fleet-resilience

Follow the same source-verification pattern in a separate checkout:

```bash
cd "$WORK_ROOT"
git clone git@github.com:scottjoyner/fleet-resilience.git
cd fleet-resilience

export FLEET_RESILIENCE_ATTESTED_SHA=f59002a5d91f89e670fe4cd0fe08f08703b08fe2
git fetch --prune origin
git cat-file -e "${FLEET_RESILIENCE_ATTESTED_SHA}^{commit}"
test "$(git rev-parse "$FLEET_RESILIENCE_ATTESTED_SHA")" = \
  "$FLEET_RESILIENCE_ATTESTED_SHA"

git show --no-patch --format=fuller "$FLEET_RESILIENCE_ATTESTED_SHA" \
  | tee "$EVIDENCE_ROOT/fleet-resilience-attested-commit.txt"

export TARGET_BASE=origin/<NEWER-FLEET-RESILIENCE-BRANCH>
git switch --create "integrate/$CHANGE_ID-fleet-resilience" "$TARGET_BASE"
export FLEET_RESILIENCE_BASE_SHA=$(git rev-parse HEAD)
git merge --no-ff "$FLEET_RESILIENCE_ATTESTED_SHA"
export FLEET_RESILIENCE_INTEGRATION_SHA=$(git rev-parse HEAD)
printf '%s\n' "$FLEET_RESILIENCE_INTEGRATION_SHA" \
  > "$EVIDENCE_ROOT/fleet-resilience-integration-sha.txt"
```

Preserve these appliance invariants during conflict resolution:

- degraded Compose profile contains FalkorDB, separate Redis, degraded AssistX, and degraded auto-router;
- no degraded worker or Hermes service;
- APIs bind to loopback;
- images are digest-pinned and loaded offline;
- installer validates the recovery UID and rootless Docker socket;
- warm standby starts without activating degraded leadership;
- snapshot and memory-guard timers remain separate;
- Neo4j shadow, worker, and Hermes remain separate promotion tiers;
- no secret is embedded in `ExecStart`.

Validate:

```bash
python3 -m json.tool recovery-island/deployments.example.json >/dev/null
python3 -m json.tool recovery-island/runbook-verify-keys.example.json >/dev/null
python3 -m json.tool recovery-island/activation-verify-keys.example.json >/dev/null
bash -n recovery-island/install.sh

cd recovery-island
cp recovery-stack.env.example recovery-stack.env

docker compose \
  --env-file recovery-island.env.example \
  --profile degraded \
  -f compose.degraded.yml config \
  > "$EVIDENCE_ROOT/compose-degraded.rendered.yml"

docker compose \
  --env-file recovery-island.env.example \
  --profile shadow \
  -f compose.recovery.yml config \
  > "$EVIDENCE_ROOT/compose-shadow.rendered.yml"

docker compose \
  --env-file recovery-island.env.example \
  --profile promoted \
  -f compose.recovery.yml \
  -f compose.promoted.yml config \
  > "$EVIDENCE_ROOT/compose-promoted.rendered.yml"

docker compose \
  --env-file recovery-island.env.example \
  --profile executor \
  -f compose.recovery.yml \
  -f compose.promoted.yml config \
  > "$EVIDENCE_ROOT/compose-executor.rendered.yml"
```

Run the repository workflow locally where supported, or push the candidate branch and require the `recovery-island` Actions workflow to pass. Save the run URL and run ID.

## 8. Prepare immutable images and the offline bundle

Do this on an approved build host, not on the Beelink.

### 8.1 Build or obtain all required images

Required logical images:

```text
falkordb/falkordb-server
neo4j
redis
assistx
auto-router
```

Each value in `recovery-island.env` must use an immutable `repository@sha256:<digest>` reference. A mutable tag such as `latest`, `main`, or `2026-07` is not acceptable by itself.

Record image identities without exposing registry credentials:

```bash
docker image inspect \
  --format '{{.Id}} {{join .RepoDigests " "}}' \
  <IMAGE-REFERENCE> \
  | tee -a "$EVIDENCE_ROOT/image-identities.txt"
```

Stop if any required image lacks the expected digest.

### 8.2 Create the offline bundle

```bash
mkdir -p "$WORK_ROOT/bundle"

docker save \
  <FALKORDB-DIGEST-REFERENCE> \
  <NEO4J-DIGEST-REFERENCE> \
  <REDIS-DIGEST-REFERENCE> \
  <ASSISTX-DIGEST-REFERENCE> \
  <AUTO-ROUTER-DIGEST-REFERENCE> \
  -o "$WORK_ROOT/bundle/recovery-images.tar"

sha256sum "$WORK_ROOT/bundle/recovery-images.tar" \
  | tee "$EVIDENCE_ROOT/recovery-images.tar.sha256"
```

Set `RECOVERY_BUNDLE_SHA256` to the hash only after a second operator independently recomputes it:

```bash
sha256sum -c "$EVIDENCE_ROOT/recovery-images.tar.sha256"
```

Pass condition: `OK`.

## 9. Prepare secrets and credentials

Create separate values for each purpose. Never reuse a value across rows.

| Secret or credential | Used by | Reuse prohibited with |
|---|---|---|
| Node-specific Basic user/password | Beelink host agent to primary AssistX | Neo4j, FalkorDB, snapshot target |
| Node token | Protected recovery task lane | Any Basic password |
| Runtime projection HMAC secret | Primary projection signer and warm replica verifier | Activation or runbook signing |
| Runbook signing key | Reviewed stage/verify/activate/deactivate runbooks | Activation signing |
| Activation signing key | Separate takeover authorization | Runbook signing |
| FalkorDB password | Operational graph only | Redis or Neo4j |
| Neo4j recovery password | Isolated restored database | Primary Neo4j |
| Snapshot source read account | Read signed projection from healthy primary | Admin or task execution |
| Snapshot target account | Publish signed snapshot to warm degraded API | Primary operator credentials |

Generate secrets with restrictive permissions and no shell tracing:

```bash
set +x
umask 077
mkdir -p "$WORK_ROOT/secrets"
openssl rand -hex 32 > "$WORK_ROOT/secrets/runtime-projection-hmac"
openssl rand -hex 32 > "$WORK_ROOT/secrets/runbook-signing-key"
openssl rand -hex 32 > "$WORK_ROOT/secrets/activation-signing-key"
openssl rand -hex 32 > "$WORK_ROOT/secrets/falkordb-password"
openssl rand -hex 32 > "$WORK_ROOT/secrets/neo4j-recovery-password"
chmod 0600 "$WORK_ROOT"/secrets/*
```

The private signing keys stay with the signer or witness. The Beelink receives verification-key mappings only where the current HMAC design requires the shared verifier secret. Protect those files as signing-capable material and restrict them to the recovery account.

## 10. Prepare the Beelink without activating it

The companion appliance procedure is in:

```text
fleet-resilience/recovery-island/IMPLEMENTER_RUNBOOK_20260731.md
```

At the end of appliance installation, all of the following must be true:

- `assistx-degraded-warm.service` is active;
- snapshot and memory-guard timers are active;
- recovery host agent is active;
- Neo4j shadow, worker, and Hermes are stopped;
- the degraded API is reachable only on the configured loopback port;
- a signed projection can be published;
- the runtime projection cannot be read before activation;
- claims and delegations return HTTP `423` before activation;
- the router has zero usable capacity before activation.

## 11. Replicate the signed runtime projection

On the Beelink, run the same command used by the systemd timer once manually:

```bash
sudo -u assistx-recovery \
  env $(grep -v '^#' /srv/assistx-recovery/deployment/recovery-island.env | xargs) \
  /srv/assistx-recovery/venv/bin/python \
  /srv/assistx-recovery/packages/auto-assist/scripts/replicate-recovery-snapshot.py \
  | tee "$EVIDENCE_ROOT/snapshot-replication.json"
```

Safer alternative: invoke the systemd unit so credentials are read from its protected environment file and never expanded into the process command line:

```bash
sudo systemctl start assistx-recovery-snapshot.service
	sudo systemctl status --no-pager assistx-recovery-snapshot.service \
  | tee "$EVIDENCE_ROOT/snapshot-service-status.txt"
sudo journalctl -u assistx-recovery-snapshot.service -n 100 --no-pager \
  | tee "$EVIDENCE_ROOT/snapshot-service-journal.txt"
```

Use the systemd alternative in real environments.

Verify the snapshot file without displaying its signature or provider credentials:

```bash
sudo -u assistx-recovery test -s \
  /var/lib/assistx-recovery/state/runtime-projection.json
sudo -u assistx-recovery sha256sum \
  /var/lib/assistx-recovery/state/runtime-projection.json \
  | tee "$EVIDENCE_ROOT/runtime-projection-file.sha256"
```

## 12. Prove warm standby is fenced

Set local variables without saving the password in shell history:

```bash
read -r -p 'Recovery API user: ' RECOVERY_API_USER
read -r -s -p 'Recovery API password: ' RECOVERY_API_PASS
echo
export RECOVERY_API=http://127.0.0.1:27900
```

### 12.1 Status must be readable

```bash
curl --fail --silent --show-error \
  -u "$RECOVERY_API_USER:$RECOVERY_API_PASS" \
  "$RECOVERY_API/api/degraded/status" \
  | tee "$EVIDENCE_ROOT/warm-status.json"
```

### 12.2 Projection read must be locked

```bash
HTTP_CODE=$(curl --silent --show-error \
  -o "$EVIDENCE_ROOT/preactivation-projection-response.json" \
  -w '%{http_code}' \
  -u "$RECOVERY_API_USER:$RECOVERY_API_PASS" \
  "$RECOVERY_API/api/degraded/runtime-projection")
test "$HTTP_CODE" = 423
printf '%s\n' "$HTTP_CODE" \
  > "$EVIDENCE_ROOT/preactivation-projection-http-code.txt"
```

### 12.3 Claim creation must be locked

```bash
HTTP_CODE=$(curl --silent --show-error \
  -o "$EVIDENCE_ROOT/preactivation-claim-response.json" \
  -w '%{http_code}' \
  -u "$RECOVERY_API_USER:$RECOVERY_API_PASS" \
  -H 'Content-Type: application/json' \
  -d '{"logical_id":"preactivation-proof","owner":"implementer","epoch":1}' \
  "$RECOVERY_API/api/degraded/claims")
test "$HTTP_CODE" = 423
printf '%s\n' "$HTTP_CODE" \
  > "$EVIDENCE_ROOT/preactivation-claim-http-code.txt"
```

Pass condition: both calls return exactly `423`.

Stop condition: either call returns `200`, the router exposes a model, or any task executes before activation.

## 13. Verify a Neo4j backup before takeover

Run this on a host with the matching Neo4j Enterprise administration tools and the isolated backup directory mounted read-only where practical:

```bash
cd "$WORK_ROOT/auto-assist"
. .venv-recovery-validation/bin/activate

python scripts/verify-neo4j-backup.py \
  <BACKUP-DIRECTORY> \
  --database neo4j \
  --max-age-seconds <APPROVED-RPO-SECONDS> \
  --require-recovered \
  | tee "$EVIDENCE_ROOT/neo4j-backup-verification.json"
```

For the isolated restore rehearsal, add `--consistency-check` only when enough disk and time are available:

```bash
python scripts/verify-neo4j-backup.py \
  <BACKUP-DIRECTORY> \
  --database neo4j \
  --max-age-seconds <APPROVED-RPO-SECONDS> \
  --require-recovered \
  --consistency-check \
  | tee "$EVIDENCE_ROOT/neo4j-backup-consistency.json"
```

Pass conditions:

- output contains `"ok": true`;
- chain starts with a full backup;
- no transaction gap exists;
- latest artifact is inside the approved RPO;
- database identity is consistent;
- consistency check passes when requested.

Do not activate degraded leadership merely because a backup exists. Backup validation and takeover fencing are separate gates.

## 14. Create a signed activation envelope

Activation requires an independently approved fence proof:

```text
witness:<exclusive-witness-lease>
```

or, for a documented total-loss rehearsal:

```text
manual-break-glass:<operator-change-id>
```

An implementer must not invent the witness value. The witness or break-glass approver supplies it after confirming the original primary cannot coordinate writes.

Create the unsigned fields:

```json
{
  "version": 1,
  "mode": "activate",
  "target_node_id": "beelink-recovery",
  "deployment": "assistx-degraded",
  "bundle_sha256": "<EXACT-64-CHARACTER-BUNDLE-SHA256>",
  "epoch": 1,
  "fence_proof": "witness:<EXCLUSIVE-WITNESS-LEASE>"
}
```

The epoch must be greater than every prior accepted activation epoch. Do not reuse an epoch or nonce.

Sign without placing the secret in command history:

```bash
export ACTIVATION_KEY_ID=activation-v1
export ACTIVATION_SECRET_FILE="$WORK_ROOT/secrets/activation-signing-key"
export ACTIVATION_OUTPUT="$WORK_ROOT/activation.json"
export RECOVERY_BUNDLE_SHA256=<EXACT-64-CHARACTER-BUNDLE-SHA256>
export RECOVERY_EPOCH=<MONOTONIC-EPOCH>
export RECOVERY_FENCE_PROOF='witness:<EXCLUSIVE-WITNESS-LEASE>'

PYTHONPATH="$WORK_ROOT/auto-assist/src" python3 - <<'PY'
import json
import os
from pathlib import Path

from assistx.recovery_island import sign_recovery_activation

secret = Path(os.environ["ACTIVATION_SECRET_FILE"]).read_text().strip()
unsigned = {
    "version": 1,
    "mode": "activate",
    "target_node_id": "beelink-recovery",
    "deployment": "assistx-degraded",
    "bundle_sha256": os.environ["RECOVERY_BUNDLE_SHA256"],
    "epoch": int(os.environ["RECOVERY_EPOCH"]),
    "fence_proof": os.environ["RECOVERY_FENCE_PROOF"],
}
signed = sign_recovery_activation(
    unsigned,
    key_id=os.environ["ACTIVATION_KEY_ID"],
    secret=secret,
    ttl_seconds=900,
)
Path(os.environ["ACTIVATION_OUTPUT"]).write_text(
    json.dumps(signed, indent=2, sort_keys=True) + "\n"
)
PY
chmod 0600 "$ACTIVATION_OUTPUT"
```

The activation expires. Generate it only when the operators are ready to execute the takeover gate.

## 15. Activate degraded coordination

Copy the signed envelope to the Beelink using the approved one-way transfer path. Do not give the Beelink a reusable SSH credential back to the primary.

Post the envelope:

```bash
HTTP_CODE=$(curl --silent --show-error \
  -o "$EVIDENCE_ROOT/activation-response.json" \
  -w '%{http_code}' \
  -u "$RECOVERY_API_USER:$RECOVERY_API_PASS" \
  -H 'Content-Type: application/json' \
  --data-binary @<(jq -n --slurpfile activation "$ACTIVATION_OUTPUT" \
    '{activation:$activation[0]}') \
  "$RECOVERY_API/api/degraded/activate")
test "$HTTP_CODE" = 200
printf '%s\n' "$HTTP_CODE" > "$EVIDENCE_ROOT/activation-http-code.txt"
```

Expected response includes:

```json
{
  "ok": true,
  "status": "DEGRADED_ACTIVE"
}
```

Stop and investigate if the response is not `200`. Do not modify the verifier, shorten validation, or reuse the same envelope after a replay rejection.

## 16. Prove runtime projection and heartbeat-qualified delegation

### 16.1 Projection becomes readable only after activation

```bash
curl --fail --silent --show-error \
  -u "$RECOVERY_API_USER:$RECOVERY_API_PASS" \
  "$RECOVERY_API/api/degraded/runtime-projection" \
  | tee "$EVIDENCE_ROOT/active-runtime-projection.json"
```

Review without exposing credentials:

- every provider is enabled only if approved;
- every URL is loopback, RFC1918/private LAN, approved internal DNS, `.ts.net`, or Tailscale `100.64.0.0/10`;
- public URLs are absent;
- LAN path precedes Tailscale fallback where both exist;
- provider model identity matches the approved artifact;
- parallel slots are bounded.

### 16.2 Send a fresh node heartbeat

Use a real approved surviving node ID and measured inflight count:

```bash
curl --fail --silent --show-error \
  -u "$RECOVERY_API_USER:$RECOVERY_API_PASS" \
  -H 'Content-Type: application/json' \
  -d '{
    "node_id":"<APPROVED-NODE-ID>",
    "inflight":0,
    "capabilities":["code"],
    "ttl_seconds":45
  }' \
  "$RECOVERY_API/api/degraded/heartbeats" \
  | tee "$EVIDENCE_ROOT/degraded-heartbeat.json"
```

### 16.3 Plan a bounded delegation

```bash
curl --fail --silent --show-error \
  -u "$RECOVERY_API_USER:$RECOVERY_API_PASS" \
  -H 'Content-Type: application/json' \
  -d '{
    "task_id":"rehearsal-delegation-1",
    "owner":"beelink-recovery",
    "epoch":<ACTIVE-EPOCH>,
    "required_capabilities":["code"]
  }' \
  "$RECOVERY_API/api/degraded/delegations/plan" \
  | tee "$EVIDENCE_ROOT/delegation-plan.json"
```

Pass conditions:

- selected node equals an approved projection provider;
- heartbeat is fresh;
- headroom is greater than zero;
- projection generation and checksum are recorded;
- no model is automatically loaded;
- no generic shell or repository action is exposed.

Negative proof: repeat with the heartbeat expired or inflight equal to slots. Delegation must fail.

## 17. Prove pending durable finalization

This is a rehearsal record, not a production task outcome:

```bash
curl --fail --silent --show-error \
  -u "$RECOVERY_API_USER:$RECOVERY_API_PASS" \
  -H 'Content-Type: application/json' \
  -d '{
    "operation_id":"rehearsal-finalization-1",
    "operation_kind":"task_outcome",
    "final_state":"COMPLETED",
    "record_checksum":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "epoch":<ACTIVE-EPOCH>,
    "evidence":{"artifact":"sha256:rehearsal-only"}
  }' \
  "$RECOVERY_API/api/degraded/finalizations" \
  | tee "$EVIDENCE_ROOT/pending-finalization.json"
```

Expected status:

```text
PENDING_DURABLE_COMMIT
```

Stop if the API reports durable completion while Neo4j is unavailable.

Verify the journal exists, is owned by the recovery account, and is not group/world-readable:

```bash
sudo stat -c '%U %G %a %n' \
  /var/lib/assistx-recovery/operation-journal/* \
  | tee "$EVIDENCE_ROOT/operation-journal-permissions.txt"
```

Do not copy journal payloads into broadly accessible evidence storage. Hash them:

```bash
sudo sha256sum /var/lib/assistx-recovery/operation-journal/* \
  | tee "$EVIDENCE_ROOT/operation-journal-checksums.txt"
```

## 18. Prove deterministic memory shedding

First observe without creating pressure:

```bash
sudo systemctl start assistx-recovery-memory-guard.service
sudo systemctl status --no-pager assistx-recovery-memory-guard.service \
  | tee "$EVIDENCE_ROOT/memory-guard-status-normal.txt"
sudo journalctl -u assistx-recovery-memory-guard.service -n 100 --no-pager \
  | tee "$EVIDENCE_ROOT/memory-guard-journal-normal.txt"
```

The reviewed sheddable list may contain only optional local-model or UI user services, for example:

```text
lmstudio-headless.service
```

The guard must refuse to stop:

```text
assistx-recovery-island.service
assistx-degraded-warm.service
falkordb
redis
neo4j
docker.service
networking.service
NetworkManager.service
tailscaled.service
ssh.service
```

For a physical pressure rehearsal, use a controlled memory workload with a hard timeout and an operator at the console. Do not run a memory pressure generator on production. Record:

- available memory before and after;
- which exact service was stopped;
- service state after recovery;
- whether promotion and new-work block files were created;
- proof that FalkorDB, Redis, networking, Docker, and Tailscale stayed healthy.

Pass condition: only an explicitly reviewed sheddable unit stops.

Stop condition: the kernel OOM killer acts first, a protected service stops, or recovery networking is lost.

## 19. Restore Neo4j in the isolated shadow tier

Before starting the shadow tier:

1. Stop or shed the largest Beelink local model.
2. Confirm at least the approved memory headroom.
3. Verify the backup chain again on the Beelink or staging host.
4. Confirm the restore target is the Beelink-local recovery directory, not the primary database path.
5. Confirm the primary remains fenced from writes.

Use the fleet-resilience companion runbook for the exact Compose stage/activation flow. The shadow tier must remain inert:

- no worker;
- no loader;
- no poller;
- no Hermes;
- no background reconciliation loop that can mutate work;
- health, schema, projection, and operator-state endpoints only.

## 20. Reconcile and return leadership

Do not call primary-return reconciliation until:

- Neo4j shadow is healthy;
- database identity matches the verified backup chain;
- runtime projection converges;
- the journal verifies;
- the original primary or replacement primary has the approved write fence;
- no new degraded claims are being admitted;
- active degraded leases are drained or explicitly expired.

Request reconciliation with the active owner and epoch:

```bash
curl --fail --silent --show-error \
  -u "$RECOVERY_API_USER:$RECOVERY_API_PASS" \
  -H 'Content-Type: application/json' \
  -d '{
    "owner":"witness:<EXCLUSIVE-PRIMARY-RETURN-FENCE>",
    "epoch":<ACTIVE-EPOCH>
  }' \
  "$RECOVERY_API/api/degraded/primary-return/reconcile" \
  | tee "$EVIDENCE_ROOT/primary-return-reconcile.json"
```

Expected terminal state:

```text
RELINQUISHED
```

Expected replay result:

```text
remaining = 0
```

Run the same reconciliation request again to prove idempotency. It must not create a second durable completion.

After relinquishment:

1. Verify degraded claims are rejected.
2. Verify the primary serves the canonical runtime projection.
3. Verify journal pending count is zero.
4. Verify the durable Neo4j finalization exists exactly once.
5. Stop promoted worker or Hermes tiers if they were used only for rehearsal.
6. Preserve volumes and evidence until review completes.

## 21. Rollback procedures

### 21.1 Before activation

```bash
sudo systemctl stop assistx-recovery-snapshot.timer
sudo systemctl stop assistx-recovery-memory-guard.timer
sudo systemctl stop assistx-degraded-warm.service
```

No leadership handoff is required because degraded coordination never activated.

### 21.2 After activation but before Neo4j shadow

1. Stop new work at the client/router boundary.
2. Drain or expire active leases.
3. Preserve the operation journal.
4. Record the active epoch and fence proof identifier.
5. Stop the degraded router and API only after no request is in flight.
6. Do not delete FalkorDB or Redis volumes until the incident reviewer approves.

### 21.3 After shadow restore

1. Keep restored Neo4j isolated.
2. Stop worker and Hermes first.
3. Stop shadow API and router.
4. Preserve Neo4j volume, logs, journal, and restore evidence.
5. Never copy the recovery Neo4j directory over the primary database directory as a rollback shortcut.

## 22. Required final evidence

The handoff is complete only when the evidence package contains:

```text
roles.txt
change-id.txt
source commit records for both repositories
base and integration SHAs
conflict-decisions.tsv, when conflicts occurred
compile/lint/test logs and JUnit XML
rendered degraded/shadow/promoted/executor Compose plans
image identity list
bundle checksum and independent checksum verification
hashes of installed configuration files
systemd unit and timer status
warm standby 423 proofs
snapshot replication status and snapshot hash
backup-chain verification and consistency result
activation response and non-secret envelope metadata
active runtime projection review
fresh-heartbeat delegation proof
expired/saturated heartbeat rejection proof
pending durable finalization proof
journal permissions and hashes
memory guard normal and pressure evidence
isolated shadow restore health
journal replay and exactly-once durable result
RELINQUISHED primary-return state
rollback or deactivation proof
reviewer sign-off
```

Create a final manifest containing file hashes:

```bash
find "$EVIDENCE_ROOT" -type f -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  > "$EVIDENCE_ROOT/manifest.sha256"

sha256sum "$EVIDENCE_ROOT/manifest.sha256" \
  > "$EVIDENCE_ROOT/manifest.sha256.sha256"
```

The validation reviewer must recompute the manifest before sign-off.

## 23. Final go/no-go criteria

### Code integration may proceed when

- exact source SHAs were verified;
- every conflict has a written decision and test;
- full unit suite passes;
- both recovery canaries pass;
- appliance workflow passes;
- candidate SHAs and run IDs are recorded.

### Physical rehearsal may proceed when

- digest-pinned offline images are present;
- bundle checksum is independently verified;
- rootless Docker socket and recovery UID match;
- secrets are separate and permissions are correct;
- warm standby proves HTTP `423` before activation;
- signed projection replication succeeds;
- backup chain and RPO are valid;
- witness or break-glass approver is present.

### Production deployment remains NO-GO until

- the entire physical rehearsal completes;
- LAN failure successfully falls back to Tailscale without public routing;
- memory shedding stops only the reviewed local-model/UI unit;
- isolated Neo4j restore and consistency check pass;
- finalization journal replays exactly once;
- leadership returns to the primary with `remaining = 0`;
- rollback is rehearsed;
- evidence is independently reviewed;
- a separate production change is approved.

## 24. Handoff statement

The implementer must finish with a statement in this exact form:

```text
CHANGE ID: <value>
AUTO-ASSIST SOURCE SHA: <value>
AUTO-ASSIST INTEGRATION SHA: <value>
FLEET-RESILIENCE SOURCE SHA: <value>
FLEET-RESILIENCE INTEGRATION SHA: <value>
CI RUNS: <values>
CONFLICTS: <none or evidence path>
WARM STANDBY FENCE: PASS|FAIL
SIGNED SNAPSHOT REPLICATION: PASS|FAIL
BACKUP VERIFICATION: PASS|FAIL
ACTIVATION REHEARSAL: PASS|FAIL|NOT RUN
DELEGATION REHEARSAL: PASS|FAIL|NOT RUN
MEMORY SHEDDING: PASS|FAIL|NOT RUN
NEO4J RESTORE: PASS|FAIL|NOT RUN
JOURNAL REPLAY: PASS|FAIL|NOT RUN
PRIMARY RETURN: PASS|FAIL|NOT RUN
ROLLBACK: PASS|FAIL|NOT RUN
PRODUCTION CHANGED: NO
REVIEWER: <name>
EVIDENCE MANIFEST SHA256: <value>
```

Any `FAIL` blocks the next tier. `NOT RUN` is acceptable only for a later physical or production tier and must not be represented as passed.