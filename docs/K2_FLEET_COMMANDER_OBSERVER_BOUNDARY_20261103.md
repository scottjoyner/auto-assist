# K2 physical witness: Fleet Commander observer boundary, Nov 3 2026

RESEARCH ONLY / NO NEW PRODUCTION AUTHORITY.

## Actual measured context, x1-370, Nov 3 ~08:01–08:05 EST

The existing physical witness first fails at
runtime_executable_link / PermissionError. Read-only investigation
established that BOTH the inspected service and the Fleet Commander
reader have Unix UID 1000, but their SECURITY CONTEXT is different:

| Observation | Fleet Commander diagnostic | LM Studio service |
| --- | --- | --- |
| Linux UID | 1000 | 1000 |
| /proc/self or target uid_map | one mapping of UID 1000 to 1000 | host-wide 0 to 4294967295 |
| /proc attr/current | unprivileged_userns (enforce) | lm-studio (unconfined) |
| /proc executable link readable by diagnostic | YES (self) | NO (PermissionError) |
| /proc memory mapping readable by diagnostic | YES (self) | NO (PermissionError) |

Both separate LAN and Tailscale socat proxy processes are under UID
1000 and appear unconfined from their /proc/attr/current labels. Their
executable links are ALSO inaccessible from the Fleet Commander
diagnostic. A normal, disposable child process started in the SAME
diagnostic session has a readable executable link.

The host's /proc mount reports standard nonspecial flags, and Yama
ptrace_scope=1. Neither alone establishes the cause of the failure.
The combination of restricted single-UID namespace mapping and enforced
observer AppArmor profile is strong evidence of a cross-security-context
COLLECTION barrier. It does NOT prove that the host itself cannot
inspect the service, that changing ptrace_scope would help, that
AppArmor alone caused denial, or that the target process is dumpable.

LM Studio remains installed at 0.4.28+2 while the reviewed source
policy remains 0.4.26+4; all three private K2 catalogs return ZERO
loaded models. Signed canonical generation 896 remains expired and
missing eligible runtime, model, access and capacity records.

## Repeatable non-admitting diagnosis

Run:

    python3 -B scripts/inspect-k2-observer-namespace-readonly.py

The diagnostic discovers LM Studio using the previous exact
process-tree matching code, reads bounded public /proc metadata
from the CURRENT observer and service, compares mapping STRUCTURES
without printing the mappings, reports coarse AppArmor modes and
readlink readability, and returns exit code 2 regardless of results.

It never prints PID, command line, raw security profile, path/FD
target, namespace map, executable hash, hardware ID, or error text.
If any required metadata is missing or malformed, it fails closed.

Permitted result statements:
- observer_target_userns_maps_differ=true is an observation about
  MAP CONTENT, not proof of namespace ancestry or trustworthiness.
- observer_apparmor_mode=ENFORCED_REPORTED means a profile marked
  enforce, not that the exact audit denial cause has been proven.
- runtime_executable_link=DENIED_OR_UNAVAILABLE means no actual
  running image has been inspected; no executable approval is granted.
- root_cause_proven=false, independent_custody_verified=false,
  physical_process_socket_chain_verified=false,
  actual_model_residency_verified=false,
  canonical_operator_approval_verified=false, can_dispatch=false.

A physically healthy kernel-protected host can correctly refuse
cross-namespace process inspection. Preserve that isolation.

## Required intervention — authorized operator path

1. DO NOT attempt nsenter, privileged /proc mounts, Yama changes,
   AppArmor bypass, ptrace injection, forced core dumps, service
   restarts, elevated Fleet Commander tool execution or permissions
   changes to make a research probe green.
2. Under an explicitly approved maintenance/security review, determine
   which host-local operator already has lawful read-only visibility
   into the actual running executable, socket owners and mapped GGUF.
   Validate minimal permissions and do not share host credentials.
3. Have the separately authorized operator attest process image
   version/hash, kernel boot, runtime+proxy PID/start-ticks, socket
   inodes, mapped model, and all private model catalog paths, with
   independently approved signing/custody and fresh challenge.
4. Independently qualify the installed LM Studio version mismatch.
   Loading a real K2 model or generating inference under test is a
   separate isolated, resource-bounded qualification request; do
   not use a production listener or renew canonical leases.
5. Independently provision issuer/CAS/anti-rollback keys and obtain a
   fresh signed canonical generation ONLY after approved evidence.

This PR is NOT a signer, independent host attestor or authorization
to change production state. It modifies no security policy, process,
model, signing key, deployment, graph, data or router.

## Test evidence

26 synthetic tests cover a different user namespace mapping despite
same UID, fake executable readlink denial without path disclosure,
same-map trust refusal, AppArmor category handling, malformed maps,
missing PIDs, overlong status, absent AppArmor label, invalid UID,
and permanent HOLD/exit 2. They run with the prior 207 K2
witness/issuer/rollback/custody tests: 233 total.
