# Two-node trace execution shadow acceptance — xwing and MacBook Air

**Date decided/tested:** 2026-10-08 America/New_York.
**Purpose:** Establish independent trace-first synthetic execution paths before any real routing or command execution.
**Decision-maker:** User requested adding xwing and MacBook Air, agent implemented shadow-only canaries.
**Decision:** Keep the production AssistX allocator and auto-router unchanged. Stage a fixed no-op/echo adapter on both nodes, using separately owned release directories and journals. Configure explicit node selection in a shadow-only registry.
**Reopen:** A live-command decision requires authenticated, currently valid AssistX claim/lease fencing, durable cross-node archive, observation-only monitoring, expiry/revocation canaries, and operator approval.

## Hypothesis and prediction (prospective for a future repeated run)

**Hypothesis:** The two physical nodes can persist cryptographically linked per-attempt receipts independently, with node identity and node path fixed by a shadow config, without exposing general remote shell execution.

**Prediction:** Under existing host-key-pinned Tailscale SSH, a synthetic no-op on each node generates one `prepared` and one `completed` journal entry and a valid hash chain. Duplicate and wrong-target synthetic attempts are rejected without modifying either journal. Python 3.9 compatibility is required on macOS.

This hypothesis/prediction is a retrospective description of the completed exploratory canaries, **not** a preregistration of those already observed results. Retain it as the prediction for a repeated controlled experiment.

## Fixed path registry

`config/trace_execution_shadow_nodes.json` records exactly two **shadow-only**, disabled-for-live-dispatch placements. `scripts/trace_execution_shadow_control.py` accepts exact `--node` selection; it cannot discover new nodes, assign real tasks, submit arbitrary commands, or edit live router configuration. The SSH command is fixed to either read-only journal verification, synthetic no-op, or synthetic negative drills.

| Logical node identity | SSH target | Shadow code | Local journal |
| --- | --- | --- | --- |
| `xwing` | `xwing` | `/home/scott/.local/opt/assistx-trace-shadow/v1` | `/home/scott/.local/state/assistx/trace-execution-shadow-xwing/journal.jsonl` |
| `scotts-macbook-air` | `scottjoyner@100.85.64.117` | `/Users/scottjoyner/.local/opt/assistx-trace-shadow/v1` | `/Users/scottjoyner/.local/state/assistx/trace-execution-shadow-macbook-air/journal.jsonl` |

Neither node enables the production `FLEET_TRACE_PROBE_ENABLED` feature flag, installs a system service, changes DNS/network access, or enables `FLEET_UNSAFE_SHELL_TASKS_ENABLED`. No raw journal or keys were copied back to x1-370. Source files were transferred by existing SSH/SCP and checked against local source digests.

## Observed evidence

Tests were executed remotely through Fleet Commander from x1-370, via existing Tailscale/OpenSSH paths. No OpenTunnel server or endpoint is involved.

- **xwing:** Linux Python 3.12.3. Synthetic noop succeeded; persisted 2 records. Independent journal verification returned `ok:true` and head hash `54cbbdb84589aac87ab464b650ef483954f799a8b511d3d887387ab4b7527dc3`.
- **scotts-macbook-air:** macOS Python 3.9.6, SSH OS hostname `kipnerter`; logical node ID is **not** derived from the OS hostname. Synthetic noop succeeded; persisted 2 records. Independent verification returned `ok:true` and head hash `d02aa98b4139c464eb94ec335b4f35d0c9c095b7877257551902d8a376473d63`.
- **Both negative drills:** `duplicate_rejected:true`, `wrong_node_rejected:true`, `journal_unchanged:true`, `private_modes:true`; final record count unchanged at 2 on both nodes.
- Adapter SHA-256 on both machines: `b8b429c69771846ceaa91bd8a819246d89f2578ae87076b53e311dc5664acdd4`.
- Canary runner SHA-256 on both: `dc6f9fa4e55db5e7244cc3fa0fa7bc6e93eef0f192d178d251aec4b2a82dcb3b`.
- Local adapter, two-node registry and prior-fleet regression run: **45 tests passed in 0.16 seconds** after this registry/negative-runner slice. Compatibility-mode Ruff lint and Python compileall passed for new code.

These are local-fs SHA-256 chains; they are not signatures, centrally anchored tamper evidence, or proof of an actual AssistX-issued claim. Synthetic runner explicitly reports `assistx_claim_verified:false`.

## Reproduction (does not execute real work)

From the isolated review worktree:

```bash
cd /home/scott/git/wt-assistx-trace-execution-20261007
PYTHONPATH=src python3 -m pytest -q tests/test_trace_execution_adapter.py tests/test_trace_execution_shadow_paths.py tests/test_fleet_node_shell_gate.py tests/test_fleet_node_recovery.py tests/test_safe_fleet_executor.py tests/test_fleet_executor_concurrency.py
python3 scripts/trace_execution_shadow_control.py --config config/trace_execution_shadow_nodes.json --node xwing --verify-only
python3 scripts/trace_execution_shadow_control.py --config config/trace_execution_shadow_nodes.json --node scotts-macbook-air --verify-only
# Only after viewing and accepting the target-specific registry:
python3 scripts/trace_execution_shadow_control.py --config config/trace_execution_shadow_nodes.json --node xwing --run-synthetic-noop
python3 scripts/trace_execution_shadow_control.py --config config/trace_execution_shadow_nodes.json --node scotts-macbook-air --run-synthetic-noop
python3 scripts/trace_execution_shadow_control.py --config config/trace_execution_shadow_nodes.json --node xwing --run-negative-checks
python3 scripts/trace_execution_shadow_control.py --config config/trace_execution_shadow_nodes.json --node scotts-macbook-air --run-negative-checks
```

Use `--verify-only` for passive checks. The no-op mode appends a **new** synthetic pair, so the record count will increase on each repeated run.

## Next acceptance gates

1. Prove node-bound, authenticated live AssistX claim with expiry/revocation and stale-lease denial **before** enabling any task consumption. The current CLI uses synthetic identifiers and is not admissible as a real executor.
2. Add durable encrypted per-node journal backup, independent immutable signed head anchoring, and tested restore. Current journals are local and independent but lack remote retention.
3. Run controlled full-disk, fsync failure, crash-between-receipts, replay, and network-failure drills. A prepared-only attempt must not be automatically replayed.
4. Confirm no node service is auto-started and no shell payloads became routable. Keep router node selection under AssistX authority, transport path under explicit operator-approved policy.
5. Only then consider extending a typed command catalogue with per-node allowlists. OpenTunnel remains optional and not installed.

**Status:** Two independent synthetic shadow paths operational. **Live executor admission: NOT ACCEPTED.**
