# AssistX trace-page provider burn compatibility release — October 8, 2026

## Deployment boundary
The active x1-370 AssistX container is `assistx-api` from the **non-Git recovery snapshot** `/home/scott/git/auto-assist-wt-ab3bb64`. Its Python source and HTML templates are bind-mounted; its static JS is baked into an older image. Its API router retains `install_live_executor_claims` and mobile-route registration that differ materially from the current Git feature branch. Replacing the complete API router, HTML shell, or Docker image without reconciling these differences is unsafe.

This interim release uses **only an authenticated Jinja template include** on the existing `/traces` route. No backend routes, agent behavior, executor state, admission, or provider keys are changed. The richer source-wired `/provider-usage` and `/api/fleet/provider-burn` remain staged for a reviewed image release, not active in this recovery snapshot.

## Live template implementation
- A host-backed `templates/provider_burn_panel.html` is a pre-rendered **HTML fragment**, generated from allowlisted provider telemetry by `scripts/render_provider_burn_fragment.py`.
- Existing `templates/traces.html` has just one additive line after the `Trace history` heading:
  `{% include "provider_burn_panel.html" ignore missing %}`
- Its original SHA-256 was `4d58baa367f7f231eeb3b87e489ae74c4e474a85729af15bfae5a6f251502a21`; immutable private backup: `/home/scott/.local/state/auto-assist/provider-burn-deploy/traces-before-20261008.html`.
- Additive template include is **inside the existing authenticated `/traces` page**; avoid linking or serving fragment contents on an unauthenticated route.
- Restoring the original template atomically and removing the now-unreferenced fragment rolls back the visible change. It does not require a container restart.

## Pinned collection and refresh
- `scripts/refresh_assistx_free_burn.sh` performs one read-only OpenCode step-finish aggregation, publishes a private JSON snapshot into the preexisting AssistX artifacts bind mount, atomically replaces the Jinja fragment, and appends a compact, hashed observation receipt to a local append-only JSONL file.
- Concurrent refresh processes are excluded using `flock`. It never queries provider APIs, sends generation requests, touches model leases or modifies deployment-wide router authority.
- Scoped databases: account and Fleet Commander isolated OpenCode SQLite. `complete` covers only these two local sources, **not fleet-wide coverage**, and cannot establish vendor quotas or physical upstream groups.
- Outputs and observation ledger are `0600`. Retention is append-only: do not trim underlying trace history to control disk pressure.
- UI forecasts are flat-pace repeats of observed 24h usage, **not** verified billing or free-tier allowance.


## Live acceptance observations — 2026-10-08 ~08:05–08:11 EDT
- Applied only one Jinja include to the deployed `traces.html` and one navigation link to deployed `base.html`. The exact reproducible diffs are in `deploy/assistx-burn-compat/traces-panel.patch` and `base-nav.patch`. Original templates remain preserved privately under `~/.local/state/auto-assist/provider-burn-deploy/*-before-20261008.html`.
- Pinned collector and renderer to `/home/scott/.local/lib/fleet/assistx-provider-burn-20261008` with `SOURCE-REVISION` and `SHA256SUMS`; no execution depends on the disposable development branch.
- Enabled user timer `fleet-provider-burn-refresh.timer` every 15 minutes; `fleet-provider-burn-refresh.service` manually invoked after installation, `Result=success`, `ExecMainStatus=0`. First scheduled recurrence was pending for 08:15 EDT when checked.
- The live collector snapshot is `/home/scott/git/auto-assist-wt-ab3bb64/artifacts/free-endpoint-burn.json` (private mode `0600`); the generated fragment is `/home/scott/git/auto-assist-wt-ab3bb64/templates/provider_burn_panel.html` (mode `0600`). Historical compact receipts are appended with hashes to `~/.local/state/auto-assist/provider-burn-deploy/observations.jsonl`, mode `0600`.
- Manual service refresh produced 350 local sessions and 35,630 timestamped step events with scoped source reads complete. Snapshot at ~08:08 EDT: 28,958,972 reported tokens in trailing 24h, zero in trailing hour, 7,381,152,763 in trailing seven days. These are reported *model-step tokens including cache*, not upstream billing.
- Confirmed from INSIDE the running Docker container: the deployed Jinja trace viewer includes the new panel and original trace list; the base shell contains the new navigation link. Container-local authenticated HTTP GET returned **200** for both `/traces` (panel and trace list present) and `/control-room` (navigation link present). Credentials were not printed or exported.
- An unauthenticated `/traces` request returned **401** and `/health` returned **200**. The original `assistx-api` container remained healthy and its original startup timestamp was unchanged; live API router hash remained `423d9127a20a3eacf24f8b5f7a98765bf856d1d18a7f800a32c2e7fec9bb3d51`.
- The richer `/provider-usage` page and `/api/fleet/provider-burn` from the feature branch remain **not deployed**; the operational view is `/traces#provider-burn` only.

## Reversibility and scope
The patch is exactly one line per existing template. To roll back after source-hash review, atomically restore the two `*-before-20261008.html` originals; stop/disable the **new** `fleet-provider-burn-refresh.timer` only, leaving existing provider monitoring and trace harvesting timers untouched. Then retire the unreferenced generated fragment. Do not delete the append-only historical ledger.

This acceptance only proves authenticated *reporting* integration and scheduled read-only refresh; no official provider quota, shared upstream budget, inference entitlement, accepted autonomous deliverable, production routing, or cross-node lease is claimed.
