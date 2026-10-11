# Direct llama-server Fleet admission preflight — October 11, 2026

**Status: a READ-ONLY security / provenance gate. Default DENY. No runtime, firewall, load, admission, data custody or canonical-approval changes.**

Standalone [P0 security issue #319](https://github.com/scottjoyner/auto-assist/issues/319) was opened after a direct, independently managed `llama-server` was discovered on a fleet machine. The listener is not loopback-only and its read-only model metadata endpoints are reachable without credentials from another authorized Tailnet node. This is **Tailnet exposure, NOT a demonstrated public Internet exposure**. Precise host IP, listener address, raw command line, model path and keys are intentionally absent from the published security report.

This finding is separate from the prior LM Studio gateway and independently hashed worker in [P0 request-affinity issue #313](https://github.com/scottjoyner/auto-assist/issues/313). The directly managed server has a **different ~84 MB model**, not the approved LM Studio child-worker's approximately 1.25 GB GGUF. Treating these distinct processes as the same model or as mutually interchangeable would invalidate model and worker provenance.

## Actual physical Fleet Commander evidence (read only)

- A standalone `llama-server` process was observed with parent PID 1 and a Linux start-tick counter distinct from the LM Studio worker. The model file is a regular nonsymlink GGUF, **84,106,624 bytes**.
- The actual kernel `/proc/net/tcp` **LISTEN** socket inode was matched to a `/proc/<pid>/fd/socket:[inode]` descriptor owned by that process. Its binding scope is **wildcard / non-loopback**.
- The observed process command did not contain an explicit `--api-key` or `--api-key-file` flag. This *does not prove* no other authentication mechanism exists.
- From another authorized Tailnet node, `GET /health`, `GET /props`, and `GET /v1/models` each returned **HTTP 200 without a supplied credential**. `/props` includes a `model_path` metadata field; the raw path was neither copied into this report nor saved.
- No inference/chat/completion was invoked, no prompt or worker logs inspected, and no configuration or model file modified.
- The exact committed physical read-only collector `scripts/audit_direct_llama_listener.py` was streamed over the already configured authorized SSH connection as `python3 -`. It rechecked boot UUID, PID start tick, argv bytes, exact listener owner, and model file identity before/after hashing, returning `BLOCKED_NON_LOOPBACK_DIRECT_LISTENER`. **Exit code 1 is intentional for this security-denied audit**, not a runtime crash.
- The observed model digest was generated only as a self-observation. **No independently approved/pinned GGUF digest was supplied**, so `model_digest_independently_pinned=false` and `model_expected_digest_match=null`. The audit always says `api_authentication_proven=false`, `signed_source_provenance=false`, `independent_worker_execution_attested=false`, `can_approve=false`, `can_renew=false`, `can_dispatch=false`.

The collector does NOT print raw model path or command line, private addresses, API keys, boot UUID, user content or any credentials. It emits a short hashed boot-UUID prefix and the observed model digest; avoid publishing even this observed digest into wide-access logs if local model inventory is sensitive.

## Reproducible read-only audit

Use an approved local shell on the actual host, or stream the exact reviewed source from a trusted controller without writing files on the host. Supply a physical PID and declared port from an **independently reviewed host inventory**, not user-supplied runtime claims:

```bash
python3 scripts/audit_direct_llama_listener.py \
    --pid "$PHYSICAL_PID" --port "$LOCAL_PORT" \
    --expected-model-sha256 "$INDEPENDENT_APPROVED_GGUF_SHA256"
```

Without an approved hash, omit `--expected-model-sha256` and the observer will explicitly mark the model **unpinned**. The exit code is **1 even for an otherwise fully loopback-local process**, because no authentication, process execution, owner, signer, or approval has been proven. Exit 2 indicates malformed/inconsistent evidence or an unreadable host. The script does not call HTTP endpoints, connect over Tailnet, log raw model text, use model SDKs, or write to Neo4j.

### Properties checked

1. The exact PID's argv identifies `llama-server` and exactly one regular, absolute and nonsymlink GGUF path; the model file must be ≤2 GiB and can be hashed without exceeding a bounded disk-read budget.
2. The process has one declared `--port` consistent with the operator-selected target; its kernel TCP listening inode is physically owned by that PID. Mere presence of a port or `/health` is insufficient.
3. Actual socket binding **and** declared `--host` must both be exclusively loopback before a future separately authenticated-gateway candidacy can be considered. Wildcard or Tailnet/LAN binds produce **BLOCKED_NON_LOOPBACK_DIRECT_LISTENER**.
4. Linux boot UUID, PID start ticks, process argv bytes, socket ownership, and GGUF device/inode/mtime/size are rechecked across hashing to detect restart or model swap.
5. CLI API-key flag presence is an *observation only*, **not proof** of configured enforcement. Never inspect/reveal the argument value or assume that an authenticated proxy exists.
6. **Every output** denies model-execution attestation, owner receipts, operator approval, source-signer provenance, canonical reapproval, and direct dispatch. A loopback bind alone also remains denied.

## Read-only cron/watchdog persistence finding (2026-10-11)

Fleet Commander inspected the live Beelink process and its current owner crontab without changing it:

- Physical direct server belongs to non-root account scott, parent PID 1, process cgroup **cron.service**. It is not registered as a standalone llama-server systemd service.
- The owner's crontab has **five enabled entries**. The matching **embedding-server-persist.sh** is invoked both **@reboot** and **periodically**. Its bounded script content mentions the direct model server and the exposed listener port. A separate inference-server-persist.sh has independent cron entries.
- This is strong evidence of an unattended relaunch/watchdog path, **not conclusive proof of the exact invocation that started today's PID**. A listener-only change could be reverted by the periodic job or next reboot.
- At the instant sampled, there were **zero established TCP sockets** and one LISTEN socket associated with the direct server. Intermittent consumers remain unknown.
- A localhost literal in the launcher is **not proof of a loopback listener**: the observed kernel listening socket is wildcard-bound. It may be a local health-check URL.

New bounded read-only tool **scripts/audit_direct_llama_startup.py** inspects only the current user's crontab and small, directly referenced nonsymlink scripts under that user's home directory. It never prints raw cron lines, command arguments, paths, secrets, HTTP payloads or source code, and never starts/stops jobs or touches cron configuration. The physical audit returned **BLOCKED_CRON_PERSISTENCE_CANDIDATE**, with one matching reboot and one matching periodic entry; no permission to mutate, approve or dispatch. Its exit code 1 is intentional default DENY.

### Coupled operator-approved containment gates

1. Review short-lived consumers, embedding service ownership, existing Tailnet ACLs, and both cron launchers before changing any listener; zero observed ESTABLISHED sockets is not a dependency inventory.
2. Review the watchdog script **and recurring cron entry** alongside the active process. Any later listener hardening must survive watchdog execution and reboot; do not change just the live command line.
3. In an authorized reversible change window, prefer loopback binding behind an authenticated gateway or a narrowly justified ACL and server authentication. Preserve rollback for the embedding consumer and the unrelated inference service.
4. After authorized implementation, run negative unauthenticated Tailnet GET checks, positive authenticated consumer checks, and persistence regression after a cron iteration/reboot-equivalent test.
5. Keep all signed source/owner admissions OFF until physical request execution provenance, independent signer custody, DBA-approved three Neo4j identity constraints and a newly operator-approved canonical generation exist.

**Test evidence:** 15 new synthetic cron-persistence/security tests in tests/test_direct_llama_cron_persistence.py plus 17 existing physical listener audit tests = **32 source-only tests**. This is a preflight, **not a deployed mitigation**.
## Ordered remediation gates — no automatic live mutation

1. **Operator-owned dependency audit:** identify who owns the independent direct server, which local services depend on it and why the listener is non-loopback. Review affected client/service behavior; do not disconnect it blindly.
2. **Contain unnecessary exposure:** prefer a reviewed loopback bind and an independently authenticated gateway, or enforce narrow host firewall/Tailnet ACLs and server-level authorization. Do **not** assume Tailnet membership equals per-user authorization. Do not change network/daemon settings without an authorized change window and rollback plan.
3. **Negative access testing after authorized change:** unauthenticated Tailnet HTTP requests must be denied; intended authenticated local and remote workflows must still pass. Read-only `/props` should not divulge unnecessary server filesystem paths to unauthenticated callers.
4. **Pin and independently review the exact model:** compare approved full SHA256, byte size, quantization, process boot/start, `/props`, runtime identity, memory/capacity state and strict offline restrictions. This very small model is not the previously approved 1.2B chat model.
5. **Real request-level trust:** implement an independently trusted worker-specific request-completion producer, legitimate signer custody and revocation, authenticated owner-binding and an atomic replay ledger. A GET or matching hash is not execution attestation.
6. **Separate production release requirements:** DBA-approved three Neo4j uniqueness constraints (`FleetProjectionState.name`, `RuntimeProjectionApprovalReceipt.approval_id`, `RuntimeInstance.runtime_instance_id`), inherited full AssistX CI repairs, a newly operator-reviewed signed canonical generation (prior 643 expired), [Auto-Router P0 #37](https://github.com/scottjoyner/auto-router/issues/37) and admin-auth compatibility.

**This is a standalone research/security draft against `main`, not a production mitigation.** It does not replace independent operational access control, source signing, or physical acceptance.
