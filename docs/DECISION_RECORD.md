# Decision record: parallel Session 1-4 branches

_Last updated: 2026-10-03_

Two agent fleets worked the same four tickets concurrently and produced two
implementations of each. This records what was adopted from where, and why, so
the comparison does not have to be redone.

## Branches

| session | adopted | parallel branch |
|---|---|---|
| S1 auto-assist | `fix/repository-source-binding-20261002` | `feat/repository-source-binding-20261002b` |
| S2 auto-router | `feat/benchmark-qualification-ladder-20261002` | `feat/benchmark-qualification-ladder-20261002b` |
| S3 my-jev | `feat/fleet-benchmark-qualification-advisory-20261002` | `feat/fleet-benchmark-qualification-advisory-20261002b` |
| S4 kipnerter-ios | `docs/pushcut-transport-audit-20261002` | `docs/pushcut-transport-audit-20261002b` |
| S4 kipnerter-ios | (not used) | `chore/notification-pushcut-audit-20261002` |

A third kipnerter branch, `chore/notification-pushcut-audit-20261002`, also
exists. It is built on a 1165-commit-divergent tree and was not evaluated
against ours beyond confirming it targets the same audit.

## S1 - adopted from the parallel branch

`.gitignore` matched only `.env`, so `.env.override` and `.env.pre-*` snapshots
were untracked but **not ignored**. Six such files sit in the live clone, holding
the Neo4j password, paperclip tokens, the projection HMAC secret, the signing
key, and node tokens. A routine `git add -A` would have committed them.

Adopted the `.env.*` rule plus negations. Added a second negation
(`!.env.*.example`) that the parallel branch did not have: it would otherwise
have swallowed `.env.kipnerter-gateway.example`, a second tracked template.

Rejected: their `Pin commit dates so the mirror tests are deterministic`. It
addresses their test suite's need, not a defect in this one.

## S2 - adopted from the parallel branch

`evidence_from_response`. Here, `classify_failure` read `failure_mode` off the
row, so a harness reporting a reasoning-only response as a timeout was believed
- backwards trust for exactly the failures the pipeline exists to catch. The
builder derives the outcome from the response instead.

`EndpointStatus` with `PROTOCOL_UNUSABLE` as its own rung, so a host that
answers HTTP but cannot complete a usable exchange is not rounded up to
`MODEL_LOADED`.

Kept from this branch: the report shape Session 3 consumes, seven independently
measured capability axes, the uppercase role vocabulary the ticket specifies,
`next_benchmark_targets`, and the mutation-verified planner integration.

## S3 - nothing adopted

The parallel branch has no auto-router bridge, so its `auto-router-report.json`
fixture cannot be consumed by its own CLI. Ours ships
`fleet_benchmark_bridge.py` and is verified end to end against a real report.
Its `fresh_roles()` and typed evidence models are comparable to ours, not
better.

Both wrote fixtures under `examples/fleet-benchmark-advisory/`. Ours is the only
set a CLI can run; theirs is superseded rather than merged.

## S4 - adopted from the parallel branch

The fail-open authentication in `deploy/fleet-inbox/inbox_server.py`. Their
`a9a2026c` and `c7fc11d9` applied to our tree; the auth defect was real here and
the original audit missed it. Audit section 10a records it.

Rejected: `921e0f93 Retire ruview_webhook_server.py`. That file does not exist
at `2dc99db`, so the commit cannot apply and the removal already happened
upstream.

## Standing risk

The parallel S4 branch audits a **different codebase**. It is built where
`deploy/ruview/` exists; at our base `2dc99db` it does not. Any branch carrying
those files needs its own audit. Until one base is chosen, "no active outbound
Pushcut producer" is a statement about `2dc99db` and not about every tree.

## Not done

Nothing was pushed. No branch was merged wholesale. Authority, runtime,
admission, routing, and live deployment are untouched in all four repositories.
