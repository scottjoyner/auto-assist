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
