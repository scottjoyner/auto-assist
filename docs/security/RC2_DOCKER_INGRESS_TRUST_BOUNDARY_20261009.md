# AssistX backend header trust — release preflight

Status: **NO-GO until independently verified backend authorization and peer trust**.
Related: [#149](https://github.com/scottjoyner/auto-assist/issues/149),
[#156](https://github.com/scottjoyner/auto-assist/issues/156),
[opt-in strict-Basic #184](https://github.com/scottjoyner/auto-assist/pull/184).

## Verified read-only facts (2026-10-09)

- The AssistX API's host-published port is bound to `127.0.0.1:8000`.
- A configured `Tailscale-User-Login` trusted identity header is accepted by
  source code without Basic credentials unless opt-in strict Basic is enabled.
- The running AssistX API belongs to two Docker bridge networks shared with
  other service containers. An isolated, one-shot TCP-only Docker peer test
  reached `assistx-api:8000` without traversing Tailscale Serve.
- Tailscale Serve normally strips and re-injects its identity headers on Serve
  traffic; that behavior does **not** authenticate direct Docker peers.

These facts prove an alternate transport path and unsafe *potential* for
backend-supplied header impersonation. **No live forged-header authentication
attempt was conducted**; no incident exploit is claimed.

## Automated, read-only blocker

`python scripts/rc2-ingress-peer-gate.py` checks Docker container status,
host-loopback binding, trusted-header/strict-Basic mode *in memory*, and peer
container counts from every attached Docker network. It returns JSON with
category flags only; no raw environment values or credentials are emitted.
The script never calls HTTP endpoints, does not change firewall rules, and
always returns nonzero because reverse-proxy provenance still needs an
independent approval.

An `EXPOSED_POSSIBLE` result **must not be relabeled green** because a
host publish is loopback-only. A `NOT_DEMONSTRATED` result is likewise not
an attestation of ingress identity, authorization scope or credential custody.

## Separate promotion steps

1. Address confirmed public Git credential risk #156 privately through
   owner-controlled rotation and history containment; do not print values.
2. Pick one backend policy: an approved strict-Basic rollout with validated
   clients and new credentials, or an authenticated/attested proxy-only ingress
   that cannot be bypassed by Docker peers. Document every bypass route.
3. In an isolated production-equivalent environment, prove wrong Basic,
   anonymous and forged `Tailscale-User-Login` requests fail before the graph
   is accessed, including same-Docker-network peer requests.
4. Verify real Tailscale/iOS client compatibility, operator roles, rollback
   and health. Re-check network peers and Tailnet Serve mappings after rollout.
5. Independently close #148 physical trace-read fencing and #145 release CI.

No production access, key mutation, ingress reconfiguration or service
deployment is authorized by this document or its test suite.
