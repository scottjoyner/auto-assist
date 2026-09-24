# Kipnerter Fleet Model Replica-Loss Canary

This runbook proves the operational invariant behind opaque Fleet model handles:

> the same mobile `model:v1:*` handle and the same admitted artifact remain authoritative when one same-artifact replica becomes ineligible, while Auto-Router selects a different surviving replica and the mobile response exposes no physical runtime coordinates.

## Safety boundary

Do **not** change the phone's selected handle, rotate the mobile-handle secret, widen `allow_cloud`, enable Agent Auto fallback, or alter admission merely to make this canary pass.

Preferred execution is an ephemeral/staging topology that mirrors the current signed projection. A production run should be observation-only unless the existing deployment/rollback procedure explicitly authorizes a replica stop. If a production replica is already unavailable naturally, this canary may capture that transition without further mutation.

## Preconditions

- Kipnerter/AssistX model-handle route is deployed.
- Auto-Router contains the trusted route-evidence fields:
  - `assistx_mobile_model_handle`
  - `assistx_mobile_request_id`
  - `artifact_fingerprint`
  - chosen provider/replica
- The target Fleet model is `state=ready` with `ready_runtime_count >= 2`.
- The two replicas serve the same admitted artifact.
- The operator can read Auto-Router's local SQLite outbox. Default container DB is `/data/router.sqlite3`; use the matching host-mounted path for the deployment.

## Environment

~~~bash
export ASSISTX_BASE_URL="https://<assistx-tailnet-gateway>"
export DISPLAY_NAME="Ternary Bonsai 2"
export ROUTER_DB="/path/to/auto-router/data/router.sqlite3"
mkdir -p /tmp/kipnerter-replica-canary
cd /tmp/kipnerter-replica-canary
~~~

Use the real authenticated Tailscale/Serve path. Do not synthesize `Tailscale-User-Login` on an untrusted network path.

## Capture the before state

~~~bash
curl -fsS "$ASSISTX_BASE_URL/api/v1/runtime/catalog" > before-catalog.json

HANDLE="$(
  jq -r --arg name "$DISPLAY_NAME" '
    .models[]
    | select(
        .display_name == $name
        and .state == "ready"
        and (.ready_runtime_count | tonumber) >= 2
      )
    | .model_handle
  ' before-catalog.json | head -n1
)"
test -n "$HANDLE" && test "$HANDLE" != "null"

jq -n --arg handle "$HANDLE" '{
  model_handle: $handle,
  messages: [{role: "user", content: "Replica canary before-state probe. Reply with OK."}],
  stream: false,
  temperature: 0,
  max_tokens: 16
}' > before-request.json

curl -fsS   -D before-headers.txt   -o before-response.json   -H 'Content-Type: application/json'   --data-binary @before-request.json   "$ASSISTX_BASE_URL/api/v1/model/chat/completions"

BEFORE_REQUEST_ID="$(
  awk 'BEGIN{IGNORECASE=1}
       /^X-Kipnerter-Model-Request-ID:/ {
         gsub("\r", "", $2); print $2
       }' before-headers.txt | tail -n1
)"
test -n "$BEFORE_REQUEST_ID"

sqlite3 -json "$ROUTER_DB" "
  SELECT payload_json AS payload
  FROM event_outbox
  WHERE event_type = 'router.route_decision'
    AND json_extract(payload_json, '$.assistx_mobile_request_id') = '$BEFORE_REQUEST_ID'
  ORDER BY id DESC
  LIMIT 1;
" | jq '.[0] | {payload: (.payload | fromjson)}' > before-route-event.json

test "$(jq -r '.payload.assistx_mobile_request_id' before-route-event.json)" = "$BEFORE_REQUEST_ID"
~~~

## Replica eligibility transition

At this point the before evidence must show at least two ready replicas.

