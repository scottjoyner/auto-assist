# AssistX private Tailnet services homepage (October 9, 2026)

This is an **already deployed** navigation-only page on x1-370. The source is
versioned here so that the deployed local file is not the only custody copy.
It does not replace or expose the AssistX API or production Kipnerter API.

## Live acceptance

- URL: `https://x1-370.tailcb8954.ts.net:8443/` **tailnet only**.
- Existing Tailscale Serve root maps to `http://127.0.0.1:8081`; it returned
  HTTP 502 before the missing homepage service was installed and HTTP 200 after.
- Existing `/health` route, proxying AssistX port 8000, stayed HTTP 200.
- `systemctl --user is-active assistx-tailnet-home.service` => active.
- `GET /healthz` returned AssistX online and Kipnerter **test** API online.
- Only private loopback port 8081 is opened. No public Funnel changes were made.
- Kipnerter production node `kipnerter-prod-01-1` is listed as **TLS blocked**:
  HTTP redirects to HTTPS, but HTTPS alerts with a TLS internal error. SSH to
  that tagged node is denied by current Tailnet policy. Do **not** label healthy.
- This page is a link directory, **not an arbitrary path proxy**. Existing
  individual AssistX API paths continue to require their existing app auth.

## Machine paths and service scope

- Deployed source: `/home/scott/git/assistx-tailnet-home/server.py`
- Deployed unit: `/home/scott/git/assistx-tailnet-home/assistx-tailnet-home.service`
- Enabled by `systemctl --user link ...` and `systemctl --user enable --now ...`.
- No port/Funnel changes were necessary: 8443 already routed root to 8081.
- Code is stdlib Python and uses an allowlist of target URLs/health probes.
- Headers: Content-Security-Policy, no-store, no-referrer, nosniff, frame deny.
- The private directory uses the existing Tailscale ACL, not independent
  per-user SSO; this is appropriate **only for non-sensitive service links**.
  Never add arbitrary API credential forwarding, tokens or unreviewed proxies.

## Safe health and rollback

```bash
systemctl --user status assistx-tailnet-home.service --no-pager
curl -fsS http://127.0.0.1:8081/healthz
curl -fsSI https://x1-370.tailcb8954.ts.net:8443/
curl -fsS -o /dev/null -w '%{http_code}\n' \
  https://x1-370.tailcb8954.ts.net:8443/health
```

For rollback, `systemctl --user disable --now assistx-tailnet-home.service`
stops only this homepage. The previous 8443 root will again return 502 unless
mapped elsewhere; authenticated /health and API routes stay intact. Do not
reset Tailscale Serve, stop AssistX or change existing Funnel configuration.

## Hard out-of-scope gates

The observed AssistX production API on x1-370 was healthy, but its current
Compose worktree `auto-assist-wt-ab3bb64` has **no Git metadata**, and
`deploy/reconciliation/migration-state.yaml` has `cutover.recommended=false`
with production backup, client switch, restart, command and restore approvals
missing. **Do not replace the live API containers** based on this navigation
change. A side-by-side, fenced latest-source canary requires separate release
evidence, rather than reusing production Neo4j or NAS5.

The Kipnerter iOS source PR #358 passed latest-head CI and an isolated
**synthetic physical Neo4j transaction** test, but physical signed-iPhone,
Core Location, Wi-Fi/RuView and DB-enforced read-only grants remain unproven.
The paired iPhone 12 Pro Max is developer-locked (developer disk image mount
error); phone must be unlocked before app installation/launch is possible.
