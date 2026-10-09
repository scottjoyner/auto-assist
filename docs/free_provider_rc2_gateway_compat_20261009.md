# PR #229 free-provider mock on RC2 attested gateway integration — experimental acceptance

**Experiment:** `assistx-provider-gateway-compat-20261009`  
**Recorded:** October 9, 2026, approximately 16:08–16:15 America/New_York.  
**Research status:** DRAFT / NO PRODUCTION ADMISSION / no hosted generation.

## Exact source inputs

- Baseline: attested Tailnet gateway research draft PR #212, commit `0d3da9d26cb2c4e44703edf3a7e084f313329d47`; this already stacks on the signed-routing, dashboard and gateway-auth research foundations. This is a **draft integration stack**, not merged production main.
- Overlay: free-provider research draft PR #229, commit `af86b0f785b71730d84ed745547f9fdc840a535e`; copied the **four independently introduced paths** through GitHub from the exact source SHA, with no source/test modifications:
  - `.github/workflows/free-provider-mock-contract.yml`: source Git blob `706f34217bd26b17c883f3bbb4c972c8b6ec6337`
  - `docs/free_provider_mock_acceptance_20261009.md`: `3affe12f5a1c34a833b5b503a75b3d73422161d8`
  - `scripts/mock_free_provider_admission.py`: `43d31f9f48e79aec38ee92b7359d25761afc135a`
  - `tests/test_mock_free_provider_admission.py`: `ba86ec996ee5f1f49153c72d07dcbcd0d144fa95`
- The change is proposed **against PR #212's head branch**, so unrelated baseline modifications do not become part of the PR's changed-file set. Neither source branch was rebased, merged, or changed by this experiment.

## Hypothesis and controls

**Prediction:** PR #229's 66 offline negative-admission checks pass unchanged on the gateway stack, and the broad suite reverts to the latter's nearly-green release gate, with credential-custody failure still visible. Any unrelated host-installation check must be reported separately, not silently waived.

**Safety:** one detached disposable worktree on x1-370, Python 3.12 isolated `/tmp` venv installed from checked-in `requirements.txt` and `requirements-dev.txt`. No model provider calls, existing Kilo/OpenRouter credentials, production APIs, live Redis/Neo4j mutations, NAS copies/deletions, or remote workers. Tests excluded preexisting `integration`-marked test cases as the repository's actual CI command does.

## Observed on physical x1-370

- Source overlay: `git apply --check --index` **PASS**; exactly four new file paths (816 added lines); `git diff --cached --check` **PASS**.
- Focused `python3 -m pytest --noconftest -q tests/test_mock_free_provider_admission.py`: **66 passed** under installed Python 3.12 test dependencies (also 66 pass in original ambient environment).
- Broad, physical-host mode, with runner-only `GITHUB_ACTIONS` unset: **1,007 passed, 2 failed, 59 deselected**, 9 warnings. Failing checks:
  1. `tests/test_env_file_ignore_rules.py::test_no_live_env_variant_is_already_tracked`: tracked `.env.bak-sync-20261006T051130Z`, `.env.reconciliation.example`. This is a **real security-custody failure**, issue #156; do not delete, waive or alter tracked sensitive files in this PR.
  2. `tests/test_fleet_unification_report_contract.py::test_installed_transition_script_matches_managed_source`: script is absent from this **physical host's** `~/.hermes/scripts`; test intentionally requires a managed installed copy unless executing in hosted CI.
- Broad run with `GITHUB_ACTIONS=true` **only to simulate GitHub runner's fresh-home condition**, while retaining all security assertions: **1,008 passed, 1 failed, 59 deselected**, 9 warnings in 16.70 seconds. Sole failure: `test_no_live_env_variant_is_already_tracked`. Do not describe this as genuine GitHub CI until the exact-head workflow executes, or as proof the physical host's installed script is correct.
- Full run had an observed thread warning for a repository-source-binding controller loop lacking Neo4j credentials; this is a separate environment/cleanup signal and not reason to promote.
- Recovery-canary and true distributed provider tests **not** performed by the local broad-unit command; they remain separate CI/physical gates.

## Decisions and downstream acceptance

1. No compatibility file modifications needed for the present targeted mock contract; file overlap conflict is absent and all 66 checks pass on the stack.
2. Leave issue #156 security-custody assertion failing. Owner-controlled credential revocation/history cleanup remains P0. Other P0s: trusted ingress #149, trace read/revocation #148, signed issuer/gateway, genuine free-provider quota/cost custody and independent off-host audit.
3. Require **exact GitHub integration-head** focused provider check, broad CI, and recovery-canary; compare results against recorded local evidence. The new PR remains draft until independent review; even a fully green CI would not authorize production.
4. The PR is an intentionally narrow compatibility proof; merging any original stack remains a separate operator and reviewer decision.

**Source PRs:** https://github.com/scottjoyner/auto-assist/pull/229 ; https://github.com/scottjoyner/auto-assist/pull/212  
**Relevant blockers:** https://github.com/scottjoyner/auto-assist/issues/156 ; https://github.com/scottjoyner/auto-assist/issues/149 ; https://github.com/scottjoyner/auto-assist/issues/148
