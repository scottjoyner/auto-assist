# Deployment Conformance Monitoring — Scope
> Priority initiative: 2026-08-24
> Problem: services run stale/broken config for hours or days before anyone notices

## The pattern (four incidents, one root cause)

Every major issue this session was **declared-vs-running drift** discovered
accidentally, never proactively:

| Incident | Drift | Silent duration | How we found it |
|---|---|---|---|
| auto-assign ran pre-hardening code | image built before credential/token fixes | ~6 hours | user noticed nodes idle |
| voice-agent verify OOM | mem cap 512MB vs ECAPA needs | **days** (enroll squeaked by) | dmesg OOM-kill trace |
| LM Studio loopback-only bind | restarted without --bind 0.0.0.0 | ~40 min | container curl probe |
| Runtime projection expired | signed approval TTL lapsed | ~4 hours | hermes lane 503s |
| Tests failing on main | 27 broken tests shipped over weeks | weeks | first time anyone ran the suite |

Also latent: xwing's stale assistx checkout, hermes-adapter env missing
rotated creds until recreate, compose files referencing vars that don't
exist in .env (`:?` failures only at deploy time).

## Root cause
No continuous check that **running state matches declared state** across:
image digests, env contracts, endpoint reachability from containers,
credential freshness, repo sync state, test-suite health.

## Proposed: `assistx-doctor`

A conformance sweep owned by AssistX (it IS the control plane), running
periodically, surfacing via control room + coordination ledger.

### Checks (v1)
1. **Image drift**: running container image digest vs compose declaration
2. **Env contract**: required vars present + non-default
   (`:?` vars, rotated creds not matching fallbacks, tokens set)
3. **Endpoint reachability matrix**: container → declared dependency URLs
   (would have caught LM Studio bind change in minutes)
4. **Credential/projection freshness**: projection expiry, token age,
   cert/backup age vs schedule
5. **Repo sync**: node checkouts vs origin (fleet-wide, light)

### Surface
- `GET /api/doctor/report` — full findings, per-check pass/fail
- Control room banner: green / N warnings / M failures with links
- Coordination ledger entry on state change (healthy→degraded transitions)

### Implementation sketch
- `src/assistx/doctor.py`: pure-python checks, no new deps
- Reads compose files via `docker compose config` (declared truth)
- Reads docker inspect via mounted socket or ssh for remote nodes (v2)
- Timer: systemd user timer every 15 min on x1
- Findings also written to FLEET-STATE dir for cross-node visibility

### Explicitly out of scope v1
- Auto-remediation (report-only; remediation is operator/agent action)
- Remote node deep inspection (ssh-based checks are v2)
- LM Studio internals (covered by RuView trust governance)

## Effort estimate
- Checks 1-4 core: ~half a day including tests
- Control room integration: ~1 hour
- Fleet-wide repo sync (check 5): defer to v2

## Success criteria
Any of today's five incidents would have surfaced within 15 minutes of
onset, with enough detail to fix without archaeology.
EOF
cd ~/git/auto-assist && git add docs/DEPLOYMENT-CONFORMANCE-SCOPE.md 2>/dev/null || (mkdir -p docs && cp /home/scott/knowledge/20-Projects/assistx/Ops-State-2026-08-24.md docs/ 2>/dev/null); echo scoped