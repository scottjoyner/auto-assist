# Kipnerter Fleet Model Replica-Loss Canary

This runbook proves the operational invariant behind opaque Fleet model handles:

> the same mobile `model:v1:*` handle and the same admitted artifact remain authoritative when one same-artifact replica becomes ineligible, while a different surviving replica actually completes the request and the mobile response exposes no physical runtime coordinates.

## Safety boundary

Do **not** change the phone's selected handle, rotate the mobile-handle secret, widen `allow_cloud`, enable Agent Auto fallback, or alter admission merely to make this canary pass.

Preferred execution is an ephemeral/staging topology that mirrors the current signed projection. A production run should be observation-only unless the existing deployment/rollback procedure explicitly authorizes a replica stop. If a production replica is already unavailable naturally, this canary may capture that transition without further mutation.

The capture tool is itself read-only with respect to fleet/runtime state. It refuses to overwrite an existing evidence directory and refuses an after-phase capture when the declared AssistX or Auto-Router SHA changed since the before phase.

## Preconditions

- Kipnerter/AssistX model-handle route is deployed.
- Auto-Router contains trusted server-side route evidence for:
  - `assistx_mobile_model_handle`;
  - `assistx_mobile_request_id`;
  - `artifact_fingerprint`;
  - `router.route_decision` with `profile=exact_artifact`;
  - `router.execution_stage.completed` with the actual serving provider/replica.
- The target Fleet model is `state=ready` with `ready_runtime_count >= 2`.
- The replicas serve the same admitted artifact.
- The operator can read Auto-Router's local SQLite outbox. Default container DB is `/data/router.sqlite3`; use the matching host-mounted path for the deployment.
- Exact deployed AssistX and Auto-Router SHAs are known.

## Zero-state x1-370 bootstrap

No existing clone is required. On `x1-370`, this single command downloads only the bootstrap script first; the script then creates normal full Git clones and pinned worktrees under `~/git`:

~~~bash
curl -fsSL \
  https://raw.githubusercontent.com/scottjoyner/auto-assist/agent/kipnerter-model-handle-resolution/scripts/bootstrap-x1-370-canary-workspace.sh \
  | bash
~~~

The command now carries the safe flow all the way to the real mutation boundary:

~~~text
create/reuse full clones in ~/git
        |
        v
create clean pinned authority worktrees
        |
        v
hash live AssistX + Auto-Router authority code
against the pinned runtime revisions
        |
        v
read-only live topology/catalog preflight
        |
        v
optional GitHub runner registration
(only when gh is installed + authenticated)
        |
        v
automatic replicated BEFORE capture
        |
        v
STOP and print transition_target_provider
~~~

It creates or safely reuses:

~~~text
~/git/auto-assist
~/git/auto-router
~/git/auto-assist-canary-ops
~/git/auto-assist-canary-runtime
~/git/auto-router-canary-runtime
~/git/canary-evidence/
~~~

Existing canonical clones are fetched but their checked-out branch is not changed. Existing canary worktrees must be clean or the bootstrap fails closed instead of resetting local changes.

The pinned authority runtime revisions are:

~~~text
AssistX mobile route: aad657bbf4fff5bec2080ada07f5a4ad02292743
Auto-Router authority path: 1fbb9726de46a30c0c91616c65c04dc1f35845a6
~~~

The local preflight does not trust Docker image labels. It hashes the live AssistX `mobile_agent_routes.py` plus Auto-Router `policy.py` / `route_events.py` and compares them directly with the pinned worktrees. For AssistX it also checks that the API process was started after the current bind-mounted authority file mtime, preventing a stale Python process from masquerading as an exact-code match.

GitHub runner registration is no longer required for the canary itself. If `gh` is installed and authenticated, the bootstrap also registers/starts the `x1-370,assistx-canary` runner for ongoing evidence workflows. Set `REGISTER_RUNNER=0` to skip that optional step.

The official runner bootstrap defaults to GitHub Actions Runner `v2.337.0` and verifies the published Linux x64 SHA-256 before extraction.

A successful bootstrap ends after the BEFORE capture and prints the exact `transition_target_provider`. That is the first point requiring an approved runtime eligibility mutation.

## Environment

~~~bash
export ASSISTX_BASE_URL="https://<assistx-tailnet-gateway>"
export DISPLAY_NAME="Ternary Bonsai 2"
export ROUTER_DB="/path/to/auto-router/data/router.sqlite3"

# These must identify the actual deployed code used for both phases.
export ASSISTX_SHA="<deployed-auto-assist-sha>"
export AUTO_ROUTER_SHA="<deployed-auto-router-sha>"

export CANARY_DIR="/tmp/kipnerter-replica-canary-$(date -u +%Y%m%dT%H%M%SZ)"
~~~

Use the real authenticated Tailscale/Serve path. Do not synthesize `Tailscale-User-Login` on an untrusted network path.

If an additional gateway header is required, put its value in an environment variable and pass only the environment-variable name, for example:

