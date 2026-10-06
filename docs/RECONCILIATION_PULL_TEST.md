# AssistX + auto-router reconciliation pull/test runbook

This runbook exercises everything that can be proven on one operator machine before a fleet cutover. It does **not** merge either PR, replace production containers, start Hermes automatically, or approve unresolved runtime identity.

## 1. Pull the exact draft branches

Start with clean working trees in both repositories.

```bash
cd ~/git/auto-assist
git fetch origin
git switch full-auto-reconciliation-20260730 || \
  git switch --track -c full-auto-reconciliation-20260730 \
  origin/full-auto-reconciliation-20260730
git pull --ff-only origin full-auto-reconciliation-20260730

cd ~/git/auto-router
git fetch origin
git switch full-auto-reconciliation-20260730 || \
  git switch --track -c full-auto-reconciliation-20260730 \
  origin/full-auto-reconciliation-20260730
git pull --ff-only origin full-auto-reconciliation-20260730
```

Record the exact revisions before testing:

```bash
printf 'auto-assist %s\n' "$(git -C ~/git/auto-assist rev-parse HEAD)"
printf 'auto-router %s\n' "$(git -C ~/git/auto-router rev-parse HEAD)"
```

## 2. Generate isolated credentials

Generate credentials outside either repository. Existing files are never overwritten and secret values are not printed.

```bash
SECRETS="$HOME/.config/assistx/reconciliation-20260730"
python ~/git/auto-assist/scripts/generate-reconciliation-secrets.py \
  --output-dir "$SECRETS"
```

The generator creates:

- a dedicated executor Ed25519 keypair;
- a distinct runtime-projection Ed25519 keypair;
- separate bootstrap, claim-status, internal-inference, and admin tokens;
- an environment file with mode `0600`;
- a fingerprint manifest containing no private key or token values.

Review the manifest and modes:

```bash
jq . "$SECRETS/reconciliation-secrets-manifest.json"
stat -c '%a %n' "$SECRETS"/* "$SECRETS"/.env.reconciliation.generated
```

Expected private-file mode is `600`; public keys are `644`; the directory is `700`.

Create a second file containing only machine/site-specific values. Do not copy token placeholders over the generated values.

```bash
cat > "$SECRETS/site.env" <<'EOF'
NEO4J_USER=neo4j
NEO4J_PASSWORD=replace-with-isolated-reconciliation-password
NEO4J_DATABASE=assistx
CORS_ALLOW_ORIGINS=http://127.0.0.1:18000
ASSISTX_API_PORT=18000
ASSISTX_NEO4J_HTTP_PORT=17474
ASSISTX_NEO4J_BOLT_PORT=17687
RECONCILIATION_ROUTER_PORT=18088
RECONCILIATION_RUNTIME_NODE_ID=x1-370
RECONCILIATION_RUNTIME_INSTANCE_ID=replace-with-observed-runtime-instance
RECONCILIATION_RUNTIME_KIND=lmstudio
RECONCILIATION_RUNTIME_VERSION=replace-with-observed-version
RECONCILIATION_RUNTIME_HEADLESS=false
RECONCILIATION_PARALLEL_SLOTS=1
RECONCILIATION_QUEUE_LIMIT=4
RECONCILIATION_QUEUE_TIMEOUT_SECONDS=30
RECONCILIATION_LAN_BASE_URL=http://replace-with-lan-ip:1234/v1
RECONCILIATION_LMSTUDIO_BASE_URL=http://host.docker.internal:1234/v1
RECONCILIATION_TAILSCALE_BASE_URL=http://replace-with-tailscale-ip:1234/v1
RECONCILIATION_MODEL_ID=replace-with-loaded-model-key
RECONCILIATION_MODEL_INSTANCE_ID=replace-with-observed-model-instance
RECONCILIATION_MODEL_ARTIFACT_FINGERPRINT=replace-with-sha256
RECONCILIATION_MODEL_QUANTIZATION=replace-with-quantization
RECONCILIATION_CONTEXT_WINDOW=32768
EOF
chmod 600 "$SECRETS/site.env"
```

Load both files into the shell for `curl` and validation commands. The site file is loaded second and should contain only intentional machine-specific overrides.

```bash
set -a
source "$SECRETS/.env.reconciliation.generated"
source "$SECRETS/site.env"
set +a
```

