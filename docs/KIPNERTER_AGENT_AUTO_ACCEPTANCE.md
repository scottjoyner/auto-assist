# Kipnerter Agent Auto live acceptance and reporting contract

This document is the canonical acceptance contract for the Kipnerter mobile
Agent Auto path provided by AssistX.

It exists to prevent three different statements from being collapsed into one:

1. the repository implementation is validated;
2. the intended source revision is deployed on x1-370; and
3. a real Tailnet client can execute one bounded Agent Auto request through the
   production Serve boundary and receive a Hermes-backed response.

Only statement 3, with statements 1 and 2 also proven for the same exact source
revision, permits the operational claim **Agent Auto live path verified**.

## Scope

The accepted path is:

```text
Kipnerter iOS / Tailnet client
  -> route-scoped HTTPS Tailscale Serve :8443
  -> AssistX mobile boundary on loopback :8000
  -> server-side Hermes
  -> scoped AssistX / Auto-Router inference path
```

This acceptance does not authorize or validate:

- Tailscale Serve reconfiguration;
- a raw AssistX listener on a LAN or Tailnet interface;
- direct phone access to Hermes or Auto-Router;
- Auto-Router admin access;
- direct model/provider credentials on the phone;
- runtime admission or model-loading changes;
- the separate `/api/v1/runtime/catalog` / model-handle work;
- recovery, deployment, approval, promotion, or other mutation authority.

## Required source state

Every live attempt must record one full 40-character Git SHA.

The operator must prove:

- the checkout HEAD equals the declared expected SHA;
- the worktree is clean;
- the source revision is the exact PR candidate being accepted;
- the API image is rebuilt from that exact checkout;
- only `assistx-api` is recreated for this acceptance slice.

A merge, branch name, tag, or recent `git pull` is not deployment evidence by
itself.

## Repository validation gate

Repository validation and live validation are separate records.

For the exact candidate SHA, record:

- `implementer-handoff` result;
- degraded-disaster canary result;
- recovery canary result;
- unit-suite pass/fail counts;
- any baseline exceptions, with evidence that the same failure exists on the
  PR base/current main and is outside this slice.

A baseline exception is not a green test. It must be named explicitly.

For PR #50, the currently recognized baseline exceptions are the two
fleet-dashboard HTML assertions in
`tests/test_fleet_dashboard_inference.py`:

- `test_fleet_dashboard_html_includes_inference_section`;
- `test_fleet_dashboard_html_has_all_sections`.

If either failure changes, a new failure appears, or one of the Agent Auto
guardrail tests fails, the baseline exception cannot be reused.

## Live validation gate

Use:

```bash
EXPECTED_SHA=<exact candidate SHA> \
  bash scripts/verify-kipnerter-agent-auto-live.sh
```

The verifier must prove all of the following:

1. exact SHA and clean checkout;
2. pre-deploy Tailscale Serve snapshot captured;
3. API image rebuilt from the exact source;
4. only `assistx-api` recreated with dependencies left running;
5. `127.0.0.1:8000` / loopback-only raw AssistX publication;
6. `TRUSTED_AUTH_HEADER=Tailscale-User-Login`;
7. `FLEET_ROUTER_BEARER_TOKEN` is present without exposing its value;
8. effective Hermes provider remains `assistx-router`;
9. post-deploy Tailscale Serve snapshot is byte-identical to pre-deploy state;
10. Tailnet `/api/v1/auth/whoami` returns HTTP 200 with
    `authenticated=true` and `provider=tailscale`;
11. a spoofed internal executor identity on loopback is rejected with HTTP 401;
12. exactly one no-tool/no-mutation `fleet-auto` request is sent through the
    existing Serve gateway;
13. that request returns HTTP 200;
14. the response carries `X-Kipnerter-Agent-Executor: hermes`;
15. the response contains non-empty assistant content.

