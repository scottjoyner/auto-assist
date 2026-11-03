# K2 physical-witness collection stage triage — November 3, 2026

Draft research-only supplement to the x1 K2 physical-witness observer.
No independent host administration, signer, model load, route or lease
change. Production admission remains HOLD.

## Verified live x1-370 observations

At approximately 07:48–07:52 EST November 3, the existing
read-only physical observer (with --skip-model-sha) reported:
- LM Studio installed application version 0.4.28+2; the reviewed
  version policy is still 0.4.26+4. Do NOT silently update that pin.
- The service root process is currently visible, with a current
  kernel boot digest and process start ticks. Historical fixed PIDs
  must never be reused as proof of current service identity.
- Two separately configured socat proxy processes are visible in
  the process table. This is NOT proof of their socket ownership.
- All loopback, LAN and Tailscale private API endpoints returned
  HTTP 200 / no loaded K2 models.
- Precise FIRST failing physical-evidence stage:
  runtime_executable_link, error type PermissionError.
- With --skip-model-sha, the current model file digest is
  intentionally UNVERIFIED by this probe. Prior full-file hash results
  must not be misrepresented as a new measurement.
- The physical socket chain and mapped GGUF remain unverified,
  model_loaded_physically_verified=false, can_dispatch=false.
- A fresh read-only canonical graph check still found generation 896
  expired, missing all current eligible runtime/model/access/capacity
  records, and gate=HOLD.

The observer was not permitted to read the actual running executable
link under the present Linux /proc security boundary. An exact
process command line, running service, or listening port cannot
substitute for that proof.

## Implemented source-only diagnostic refinement

The existing local physical observer now attaches a fixed,
public-label collection_error_stage to the PROCESS object when
a process/socket/model collection operation fails. It also retains
collection_error_kind and the original
process_identity_or_socket_unverified HOLD reason. It does not
include raw exceptions, file-descriptor identifiers, private
paths, process command lines or environment variables.

Potential diagnostic labels include:
- runtime_process_inventory
- boot_identity
- runtime_executable_link, runtime_executable_hash
- installed_executable_hash
- listener_inventory, runtime_listener_ownership
- proxy_lan_executable_link / proxy_tailscale_executable_link
- proxy_lan_executable_hash / proxy_tailscale_executable_hash
- proxy_lan_listener_ownership / proxy_tailscale_listener_ownership
- mapped_model_inventory

The new field is only emitted after a failure; a successful physical
chain does not carry a failure-stage marker. The independently pinned
signed-witness verifier now refuses EITHER
collection_error_kind or collection_error_stage, even if a
synthetic signed payload improperly claims all required booleans true.

The tests inject failures at the real executable link, both transport
proxy executable links, the runtime listener ownership probe and the
GGUF memory-map read. They prove no sensitive exception information is
returned. Socket-ownership verification is retained if it genuinely
completed before a separate GGUF memory-map step fails, but actual
model residency and dispatch remain denied.

## Required next qualified intervention

1. An authorized Linux/runtime operator should separately review the
   actual installed LM Studio binary and version, /proc security
   policy, and whether a least-privileged external attestation agent
   can lawfully obtain running executable/socket/GGUF mapping facts.
   Do not loosen hidepid, ptrace policy, kernel or systemd controls
   merely to make the witness report green.
2. Independently qualify the exact running executable digest and
   reviewed LM Studio build with an isolated 64k/four-slot load/
   inference workload before any proposed pin change.
3. Verify running model residency and socket inode ownership on all
   three private paths from the actual live process under approved
   operating permissions, with independently signed operator custody.
4. Reconcile the real challenge issuer/high-water with an externally
   administered anti-rollback anchor; obtain fresh operator-signed
   canonical compare-and-swap approval.
5. ONLY after those gates are independently validated should a
   separate, approved controlled production admission be considered.

This PR is neither a new physical witness nor a production release.
It does not change any model, process, service, signing key, graph,
archive or production router state.
