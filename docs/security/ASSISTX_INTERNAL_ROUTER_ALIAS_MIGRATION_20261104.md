# AssistX internal router alias migration — held preflight

**November 4, 2026 · x1-370 · research-only implementation**

The running AssistX API was traced to its source Compose inputs
under /home/scott/git/auto-assist-deploy-20261016. The live
git-router-1 container publishes TCP 8088 on wildcard IPv4
and IPv6 interfaces, with five administrative GET routes still
anonymously accessible in the stale October 7 image. Changing
the router's host publication without first checking actual AssistX
traffic can cause an outage.

## Physically verified runtime dependencies

A small read-only Python process was executed in assistx-api
without accessing or printing any credentials.

| Runtime setting | Actual configured destination | TCP test |
| --- | --- | --- |
| AUTO_ROUTER_BASE_URL | host.docker.internal:8088 | Reachable |
| FLEET_ROUTER_URL | router:8088 | Not reachable |
| Router network alias | llm-router:8088 | Reachable |

Inside the same running AssistX API, a status-only public
GET /health returned HTTP **200** via both host.docker.internal
and llm-router. The live router advertises llm-router on
git_default; AssistX API is also attached to git_default.
These checks do not prove authorized inference, provider
inventory, runtime projection, agent polling or every other
fleet client's compatibility.

The router admin token was observed as configured in AssistX,
but no token value was retrieved, printed or used. Existing
auto-assist control-room source explicitly requires
AUTO_ROUTER_ADMIN_TOKEN before requesting protected
/admin/providers and /admin/admission, and tests reject
credential forwarding on HTTP redirects.

## Actual implementation in this branch

The canonical docker-compose.yml defaults were corrected from
the nonexistent auto-router and router service names to:

- AUTO_ROUTER_BASE_URL default: http://llm-router:8088
- FLEET_ROUTER_URL default: http://llm-router:8088

The matching network-attachment comment was also updated.
The separate OPENAI_BASE_URL default is intentionally left
on the host LM Studio endpoint :1234 and must not be replaced
by the router URL as part of this narrow change.

The new host-side script scripts/audit_router_alias_preflight.py
issues only bounded read-only Docker inspection and a fixed
process inside the running AssistX API to check two URL settings,
DNS/TCP and HTTP status of public /health via the internal
alias. It never reads operator tokens, prints configured URL
values, issues admin requests, reads HTTP bodies, modifies
service configuration or touches Docker lifecycle/network commands.

It requires the patched source defaults, both runtime
settings to be exactly the canonical internal alias URL, and
stable container image/start identity across the check.
Its result is strictly PASS_ALIAS_PATH_PREFLIGHT_ONLY or
HOLD; even a PASS never authorizes restarting or deploying
a container.

## Real fail-closed preflight

The actual current runtime yielded:

- Source Compose defaults pinned: **PASS** (isolated checkout)
- llm-router DNS: **PASS**
- llm-router:8088 TCP: **PASS**
- llm-router:8088/health HTTP 200: **PASS**
- Both runtime router URLs use llm-router: **FAIL**

Therefore the preflight correctly returned **HOLD / exit 2**.

Private 0600 evidence receipt on x1-370:
/tmp/assistx-router-alias-20261104-1512.json
SHA-256:
22000372bcc788a1042849d4b15d0b4b11b8dbafa41043ef56d886ead5d365d9.

The script and Compose change passed 23 isolated synthetic tests
with --noconftest (the repository-wide test fixture requires
optional AssistX dependencies absent from the lightweight
test venv). Targeted Ruff F401/F821 lint passed.

## Cutover ordering — not yet authorized

1. Independently review/merge the minimal Compose + preflight
   correction. Do not assume changed defaults override an
   existing .env or Compose override value.
2. Review live Compose interpolation and reconcile only the two
   relevant URL environment overrides to llm-router:8088,
   without printing or changing any credential.
3. Approve a controlled AssistX-only change with state/traffic
   rollback. Do not change router host-port binding in the same
   step. After the controlled change, rerun the read-only preflight
   to verify actual runtime values and internal health.
4. Verify authenticated /admin/providers and /admin/admission
   compatibility plus legitimate inference, fleet executor and
   task polling paths without leaking tokens or response bodies.
5. Only after all router clients, external ingress, and a secure
   fallback or isolation option are independently validated,
   consider restricting port 8088 or replacing the hardened
   Auto-Router production image.

**This branch was not deployed. Both running AssistX URL settings
remain misaligned and the production Auto-Router admin exposure
remains live. The combined release gate is NO-GO.**

## Newly discovered third consumer: Hermes adapter (later fleet iteration)

The previous two-variable preflight did **not** cover `hermes-adapter`. In the
Compose source the Hermes adapter independently used
`HERMES_LM_BASE_URL=http://host.docker.internal:8088/v1` and was **not**
attached to `git_default`. This is a concrete service-compatibility gap:
closing router host port 8088 after only migrating `assistx-api` could
strand Hermes even if the API is healthy. This evidence is from source on the
PR branch; it is not proof of the running Hermes container's effective values.

This branch now additionally:

1. Defaults `HERMES_LM_BASE_URL` to
   `http://llm-router:8088/v1` without changing model credentials or
   the separate host LM Studio port 1234.
2. Attaches the Hermes adapter to its existing default network **and**
   external `git_default`, preserving AssistX/Redis DNS while admitting the
   private router alias.
3. Extends the same read-only alias preflight with an exact-argv, bounded
   `docker exec assistx-hermes-adapter` probe. It reports only whether
   the current URL is canonical, whether DNS/TCP/public `GET /health`
   work, and whether the Hermes container image/start identity was stable.
   It reads no URL contents, authentication secrets, response bodies or
   protected API paths.
4. Rejects alias-path PASS unless the two API URLs, the Hermes `/v1`
   URL, both bridge attachments and both container identities are
   independently coherent. Dedicated synthetic tests cover a missing
   Hermes container, stale URL, embedded URL credentials, DNS/TCP failures,
   or container replacement during inspection.

**Critical cutover addition:** independently reconcile the actual active
deployment checkout and explicit Compose overrides for the Hermes adapter.
Approved migration must preserve Hermes identity, volume/home/SSH mounts,
task and inference compatibility, registered runtime projection, and any
unrelated services. Test the Hermes adapter against the authenticated
private `/v1` route and prove no regressions while router host 8088
remains temporarily available. Then require a separately witnessed full
client/ingress matrix before closing that host publication. No automatic
recreation, source overlay or network change is approved here.

This follow-up remains draft/NO-GO until **branch-head hosted CI** and real
owner-controlled verification are accepted; prior green CI on an older
two-variable branch does not validate the new Hermes integration.