## 3. Run repository tests before containers

```bash
cd ~/git/auto-assist
python -m py_compile $(find src tests scripts -name '*.py' -type f)
pytest -q \
  tests/test_executor_claims.py \
  tests/test_executor_security.py \
  tests/test_strict_executor_adapter.py \
  tests/test_runtime_projection_v2.py \
  tests/test_reconciliation_secret_generator.py \
  tests/test_reconciliation_security_config.py

cd ~/git/auto-router
python -m py_compile $(find src tests scripts -name '*.py' -type f)
pytest -q \
  tests/test_executor_auth.py \
  tests/test_claim_fence.py \
  tests/test_request_idempotency.py \
  tests/test_stream_lifecycle.py \
  tests/test_runtime_projection_v2.py \
  tests/test_reconciliation_security_config.py
bash scripts/test_reconciliation.sh
```

## 4. Render both Compose plans without starting anything

Create the isolated shared network once:

```bash
docker network inspect assistx_reconciliation_shared >/dev/null 2>&1 || \
  docker network create assistx_reconciliation_shared
```

Render AssistX:

```bash
cd ~/git/auto-assist
mkdir -p artifacts/reconciliation-preflight
docker compose \
  --env-file "$SECRETS/.env.reconciliation.generated" \
  --env-file "$SECRETS/site.env" \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.canary.yml \
  -f compose.reconciliation.yml \
  --profile executor \
  config > artifacts/reconciliation-preflight/assistx.compose.yaml
```

Render auto-router:

```bash
cd ~/git/auto-router
mkdir -p artifacts-reconciliation
docker compose \
  --env-file "$SECRETS/.env.reconciliation.generated" \
  --env-file "$SECRETS/site.env" \
  -f docker-compose.yml \
  -f compose.reconciliation.yml \
  config > artifacts-reconciliation/router.compose.yaml
```

Review the rendered plans before continuing:

```bash
grep -nE 'secure_live|127\.0\.0\.1|executor_(public|private)|runtime_projection_(public|private)|ASSISTX_EXECUTOR_SERVICE_TOKEN' \
  ~/git/auto-assist/artifacts/reconciliation-preflight/assistx.compose.yaml \
  ~/git/auto-router/artifacts-reconciliation/router.compose.yaml

! grep -R 'RUNTIME_PROJECTION_HMAC_SECRET' \
  ~/git/auto-assist/artifacts/reconciliation-preflight/assistx.compose.yaml \
  ~/git/auto-router/artifacts-reconciliation/router.compose.yaml
```

The router host port must be loopback-only, its command must use `auto_router.secure_live:app`, executor and projection key files must be different, and no runtime-projection HMAC may remain.

## 5. Start authority services without Hermes

Start the isolated AssistX database, Redis, and API. Do not include the `executor` profile.

```bash
cd ~/git/auto-assist
docker compose \
  --env-file "$SECRETS/.env.reconciliation.generated" \
  --env-file "$SECRETS/site.env" \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.canary.yml \
  -f compose.reconciliation.yml \
  up -d neo4j redis api
```

Start the authenticated router and its Redis:

```bash
cd ~/git/auto-router
docker compose \
  --env-file "$SECRETS/.env.reconciliation.generated" \
  --env-file "$SECRETS/site.env" \
  -f docker-compose.yml \
  -f compose.reconciliation.yml \
  up -d redis llm-router
```

Inspect container state and logs:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker logs --tail 200 assistx-canary-api
docker logs --tail 200 auto-router-reconciliation
```

No Hermes container should be running.

## 6. Verify API, signing, and authentication boundaries

AssistX health and schema-v2 projection:

```bash
curl -fsS -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASS" \
  http://127.0.0.1:18000/health | jq .

curl -fsS -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASS" \
  http://127.0.0.1:18000/api/router/runtime-projection \
  | tee /tmp/runtime-projection.json \
  | jq '{schema_version,signature_algorithm,signature_key_id,generation,revision,expires_at_ms,provider_count:(.providers|length)}'

jq -e '
  .schema_version == "2" and
  .signature_algorithm == "Ed25519" and
  (.signature | length) >= 80 and
  (.checksum | length) == 64
