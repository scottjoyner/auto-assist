# Fleet task-family routing

## Purpose

Fleet capability evidence is useful only when the task retains its intent from
creation through allocation, claim, and gateway dispatch. AssistX now carries an
explicit task family through that complete path instead of selecting a concrete
model before auto-router can compare the fleet.

## Supported families and virtual aliases

| Task family | Virtual model alias | Typical workers |
|---|---|---|
| `coding` | `auto/code` | full agent or code-agent nodes |
| `reasoning` | `auto/high-quality` | full agent or reasoning nodes |
| `tool_use` | `auto/high-quality` | full agent or tool-agent nodes |
| `long_context` | `auto/review` | full agent or long-context nodes |
| `summarization` | `auto/summarize` | full or auxiliary LLM nodes |
| `compression` | `auto/compress` | full or auxiliary LLM nodes |
| `extraction` | `auto/extract` | full or auxiliary LLM nodes |

The virtual alias identifies a routing policy, not a physical model. auto-router
chooses the final admitted node/model using signed role and benchmark evidence.

## Producer tagging

The continuous work producers add task-family data before persistence:

- repository analysis and bounded improvement proposals: `coding`;
- paper, signal, failure, and research insight tasks: `reasoning`;
- memory synthesis and summary-style knowledge work: `summarization`;
- explicit compaction work: `compression`;
- explicit structured/entity extraction work: `extraction`.

An explicitly supplied `task_family` or `workload_class` takes precedence over
inference from kind, title, or prompt.

Persisted payload example:

```json
{
  "task_family": "compression",
  "workload_class": "compression",
  "model": "auto/compress",
  "prompt": "Compress this context while preserving decisions and identifiers."
}
```

## Allocation

AssistX evaluates each task independently:

1. queue priority and current claim state;
2. node worker mode and permitted roles;
3. exact node/model/family quality-floor exclusion;
4. benchmark quality, throughput, reliability, and confidence;
5. current load, opportunity cost, learned reliability, and KV-cache locality.

An unmeasured eligible model may be used as a fallback. An exact loadout with a
measured quality-floor failure is removed for that family.

## Claim-scoped gateway dispatch

The safe fleet executor no longer turns a task family into one concrete model
before gateway dispatch. It sends the virtual alias and family metadata to
auto-router.

Immediately before each request, AssistX mints a short-lived Ed25519 executor
token containing:

- task ID;
- current claim ID;
- executor agent ID;
- current runtime projection generation;
- `inference` scope;
- the exact virtual or concrete model alias used by the request;
- input/output token and attempt limits;
- an expiration bounded by the configured task lease and token TTL.

This means virtual routing still passes auto-router's executor-auth middleware and
its post-queue AssistX claim-status recheck. The safe executor does not rely on a
static bearer token for task inference.

Required key configuration:

```dotenv
ASSISTX_EXECUTOR_SIGNING_KEY_FILE=/run/secrets/assistx-executor-ed25519.pem
ASSISTX_EXECUTOR_KEY_ID=assistx-executor-v1
ASSISTX_EXECUTOR_TOKEN_TTL_SECONDS=600
ASSISTX_EXECUTOR_MAX_INPUT_TOKENS=65536
ASSISTX_EXECUTOR_MAX_OUTPUT_TOKENS=8192
ASSISTX_EXECUTOR_MAX_INFERENCE_ATTEMPTS=4
```

Matching auto-router configuration:

```dotenv
AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED=true
AUTO_ROUTER_EXECUTOR_VERIFY_KEY_FILE=/run/secrets/assistx-executor-ed25519-public.pem
AUTO_ROUTER_EXECUTOR_KEY_ID=assistx-executor-v1
AUTO_ROUTER_EXECUTOR_CLAIM_STATUS_URL=http://assistx:8000
AUTO_ROUTER_ASSISTX_EXECUTOR_SERVICE_TOKEN=replace-with-claim-status-service-token
```

The claim-status token is a separate service credential. It is not an inference
credential.

## Verification

Create one task per family and verify the router decision records the expected
family and an eligible role. At minimum:

- summarization may select an auxiliary node;
- compression may select an auxiliary node;
- coding may not select an auxiliary-only node;
- a quality-floor-failed exact loadout is absent from candidates;
- the request token model scope equals the virtual alias;
- stale claim and stale projection negative canaries still fail closed.

Focused tests:

```bash
pytest -q tests/test_task_family_routing.py
pytest -q tests/test_benchmark_allocation_quality_floor.py
```