No direct Auto-Router probe is part of this acceptance. A failed Agent Auto
request is evidence to diagnose later, not authorization to bypass AssistX or
add credentials to the phone.

## Stop conditions

Stop immediately and report **FAIL** or **BLOCKED** if:

- the expected SHA differs from the checkout;
- the worktree is dirty;
- the API does not become healthy;
- raw AssistX publication is not loopback-only;
- required server-side credential wiring is absent;
- Tailscale Serve changes;
- Tailnet identity does not authenticate;
- the spoof negative does not return 401;
- the single Agent Auto smoke is not HTTP 200;
- the Hermes executor response header is absent or different.

Do not retry by widening authority. In particular, do not:

- add a client bearer credential;
- use the Auto-Router admin token;
- expose another Serve path as a workaround;
- publish raw port 8000;
- perform extra direct router probes;
- switch the phone to a direct model lane and call that an Agent Auto pass.

## Evidence bundle

Every attempt after the evidence directory is created must retain a bounded
bundle under:

```text
artifacts/kipnerter-agent-auto-live/<UTC timestamp>/
```

The bundle is expected to contain, when the attempt reaches the corresponding
stage:

- `source-sha.txt`;
- `gateway-url.txt`;
- Serve before/after snapshots and checksums;
- API image before/after identifiers;
- loopback port-binding evidence;
- sanitized runtime configuration evidence;
- health response;
- Tailnet whoami response and HTTP status;
- executor-spoof negative response and HTTP status;
- the single Agent Auto request;
- response headers, body, and HTTP status;
- `result.txt` on success;
- `validation-report.json`;
- `knowledge-report.md`.

Secret values must never be included in the evidence bundle.

## Result vocabulary

Use only these top-level operational results:

| Result | Meaning | Healthy claim allowed? |
|---|---|---:|
| `NOT_RUN` | exact live verifier has not been executed | no |
| `BLOCKED` | verifier could not reach a required precondition without testing the path | no |
| `FAIL` | a required invariant or live check failed | no |
| `PASS` | all required live checks passed on one exact SHA | yes |

Do not use phrases such as "probably deployed", "looks healthy", or "merge
should fix it" as acceptance results.

The exact permitted healthy statement is:

> Agent Auto live path verified at `<full SHA>` by exact-source API recreate,
> unchanged Serve topology, Tailnet identity, executor-spoof rejection, and one
> HTTP 200 Hermes-backed Agent Auto smoke.

## Knowledge-base reporting

The project knowledge base is the durable human-readable record. The canonical
existing gateway log is:

```text
20-Projects/kipnerter-ios/EXECUTION-LOG-2026-09-09-RC2-GATEWAY.md
```

For every live attempt:

1. preserve the raw evidence bundle in the auto-assist checkout;
2. publish the generated `knowledge-report.md` into:
   `20-Projects/kipnerter-ios/validation/`;
3. append a concise checkpoint to the gateway execution log containing:
   - UTC timestamp;
   - exact auto-assist SHA;
   - result: `BLOCKED`, `FAIL`, or `PASS`;
   - evidence directory;
   - repository-validation state and any named baseline exception;
   - last completed live stage;
   - failure reason when not PASS;
   - confirmation that Serve and authority boundaries were not widened;
4. do not copy secrets, raw bearer values, or private identity details into the
   knowledge repo.

Publishing the report is documentation, not deployment authority. It does not
change the live result and must not convert a failure into a pass.

Use `scripts/publish-kipnerter-agent-auto-report.sh` after the verifier to
perform the bounded knowledge-base write.

## Cross-repository reporting

When this acceptance changes state, references may be added to the paired
Kipnerter iOS work, but the server result must remain authoritative:

- iOS readiness does not prove the x1-370 deployment;
- a server PASS does not prove physical-iPhone UX;
- runtime-directory/model-handle acceptance remains a separate slice.

This separation preserves the existing Hermes/AssistX authority and prevents
one green layer from being reported as proof for another.
