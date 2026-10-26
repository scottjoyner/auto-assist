# Beelink recovery memory guard — read-only entrypoint preflight

Tracking: [Auto-Assist issue #446](https://github.com/scottjoyner/auto-assist/issues/446).

The October 26 physical Beelink systemd readback found
`assistx-recovery-memory-guard.service` still failed, Python exit 2,
with a journal indication that the configured guard script cannot be
opened. The configured `WorkingDirectory` is **protected**; the
non-root Fleet Commander identity cannot inspect it. Memory headroom
alone cannot classify a guard execution refusal versus a missing source
file, and no root privilege was requested or obtained.

## What this standalone tool does

`scripts/beelink_recovery_guard_entrypoint_preflight.py` invokes only
`systemctl show` for the exact allowlisted
`assistx-recovery-memory-guard.service`, retrieving **ExecStart,
WorkingDirectory and User** (never Environment or EnvironmentFiles).
It parses only the configured Python script name and root-relative
or WorkingDirectory-relative identity. It inspects metadata, file
modes and symlink status; it **never reads script content, executes
the script, invokes systemctl start/reset-failed, changes permissions,
opens credentials, restarts the recovery island, or scans the NAS**.

The output contains safe fixed reason codes, the allowlisted unit and
script basenames, and explicit `false` for memory admission,
source commit verification, deployment authorization and service restart.

Possible HOLD reasons include:

- `PROTECTED_PATH_UNVERIFIABLE` — the caller cannot inspect the
  protected checkout; **NOT proof that the entrypoint is missing**.
- `ENTRYPOINT_PATH_MISSING` — missing to the executing identity.
- `SYMLINKED_ENTRYPOINT_PATH`, `ENTRYPOINT_NOT_REGULAR_FILE`,
  `ENTRYPOINT_GROUP_OR_WORLD_WRITABLE`.
- `unexpected_launcher`, `unexpected_script_identity`,
  `invalid_working_directory`, `parent_traversal_refused`,
  or other denied malformed unit metadata.

`ENTRYPOINT_METADATA_ONLY` (exit 0) proves only that this caller
can see a regular readable Python entrypoint. It is **not** a
healthy memory guard, safe identity/trust chain or release permit.
Any HOLD returns exit 2.

## Physical acceptance

The same exact reviewed GitHub blob was tested on x1-370 and Xwing,
**16/16 isolated stdlib tests PASS on each**. Negative tests include
a missing file, symlinked path, directory substitution, traversal,
world-writable source, an unexpected executable/script and malformed
unit properties. CLI tests assert no full private checkout path is
printed and no service action is issued.

The source blob was streamed over strict-host-key SSH to nonroot Beelink
Python, without creating a file. It returned
`HOLD: PROTECTED_PATH_UNVERIFIABLE`, **exit 2**. Unit remained failed,
ExecMainStatus 2, and active recovery island was not restarted.
This is deliberately not promoted to `ENTRYPOINT_PATH_MISSING`
based on a nonroot inaccessible directory.

## Owner-required closeout

Authorized recovery owner must independently verify the configured
working directory and Python entrypoint through the correct privileged
owner channel, compare against a reviewed source commit/manifest,
classify its observed Python exit 2 accurately, then repair just the
pinpointed unit/source mismatch, run isolated negative admission tests,
and read back the actual service and cgroup state. The caller must
not change memory guard thresholds, bypass spend/router auth,
launch models, enable automatic retry, or clear the service failure
as a cosmetic fix.

**This commit does not fix or deploy the Beelink guard.** It provides a
reproducible and safe reason-code gate for that separately authorized
recovery.
