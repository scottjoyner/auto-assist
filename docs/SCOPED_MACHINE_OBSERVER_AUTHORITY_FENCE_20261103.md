# Scoped machine Task observer: fail-closed graph authority (research only)

**Status: DRAFT, off by default, not registered in production, no deploy, no Task claim.**

## Source finding

The disabled-by-default scoped_machine_poll observer independently checked node token,
a local allowlist, signed runtime projection, WorkSupplyState, physical model, access path
and capacity. But the final read could return a READY targeted Task ID without
requiring the graph's explicit machine Task approval, generation, owner or claim-clean
state. It did not check graph MachinePrincipalAdmission or token-epoch rotation.
This mismatch with the already stricter scoped claim contract can leak Task
identifiers if a future operator enables the observer under a permissive registry.
No live production endpoint or unauthorized Task leak has been observed.

The ordinary list_agent_tasks(agent_id=...) intentionally admits untargeted
legacy Tasks, so that API must NOT be substituted for this machine-only observer.

## Narrow research correction

- Require a positive, strictly integer token epoch in server-held node policy.
- Require exact approved, unexpired MachinePrincipalAdmission for this node,
  projection generation, artifact and token epoch. Fail closed on zero or
  multiple approved principals, including the final Task query.
- Require machine_chat_eligible=true, exact Task machine authorization,
  current generation, matching approved principal identity and nonempty owner.
- Exclude already-claimed Tasks, earlier execution attempts and Paperclip
  dispatch provenance. The final Task-ID read repeats current physical and
  canonical capacity/approval checks to avoid trusting a previous observation.
- Preserve authorizes_claim=false, authorizes_dispatch=false, Task IDs only,
  no payload and the default-disabled FastAPI router.

## Executed validation

On x1-370, exact local source tests: **101 passed** across observer, scoped
claim and execution contracts. The new disposable Neo4j test module:
**11/11 passed**, with approved Task positive and negatives for missing approval,
wrong generation/owner, stale claim, prior execution, ineligible chat model,
revoked or duplicate principals and wrong epoch. The graph was a new isolated
localhost-only test container with a memory/CPU cap; it was removed after the
test. Test assertions read Task properties before and after observer queries.

Host source lint/compile/diff checks pass. Hosted exact-head CI is a distinct
release gate; parent CI is not proof for this branch.

## Production blockers (not solved here)

The five existing user-level fleet agents have authenticated Task GET 401.
Their current Basic operator authentication contract is separate from this
unregistered machine observer. Credential-owner-approved scoped per-node
principals, complete direct writer/source census, issuer and independent
revocation/acceptance custody, approved current signed runtime projection,
explicit capacity, exact artifact and safe claim/heartbeat/completion fencing
are still required. A passing observer cannot be used as Task admission.

No production API routes, Task graph, services, model processes, keys,
provider routes or signed approvals were modified by this patch.
