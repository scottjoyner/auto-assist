# AssistX offline research release reconciliation — 2026-10-08 RC1

**Release class: OFFLINE_RESEARCH_ONLY.** No production deploy, hosted inference, provider quota authority, or fleet command authorization.

Base: scottjoyner/auto-assist at a300072d11787a58a4587fa325f1b8fd66803730 (freshly fetched origin/main).
Candidate branch: rc/assistx-offline-reconcile-20261008. Source-only mock and read-only UI overlay, not installed anywhere.
All upstream PRs remain independent and unmerged.

## Included exact source revisions

| Component | PR and SHA | Scope |
| --- | --- | --- |
| Mercury simulation | auto-assist #130, a1d8059e21000b0af388e738b1c36ce1dd3826aa | Default-disabled fixture, lease test, research patch |
| Deny-only Mercury reference | auto-assist #133, 3d935cf151cc4aef0e7e4640de2d7436944e694b | Separate always-DENY stub, not integrated as production policy |
| Provider usage forecast | auto-assist #134, 3909a337549caf78d9f7db132b10dba548150737 | Observation-only burn JS, no quota authority |
| Trace investigation base | auto-assist #121, cecbb9961dd542743d1d851b4425e0af30c2b229 | Read-only authenticated UI scaffold |
| Keyboard and retry correction | auto-assist #131, e212c7eddf25cd043a849bfa570c9464341a85f1 | Exactly stacked on #121, updated traces.js and tests |

## Dependency reconciliation: deliberately NOT included

| Line | Dependency | Release disposition |
| --- | --- | --- |
| Execution #119 | synthetic dual-node trace + claims | HOLD: physical negative admission, API startup, key custody |
| Negative admission #132 | stacked on #119 | HOLD: fixture != physical rejection |
| NAS two-phase #135 | stacked on #119 | HOLD: simulated witness != independent WORM custody |
| Trace context #122 -> #124 -> #128 -> #129 | stacked chain on #121, but **not** #131 | HOLD: rebase #131 keyboard/retry fix onto stack tip and repeat browser/API tests |
| Shadow latency #90 -> #91 -> #92 | non-authoritative scoring | HOLD: no production route authority |
| Auto-router #30 | separate repo, read-only correlation usage projection | Separate release: 7 focused tests + SQLite probe pass with correct PYTHONPATH |
| Auto-router #13/#15/#16 | cache affinity, artifact selector, bounded observations | HOLD; #15 has nonmergeable status |
| Knowledge #51 | Mercury architecture handoff | Research prerequisite |
| Knowledge #52 | Jev signed synthetic lifecycle | HOLD; no hosted-inference or authority grant |
| Knowledge #54/#59/#61 | coordination, trace linkage, worktree-quota leases | HOLD; nonmergeable drafts, research only |

**Important UI overlap:** #131 and #122/#124/#128 each change static/js/traces.js; the latter branches also modify swarm_core.py or swarm_routes.py.
Blindly merging their tips discards fixes or introduces undocumented API behavior. Use a controlled rebase and repeat the complete browser/auth/Neo4j matrix.

**Important Mercury distinction:** #133 denies every fixture input; #130 can simulate a fixture turn when explicitly enabled.
Both are independent research contracts. Passing their separate tests does NOT demonstrate an end-to-end auto-assist -> auto-router -> real Mercury flow.

## Observed release-candidate verification

- Combined clean-main overlay: 80 pytest cases passing plus 5 reported pytest subtests; 15 native burn JS tests and 9 trace workbench Node VM tests passing.
- Type and syntax: Python research modules compile; JavaScript files parse.
- Isolated pinned Mercury source patch from #130: 12/12 native no-network TypeScript stream-fixture tests, pristine upstream patch-apply and Node syntax checks passed. No actual SDK or BotManager execution.
- Separate auto-router #30: 7 focused tests passed and the SQLite correlation-ID lookup/exclusion/index fixture passed with explicit PYTHONPATH targeting the PR checkout. An earlier invocation accidentally loaded an older installed package and must NOT be counted as PR evidence.
- Browser Playwright/Chromium: NOT reproduced in this combined checkout; Python Playwright dependency unavailable in the initial host environment. PR #131's prior reported browser acceptance remains independent evidence.
- Real generation calls 0; production services changed 0; provider credentials touched 0.

## Release decision

**Offline mock/read-only research overlay: PASS for the focused tests above.**
**Integrated production release: NO-GO.**

Production promotion gates: actual Mercury BotManager and SDK revocation; authenticated cross-node quota leases, physical upstream provider entitlements, and cancellation; independent WORM receipt and NAS restore with verified storage; production AssistX API startup and signing-key escrow; physical negative admission; stacked UI rebase plus authenticated 375px/tablet/desktop browser; cross-repo auto-router deployment proof; accepted work quality; and a separate dated operator release decision.

Existing FREE-PROVIDER-LEASE-NEXT-CHECKPOINT is NOT REACHED. Neither operator approval of this research nor the mock suite modifies that.

## Rollback

No live configuration, daemon, NAS mount, phone app, provider settings, or production router changed.
Remove the isolated RC branch/worktree to undo research integration. Source overlay archives are NOT daemon installers.
