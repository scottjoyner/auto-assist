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