Make exactly one same-artifact replica unavailable **only through the approved canary/staging or existing operational procedure**. Do not change the artifact identity, model handle, handle secret, or routing authority.

Wait until the authoritative mobile catalog reports the same handle with a smaller positive `ready_runtime_count`.

## Capture the after state

~~~bash
curl -fsS "$ASSISTX_BASE_URL/api/v1/runtime/catalog" > after-catalog.json

test "$(
  jq -r --arg handle "$HANDLE" '
    .models[]
    | select(.model_handle == $handle)
    | .model_handle
  ' after-catalog.json
)" = "$HANDLE"

jq -n --arg handle "$HANDLE" '{
  model_handle: $handle,
  messages: [{role: "user", content: "Replica canary after-state probe. Reply with OK."}],
  stream: false,
  temperature: 0,
  max_tokens: 16
}' > after-request.json

curl -fsS   -D after-headers.txt   -o after-response.json   -H 'Content-Type: application/json'   --data-binary @after-request.json   "$ASSISTX_BASE_URL/api/v1/model/chat/completions"

AFTER_REQUEST_ID="$(
  awk 'BEGIN{IGNORECASE=1}
       /^X-Kipnerter-Model-Request-ID:/ {
         gsub("\r", "", $2); print $2
       }' after-headers.txt | tail -n1
)"
test -n "$AFTER_REQUEST_ID"
test "$AFTER_REQUEST_ID" != "$BEFORE_REQUEST_ID"

sqlite3 -json "$ROUTER_DB" "
  SELECT payload_json AS payload
  FROM event_outbox
  WHERE event_type = 'router.route_decision'
    AND json_extract(payload_json, '$.assistx_mobile_request_id') = '$AFTER_REQUEST_ID'
  ORDER BY id DESC
  LIMIT 1;
" | jq '.[0] | {payload: (.payload | fromjson)}' > after-route-event.json

test "$(jq -r '.payload.assistx_mobile_request_id' after-route-event.json)" = "$AFTER_REQUEST_ID"
~~~

## Build and validate the evidence bundle

~~~bash
jq -n   --slurpfile before_catalog before-catalog.json   --slurpfile before_response before-response.json   --slurpfile before_event before-route-event.json   --slurpfile after_catalog after-catalog.json   --slurpfile after_response after-response.json   --slurpfile after_event after-route-event.json   --arg before_request_id "$BEFORE_REQUEST_ID"   --arg after_request_id "$AFTER_REQUEST_ID"   '{
    before: {
      catalog: $before_catalog[0],
      mobile_response: $before_response[0],
      mobile_request_id: $before_request_id,
      route_event: $before_event[0]
    },
    after: {
      catalog: $after_catalog[0],
      mobile_response: $after_response[0],
      mobile_request_id: $after_request_id,
      route_event: $after_event[0]
    }
  }' > evidence.json

python /path/to/auto-assist/scripts/validate_mobile_model_replica_canary.py   evidence.json   --output result.json
~~~

A valid run returns `"result": "PASS"` and proves all of the following simultaneously:

- mobile handle is unchanged;
- artifact fingerprint is unchanged in trusted server-side evidence;
- chosen provider/replica changes;
- ready replica count decreases but remains positive;
- route profile stays `exact_artifact`;
- `local_only=true` and `allow_cloud=false`;
- each mobile request is matched to exactly its own internal route event by the AssistX-generated `kmr:*` correlation ID;
- the mobile response contains no artifact fingerprint, runtime instance, provider identity, base URL, access URL, or other physical route coordinate.

## Evidence retention

Retain `evidence.json`, `result.json`, both catalog snapshots, response bodies, response headers, and route-event extracts together with:

- exact `auto-assist` SHA;
- exact `auto-router` SHA;
- signed projection generation/revision/checksum from the route events when present;
- timestamp and environment/topology identifier.

Do not publish the internal evidence bundle to a client-visible endpoint. The route events intentionally contain server-side provider and artifact provenance.
