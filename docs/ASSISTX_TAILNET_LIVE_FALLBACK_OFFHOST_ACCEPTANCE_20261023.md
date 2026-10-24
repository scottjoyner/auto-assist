# Live Tailnet sanitized fallback — independent off-host acceptance witness

**October 23, 2026, ~22:30 Eastern | ingress P0 #149 | draft auto-assist PR #425**

## Operational state observed (not initiated by this canary)

During a read-only preflight, the production ingress changed under a separate concurrent operation. The old `control-room-ts-proxy.service` became **inactive and disabled** around 22:26:49 Eastern, while `assistx-tailnet-sanitizer-fallback.service` became **active and boot-enabled** around 22:26:52. The primary `assistx-tailnet-sanitizer.service` remained inactive and disabled. The investigator's separate draft cutover script stopped at its initial state check and did **not** start or stop any production service.

A live kernel socket listing attributed the unique Tailnet IPv4 TCP:8000 listener to UID 1000, a positive socket inode, and the exact user-systemd cgroup for the **fallback Caddy service**; the localhost 127.0.0.1:8000 listener remains owned by Docker. Both the real on-disk fallback Caddyfile and Caddy binary match their pinned SHA-256 release digests. Process argv, `systemctl --user show ExecStart`, UID, listening cgroup, and config hash were verified together. Linux did not allow the research process to follow `/proc/<pid>/exe` for this existing Caddy process; no elevated permission or trust bypass was introduced.

## Actual live Lenovo-originated acceptance

`tests/assistx_tailnet_live_offhost_acceptance_canary.py --physical-live-readonly` provides a new **opt-in read-only** test, separate from the earlier disposable high-port canary. It **refuses to open a live Tailnet:8000 SSH SOCKS connection until** the old raw socat is verified inactive and not boot-enabled, precisely one of the pinned Caddy user services is active/boot-enabled, that service owns the Tailnet socket by kernel cgroup, the binary/config hashes match, and the five mobile Tailscale Serve routes retain their exact allowed scope.

After the gate, x1-370 establishes a batch-only pinned-host-key SSH dynamic SOCKS tunnel using Lenovo as a TCP relay. The existing production Basic username/password are read **only into transient x1-370 process memory** from the local AssistX container metadata; they are never written to Lenovo, logs, GitHub, test files or tool output. The test permits only GET `/health` and GET `/control-room` to the **verified live Caddy port 8000** and reads only the HTTP status line, not protected response bodies. All forged identity values are synthetic.

The first run of the new helper correctly denied the connection because it reused a *disposable-only* SOCKS library that forbids production port 8000; that earlier guard remains untouched. The revised helper uses a different production-only SOCKS function that requires the proved Caddy socket gate. The next run returned HTTP 307 for `/command-center` because that URL redirects to the canonical `/control-room`. A read-only, no-redirect local check established this target; the final harness restricts itself to the canonical URL and **never follows arbitrary redirects**.

The final physical acceptance, independently rerun, passed:

| Read-only request | Result |
| --- | --- |
| Lenovo-originated GET `/health` | **HTTP 200** |
| Anonymous Lenovo-originated GET `/control-room` | **HTTP 401** |
| Synthetic forged `Tailscale-User-Login` / forwarded identity headers | **HTTP 401** |
| Real configured Basic auth, no client identity header | **HTTP 200** |
| Real configured Basic auth plus synthetic forged identity header | **HTTP 200** |

The final test also rechecked exact running Caddy identity and the original Hermes image/mount count. **No production task mutation, model call, protected response-body read, Docker restart, Tailscale Serve update or service change was initiated by the acceptance harness.** Its 15 focused offline regressions passed (default inert, production socket denied before proof, only approved read-only paths, no credentials/logging, negative mismatched-authorization unit case), and Ruff lint passed.

## Readiness and remaining HOLDs

The **Tailnet raw-header-forwarding risk is now contained on the observed live port 8000** by a pinned header-stripping Caddy fallback, and external Basic clients still work. This is **not proof** that all possible endpoints, callers, reconnecting streams or route-specific Tailscale Serve identity paths are secure. The existing `assistx_tailnet_postcutover_attestation.py` checks specifically for the *primary* Caddy unit and still reports a static HOLD when the **fallback** legitimately owns the socket; the new fallback-specific witness supplies the actual off-host evidence without misrepresenting that primary-only assessment as PASS.

Before closing ingress issue #149, qualify persistence across reboot, all authorized client workflows, release-safe promotion of the primary vs pinned fallback, protected route authorization including non-Basic clients, and direct localhost/Serve trust boundaries. Independent signed owner and revocation custody, durable high-water protection, real two-owner task receipts, and the unsafe 16 production Hermes mounts are **separate P0 #414 NO-GO requirements**. Keep Hermes deployment on HOLD, and do not reactivate raw socat as rollback.
