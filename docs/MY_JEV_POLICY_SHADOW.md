# my-jev policy shadow integration

## Purpose

This integration observes how the new System-One policy model would route an
AssistX intent without changing the current production decision.

Shadow mode is deliberately the first deployment phase.

```text
incoming Intent
     |
     v
current classifier/orchestrator ----> live behavior
     |
     +----> mark intent orchestrated
               |
               v
        enqueue sanitized snapshot
               |
               v
        shadow observer job ----> my-jev sidecar
                                      |
                                      v
                               policy_shadow_* only
```

The two paths are recorded together so disagreements can become training and
evaluation examples.

## Safety boundary

Shadow mode never:

- replaces `classification`;
- replaces `policy_action`;
- creates, claims or cancels tasks;
- invokes Hermes tools;
- grants an action scope;
- bypasses approvals;
- changes recovery or mutation authority.

The sidecar can return a resolved recommendation, but AssistX only stores it.

The live intent handler never calls the sidecar. After live handling succeeds and
the Intent is marked orchestrated, AssistX best-effort enqueues a sanitized snapshot
containing only the fields needed for policy observation. The observer job owns its
own Neo4j client and performs the HTTP request out of band.

A queue failure, timeout, connection failure, malformed response or unavailable
recorder is logged and ignored. None can reopen the already-committed live decision.
Shadow persistence is restricted to `policy_shadow_*` fields and does not update
the Intent's generic lifecycle timestamps.

## Configuration

Disabled by default:

```env
MY_JEV_POLICY_SHADOW_ENABLED=false
MY_JEV_POLICY_URL=http://my-jev:8088/v1/agent-policy
MY_JEV_POLICY_SHADOW_TIMEOUT_S=0.75
MY_JEV_POLICY_REQUIRE_DECISION_RECEIPT=false
```

The shadow resolver also receives conservative runtime authority flags. Keep
these false unless the shadow experiment explicitly needs to model another
policy profile:

```env
MY_JEV_SHADOW_ACTIONS_ALLOWED=false
MY_JEV_SHADOW_LOCAL_WRITES_ALLOWED=false
MY_JEV_SHADOW_EXTERNAL_ACTIONS_ALLOWED=false
MY_JEV_SHADOW_PRIVILEGED_ACTIONS_ALLOWED=false
MY_JEV_SHADOW_APPROVAL_GATE_AVAILABLE=true
```

These values affect the sidecar's **resolved recommendation**, not live
execution.

## Evidence

Each observed Intent stores:

- `policy_shadow_json` — complete request, response and legacy comparison;
- `policy_shadow_contract`;
- `policy_shadow_route`;
- `policy_shadow_disposition`;
- `policy_shadow_policy_action`;
- shadow timestamp.

When a sidecar response includes `system-one-decision-receipt-v1`, AssistX
strictly validates its provider/model identity, question distributions, response
hash and literal-false authority fields. It then stores an observation-only
binding between the exact AssistX request hash and the provider's model-visible
input/question/candidate/response hashes. The two input hashes are deliberately
kept distinct because the sidecar owns model-state normalization.

Set `MY_JEV_POLICY_REQUIRE_DECISION_RECEIPT=true` only after all configured
shadow producers emit the stable receipt contract. With the default `false`,
older shadow producers remain compatible; any receipt that is present must still
validate.

The evidence includes the current legacy classification/policy action alongside
the model route. The operator-run exporter now enriches each row with a
read-only `trajectory` object assembled from existing graph evidence:

- explicit Task approval fields when present;
- terminal Task status, completion actor, summary and result;
- linked AgentRun status/model/result;
- linked ToolCall input/output and success flag;
- verification evidence recorded by the existing `acceptance` tool or explicit
  task-result verification fields.

Approval and verification are intentionally **not inferred** from `READY`,
`DONE`, a successful tool call, or any other proxy. Missing explicit approval
evidence therefore means "unknown/not recorded", not "not approved".

This slice does not yet emit user-correction labels because AssistX does not have
one canonical Intent-to-correction relation to read. A future evidence-only slice
can add that once the provenance link is explicit; the exporter must not guess
corrections from later messages.

The resulting rows contain:

```text
policy state
model probability vector
legacy behavior
resolved shadow recommendation
explicit approval evidence
task outcome
agent runs
tool calls
explicit verification evidence
```

That is the basis for the DAgger-style trajectory loop described in
`scottjoyner/my-jev/docs/ASSISTX_POLICY.md`. The exporter remains read-only and
does not feed any trajectory field back into classification, dispatch, approval,
tool authorization, mutation authority or recovery control.

## Rollout phases

1. **offline** — synthetic/verifier corpus only;
2. **shadow** — observe real intents, no behavior change;
3. **advisory** — expose model recommendation to operators/current orchestrator;
4. **bounded routing** — allow high-confidence chat/read-only/task-graph routes;
5. **action routing** — only after calibration, authority-counterfactual and
   state-sensitivity gates are proven; all existing approval/mutation gates
   remain authoritative.

A checkpoint should never advance phases merely because top-1 route accuracy is
high. Promotion also needs calibration, shuffled-state degradation, low-risk
false-action analysis, and replay against real user corrections.


### Offline physical-receipt acceptance

Before using System-One receipts as fleet benchmark provenance, capture one harmless
policy request and its `/v1/agent-policy` response from the physical producer as
JSON files. Validate those files offline; do not route the captured recommendation
or persist it as authoritative state.

```bash
python scripts/validate-system-one-shadow-receipt.py \
  --request artifacts/system-one/request.json \
  --response artifacts/system-one/response.json \
  --expected-provider-id my-jev \
  --expected-model-id '<exact-checkpoint-id>' \
  --expected-model-artifact-sha256 '<64-lowercase-hex>' \
  --require-model-artifact \
  --output artifacts/system-one/acceptance.json
```

The validator performs no network access. It revalidates the receipt, recomputes
the AssistX request and normalized response hashes, checks the expected
provider/model/artifact identity, and requires the complete literal-false authority
block. Keep `MY_JEV_POLICY_REQUIRE_DECISION_RECEIPT=false` during initial fleet
acceptance so legacy shadow producers remain compatible.
