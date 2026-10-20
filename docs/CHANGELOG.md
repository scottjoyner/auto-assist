# AssistX documentation changelog

> **Status:** UNRELEASED DRAFT, not deployed. **Scope:** 2026-10-14 cross-system source, auth, routing and gate reconciliation. **Source commit:** current GitHub main 6824a55fa89e0670c402d289421e44a15ef927ae; historical local 567ea82a63894c0340d44d23c853500d47fafa1b. **Last verified:** 2026-10-14. **Owner/reader:** AssistX release/security owner. **Evidence links:** [candidate packet](RELEASE_CANDIDATE_EVIDENCE_20261014.md), [central hub](https://github.com/scottjoyner/knowledge/blob/docs/release-evidence-20261014/20-Projects/auto-suite/README.md). **Known gaps:** deployed revision and custody.

## Unreleased — 2026-10-14 documentation candidate

- **Documented source:** authenticated mobile catalog and model-handle-gated chat in local main. **Reconciled stale-checkout failure:** historical local 20/22 passed with missing exception alias; isolated current GitHub main includes alias and **26/26 focused offline tests pass**. Deployed behavior remains unverified. No newly deployed capability asserted.
- **Merged but not verified deployed:** #338 source-tree reconciliation; credential custody/rotation #156 unresolved.
- **Open draft / not enabled:** #294 graph bypass, #361/#365 deny-only routing/shadow receipts, #363/#366 co-restore and uncertainty witnesses.
- **NO-GO:** B01/B02/B06, source/secret custody, replay high-water, exclusive broker, physical native routing and iOS 1.7 release proof.

### Upgrade/rollback

No routine upgrade is approved. First verify exact deployed source and custody without exposing secret values; independently close graph/resource and router gates; preserve replay/high-water and prior runtime snapshot for rollback. See [candidate packet](RELEASE_CANDIDATE_EVIDENCE_20261014.md) for proposed order and evidence requirements.
