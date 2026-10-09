# AssistX Offline Safety RC2 — 2026-10-08

**PRE-REGISTERED BEFORE INTEGRATED TESTS.**

Base: locally tested offline RC1 branch commit 4f4490842de8fdde0eae9dc8ef15506bff1bec7a
(from draft GitHub PR #136). Add only the independently reviewed source overlays:
source binding draft #144, model output draft #151, Jev observer draft #152
and workbench test draft #153. These drafts have independent mainline bases;
no production API or provider deployment is implied.

**Predictions:**
1. The 56 source-binding tests remain fail-closed under the RC tree.
2. The 38 provider-output-integrity tests still reject false health credit and
   preserve valid UTF-8 text/declared charsets and real transport errors.
3. The 17 synthetic Jev receipt tests pass; shadow observer stays explicit-only,
   cannot grant authority or contact a real provider in the tests.
4. RC1's 80 Mercury mock Python cases + 5 subtests, 24 Node workbench/burn
   tests, and 21 synthetic Chromium checks remain unchanged and passing.
5. There will be no source-path collisions among these four safety fixes.
6. The auth fixture change must remain unclaimed until full dependency-enabled
   GitHub CI checks both valid-credential 200 and wrong-password 401.
7. No live hosted generation, API/NAS/Neo4j/SSH service, keys, or production
   router state may be touched.

**This candidate is NOT a deployable release.** It does not contain
#140's stacked backend trace source (that remains reviewed separately)
and it does not satisfy authenticated live browser/device or physical
negative admission and WORM custody. Its free-provider checkpoint is
CHECKPOINT_NOT_REACHED.

## Observed integration acceptance

- Clean isolated RC2 overlay included source binding #144, output integrity
  #151, Jev observer #152 and workbench auth fixture #153, with no
  collisions among the production source paths.
- **191/191 Python focused tests passed** plus **5 pytest subtests**
  across Mercury, source binding, output integrity and Jev receipts.
  The source-binding corrections remained fail-closed.
- **24/24 Node UI/provider-burn fixtures passed**.
- **21/21 synthetic Chromium browser checks passed** with loopback
  GET interception, at mobile/tablet/desktop widths; no production login.
- Python compilation and git whitespace checks passed.
- Test-only workbench auth positive/negative runtime acceptance remains
  unproven locally because the isolated Python env lacked LangGraph.
  Added a dedicated full-dependency CI step using synthetic
  offline credentials, so remote CI must prove the 200 and 401 checks.
- RC2 verification script and a scoped GitHub Actions workflow are included;
  statuses at publication are not presumptively green.
- All prior production NO-GO claims remain unchanged.

**Rollback:** remove this standalone RC2 worktree/branch. No process,
service, model, router, graph or NAS was modified by this overlay.

## Workbench-auth follow-up (October 8, 2026)

Independent PR #153 CI established that using configured credentials resolves
the original 401, but then rendered the wrong page (Fleet Control Room).
Source inspection proved that workbench.html inherited base.html without
defined Jinja content/head/scripts slots; the child markup was discarded.
PR #153's follow-up adds those Jinja slots without changing auth middleware,
keeping Control Room defaults, and suppressing control_room.js on workbench.
RC2 now includes the same source files and a standalone Jinja regression
that must run in local smoke and dedicated CI.
Wrong-password 401 and authenticated API 200 remain separate full-stack
CI checks; no local runtime execution was made.