~~~bash
export KIPNERTER_GATEWAY_AUTH="Bearer ..."
EXTRA_AUTH=(--header-env "Authorization=KIPNERTER_GATEWAY_AUTH")
~~~

The collector explicitly rejects `Tailscale-User-Login=...` through `--header-env`.

## Preferred operator flow

### 1. Capture the replicated before state

~~~bash
python scripts/capture_mobile_model_replica_canary.py before   --assistx-base-url "$ASSISTX_BASE_URL"   --router-db "$ROUTER_DB"   --display-name "$DISPLAY_NAME"   --assistx-sha "$ASSISTX_SHA"   --router-sha "$AUTO_ROUTER_SHA"   --out-dir "$CANARY_DIR"   "${EXTRA_AUTH[@]}"
~~~

The command fails unless:

- the target row is `ready`;
- it has at least two ready replicas;
- AssistX returns the selected opaque handle;
- the response contains an opaque `X-Kipnerter-Model-Request-ID: kmr:*`;
- the matching `exact_artifact` route-decision event appears;
- the matching completed execution event appears.

The collector writes immutable before evidence and `capture-state.json`. Its JSON summary also emits `transition_target_provider`; that is the **before-serving replica** the approved canary transition must remove from eligibility.

### 2. Perform the approved replica eligibility transition

Make the exact `transition_target_provider` from the before capture unavailable **only through the approved canary/staging or existing operational procedure**. Do not remove an arbitrary sibling replica: the canary is intended to prove that the replica which actually served the before request leaves the exact-artifact candidate set. Do not change the artifact identity, model handle, handle secret, AssistX code, Auto-Router code, or routing authority.

Wait until the authoritative mobile catalog reports the same handle with a smaller positive `ready_runtime_count`.

If this is a naturally occurring replica loss, make no runtime mutation; simply continue after the catalog reflects the transition.

### 3. Capture and validate the after state

~~~bash
python scripts/capture_mobile_model_replica_canary.py after   --assistx-base-url "$ASSISTX_BASE_URL"   --router-db "$ROUTER_DB"   --assistx-sha "$ASSISTX_SHA"   --router-sha "$AUTO_ROUTER_SHA"   --out-dir "$CANARY_DIR"   "${EXTRA_AUTH[@]}"
~~~

The after command automatically:

1. reuses the before-phase opaque handle;
2. fails if either declared exact head changed;
3. fails unless the ready replica count decreased but remains positive;
4. sends the second plain Fleet-model probe;
5. captures the second `kmr:*` ID;
6. joins that request to both route decision and completed execution evidence;
7. builds `evidence.json`;
8. runs `validate_mobile_model_replica_canary.py`;
9. writes `result.json`.

Expected result:

~~~json
{
  "result": "PASS",
  "authority_invariant": "same_handle_same_artifact_different_replica_no_authority_widening"
}
~~~

## What PASS proves

A valid run proves all of the following simultaneously:

- the mobile handle is unchanged;
- the exact artifact fingerprint is unchanged in trusted server-side decision and execution evidence;
- the **completed serving provider/replica** changes;
- the before-serving provider is absent from the after `exact_artifact` candidate set;
- ready replica count decreases but remains positive;
- policy profile stays `exact_artifact`;
- decision and execution remain `local_only=true` and `allow_cloud=false`;
- each mobile response is matched to its own decision and completed execution events by the AssistX-generated `kmr:*` correlation ID;
- the mobile response contains no artifact fingerprint, runtime instance, provider identity, base URL, access URL, or other physical route coordinate;
- the canary ran against one declared AssistX SHA and one declared Auto-Router SHA from before through after.

The decision's initially chosen provider is **not** used as the serving-replica proof. If candidate A fails and the same exact-artifact stage succeeds on candidate B, only `router.execution_stage.completed` proves B actually served the request.

## Evidence emitted

The collector preserves:

~~~text
capture-state.json
before-catalog.json
before-response.json
before-response-headers.json
before-request-metadata.json
before-route-decision.json
before-route-execution.json
after-catalog.json
after-response.json
after-response-headers.json
after-request-metadata.json
after-route-decision.json
after-route-execution.json
evidence.json
result.json
~~~

Retain the directory with:

- exact `auto-assist` SHA;
- exact `auto-router` SHA;
- signed projection generation/revision/checksum from route evidence when present;
- timestamp and environment/topology identifier.

Do not publish the internal evidence bundle to a client-visible endpoint. Route events intentionally contain server-side provider and artifact provenance.

## Manual fallback

The collector is the normative path because it avoids request/event mismatches and accidental evidence reuse. If it cannot run in the target environment, reproduce the same steps manually:

1. capture the authoritative catalog;
2. POST one non-stream Fleet-model probe;
3. read `X-Kipnerter-Model-Request-ID`;
4. query the Auto-Router outbox for both `router.route_decision` and `router.execution_stage.completed` with that exact ID;
5. repeat after the replica transition with the unchanged handle;
6. construct the same evidence schema;
7. run `scripts/validate_mobile_model_replica_canary.py`.

Manual evidence is not acceptable if it cannot correlate each phone-safe request to its exact decision and completed execution events.