' /tmp/runtime-projection.json
```

A `503` here is expected until Neo4j contains one approved, fresh, identity-complete runtime, access path, capacity observation, loaded model, and canonical projection approval. Do not bypass that gate with placeholder identity.

Router public health and protected administration:

```bash
curl -fsS http://127.0.0.1:18088/health | jq .

test "$(curl -s -o /dev/null -w '%{http_code}' \
  http://127.0.0.1:18088/admin/runtime-projection)" = 401

curl -fsS -H "X-Admin-Token: $AUTO_ROUTER_ADMIN_TOKEN" \
  http://127.0.0.1:18088/admin/runtime-projection | jq .
```

Unauthenticated inference must fail before idempotency reservation or routing:

```bash
test "$(curl -s -o /tmp/unauth.json -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: must-not-reserve' \
  -d '{"model":"auto/local","messages":[{"role":"user","content":"auth boundary"}],"max_tokens":8}' \
  http://127.0.0.1:18088/v1/chat/completions)" = 401
```

The AssistX-only internal token must cross authentication but remain subject to projection, admission, and provider state:

```bash
FIRST_CODE=$(curl -s -o /tmp/internal-first.json -w '%{http_code}' \
  -H "Authorization: Bearer $AUTO_ROUTER_INTERNAL_SERVICE_TOKEN" \
  -H 'Idempotency-Key: internal-smoke-1' \
  -H 'Content-Type: application/json' \
  -d '{"model":"auto/local","messages":[{"role":"user","content":"Reply with exactly: reconciliation-ok"}],"max_tokens":16}' \
  http://127.0.0.1:18088/v1/chat/completions)
echo "first request: $FIRST_CODE"
cat /tmp/internal-first.json | jq .

DUPLICATE_CODE=$(curl -s -o /tmp/internal-duplicate.json -w '%{http_code}' \
  -H "Authorization: Bearer $AUTO_ROUTER_INTERNAL_SERVICE_TOKEN" \
  -H 'Idempotency-Key: internal-smoke-1' \
  -H 'Content-Type: application/json' \
  -d '{"model":"auto/local","messages":[{"role":"user","content":"Reply with exactly: reconciliation-ok"}],"max_tokens":16}' \
  http://127.0.0.1:18088/v1/chat/completions)
test "$DUPLICATE_CODE" = 409
cat /tmp/internal-duplicate.json | jq .
```

The first response may be `200`, `429`, `503`, or `504` depending on approved projection and runtime availability. The duplicate must be `409` and must not be forwarded again.

## 7. Approve and verify runtime projection evidence

Use observed runtime/model evidence, not Compose defaults. The existing approval and verification tools remain the supported path:

```bash
cd ~/git/auto-assist
python scripts/approve-runtime-projection.py --help
python scripts/verify-runtime-projection.py --help
```

After approval, wait for the router poller and require a fresh configured generation:

```bash
for _ in $(seq 1 30); do
  STATUS=$(curl -fsS -H "X-Admin-Token: $AUTO_ROUTER_ADMIN_TOKEN" \
    http://127.0.0.1:18088/admin/runtime-projection)
  echo "$STATUS" | jq '{configured,fresh,current:.current.generation,error:.last_error}'
  echo "$STATUS" | jq -e '.configured == true and .fresh == true' >/dev/null && break
  sleep 2
done
```

Then run the private-network verifier from inside the router container:

```bash
cd ~/git/auto-router
python scripts/verify_reconciliation_network.py --help
```

Record LAN-first success and Tailscale fallback against the **same** runtime instance and shared capacity record.

## 8. Validate containment before enabling Hermes

```bash
cd ~/git/auto-assist
python scripts/validate-executor-containment.py \
  artifacts/reconciliation-preflight/assistx.compose.yaml
python scripts/validate-hermes-external-config.py --help
python scripts/validate-external-dependencies.py --help
```

Require all of the following before the next step:

- no host repository, SSH key, Docker socket, or broad home-directory mount;
- non-root executor UID/GID;
- read-only root filesystem, dropped capabilities, and no-new-privileges;
- no hosted-provider credentials;
- no admin, projection-signing, claim-status, or bootstrap credential in the Hermes child process;
- fresh runtime projection and active task claim;
- one bounded synthetic task with no destructive tools.

## 9. Run one synthetic task

Create a bounded synthetic task through the existing AssistX Control Room/API, record its task ID, and verify it is in the isolated reconciliation Neo4j database. The task should only write a disposable artifact under the mounted reconciliation artifacts directory.

Enable exactly one executor only after the task exists:

```bash
cd ~/git/auto-assist
docker compose \
  --env-file "$SECRETS/.env.reconciliation.generated" \
  --env-file "$SECRETS/site.env" \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.canary.yml \
  -f compose.reconciliation.yml \
  --profile executor \
  up -d hermes-adapter

docker logs -f assistx-canary-hermes-adapter
```

Expected lifecycle:

1. bootstrap token polls and claims;
2. AssistX issues a short-lived task token bound to task, claim, agent, projection generation, model aliases, and token budgets;
3. heartbeat starts before Hermes;
4. the task token is injected only into the Hermes child environment;
5. auto-router revalidates the claim after admission and before provider dispatch;
6. completion is accepted only while the same claim and projection remain active;
7. the executor exits after its single-task limit.

## 10. Failure and revocation tests

Run these only against the isolated reconciliation database and disposable task.

### Heartbeat/API loss

Pause the AssistX API long enough to exhaust the configured heartbeat failure budget:

```bash
docker pause assistx-canary-api
sleep 60
docker unpause assistx-canary-api
```

Expected: the Hermes process group is terminated, no authoritative completion is posted, and the task remains recoverable/reclaimable according to lease policy.

### Claim replacement or lease expiry

Using the isolated Neo4j browser or `cypher-shell`, change only the synthetic task so its `claim_id` no longer matches or its `lease_expires_at_ts` is in the past. Do not do this against production data.

Expected: AssistX task-token API calls return `401`, auto-router rejects provider dispatch, Hermes is terminated after heartbeat failures, and a duplicate request remains `possibly_accepted` rather than being blindly retried.

### Router restart during inference

```bash
docker restart auto-router-reconciliation
```

Expected: the interrupted attempt is not reported as a successful completed stream. The durable idempotency record prevents the same claim/JTI request from being forwarded twice after restart.

### Stream/client cancellation

Start a streaming request with a unique `Idempotency-Key`, then terminate the client after the first chunk.

Expected: iterator-final accounting records cancellation/ambiguity, releases quota/admission, does not mark circuit success at stream establishment, and leaves usage as `pending` when the provider supplied no final usage frame.

### Model-node loss

Stop the selected LM Studio/model process during a disposable request.

Expected: no failover to a public provider, no duplicate physical capacity record, no authoritative completion from a stale claim, and a clear failure/possibly-accepted event in the router outbox.

## 11. Capture evidence

Keep secrets out of evidence files.

```bash
mkdir -p ~/git/auto-assist/artifacts/reconciliation-evidence
cp /tmp/runtime-projection.json \
  ~/git/auto-assist/artifacts/reconciliation-evidence/

docker inspect assistx-canary-api auto-router-reconciliation \
  > ~/git/auto-assist/artifacts/reconciliation-evidence/container-inspect.json

docker logs assistx-canary-api \
  > ~/git/auto-assist/artifacts/reconciliation-evidence/assistx-api.log 2>&1
docker logs auto-router-reconciliation \
  > ~/git/auto-assist/artifacts/reconciliation-evidence/auto-router.log 2>&1

sha256sum ~/git/auto-assist/artifacts/reconciliation-evidence/* \
  > ~/git/auto-assist/artifacts/reconciliation-evidence/SHA256SUMS
```

Review logs for accidental token/private-key output before retaining or sharing them.

## 12. Stop the isolated stack

```bash
cd ~/git/auto-assist
docker compose \
  --env-file "$SECRETS/.env.reconciliation.generated" \
  --env-file "$SECRETS/site.env" \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.canary.yml \
  -f compose.reconciliation.yml \
  --profile executor \
  down

cd ~/git/auto-router
docker compose \
  --env-file "$SECRETS/.env.reconciliation.generated" \
  --env-file "$SECRETS/site.env" \
  -f docker-compose.yml \
  -f compose.reconciliation.yml \
  down
```

Do not remove reconciliation volumes until evidence has been reviewed. Do not merge or cut over until the exact tested commit SHAs, runtime identities, projection generation, model fingerprints, network paths, failure tests, and rollback results are recorded in the migration ledger.
