# Beelink recovery memory-guard installation preflight (HOLD)

Parent: [Auto-Assist #446](https://github.com/scottjoyner/auto-assist/issues/446).
NAS custody is separately owned by [knowledge #95](https://github.com/scottjoyner/knowledge/issues/95) and [#173](https://github.com/scottjoyner/knowledge/issues/173).

## Physical symptom and what is **not** yet known

On October 25, 2026, Beelink's dedicated
`assistx-recovery-memory-guard.service` reported `ExecMainStatus=2`.
Its exact-time journal had Python's "can't open file"/"No such file"
category, and no guard JSON evaluation receipt or Python application
traceback. The current checked-in
`scripts/recovery-memory-guard.py` returns 0 or 1, **not 2**. Thus the
failure occurred before the guard's memory-pressure logic started.

The installed systemd unit runs as `assistx-recovery` from a protected
checkout under `/srv/assistx-recovery/packages/auto-assist`.
The ordinary `scott` inspector cannot traverse that protected tree,
which is expected. Do not change Unix modes or elevate the fleet scanner
just to make the failure disappear. No trustworthy package-generation
manifest, deployed entrypoint bytes, or exact service-identity executable
readback has yet been obtained.

## Read-only, nonauthorizing preflight

`scripts/recovery_memory_guard_installation_preflight.py` is a
standalone Python standard-library tool. Its default checks are:

- Inspect the pinned systemd unit path, refusing symlinks, duplicate
  service keys and ambiguous or unexpected `ExecStart`.
- Require `User=assistx-recovery`, the exact protected working
  directory, **relative** `ExecStart` script argument
  `scripts/recovery-memory-guard.py`, and the exact recovery virtual
  environment Python at `<recovery-root>/venv/bin/python`. This is the
  live Beelink unit layout; an arbitrary host or temporary Python binary
  is refused.
- Open the unit and entrypoint with `O_NOFOLLOW | O_NONBLOCK`; reject
  FIFOs, nonregular files, unsafe group/other write permission, oversize,
  race-changed files and symlinks anywhere along their directory paths.
- Compare the entrypoint bytes with an **explicit supplied**
  SHA-256 obtained from the authorized, reviewed package generation.
  There is deliberately **no embedded auto-accepted checksum**.

The tool only returns `decision=HOLD`, `deployment_authorized=false`,
`runtime_guard_evaluated=false`, `recovery_service_started=false`;
CLI exit is **2 even for an exact file match**. It does **not** import the
deployed guard, invoke `systemctl`, access recovery state, scan a NAS
payload or read tokens/environment values. Its JSON only contains stable
reason codes, never file content, command lines, actual paths or secrets.

### Reproduce tests (safe on a disposable host)

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover \
  -s tests -p test_recovery_memory_guard_installation_preflight.py -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -B -m pytest -q \
  tests/test_recovery_memory_guard.py
```

The tests write only fake unit/script files in temporary scratch.
They cover a script that would raise if executed to prove the preflight
never imports or runs entrypoint code.

### Authorized operator-only inspection (not yet performed)

After obtaining a valid release manifest through a separate trusted
channel, the **authorized recovery service identity**, not the fleet
scanner, can run:

```sh
python3 -B scripts/recovery_memory_guard_installation_preflight.py \
  --unit-file /etc/systemd/system/assistx-recovery-memory-guard.service \
  --package-root /srv/assistx-recovery/packages/auto-assist \
  --expected-entrypoint-sha256 <VERIFIED_64_CHARACTER_RELEASE_SHA256>
```

This still returns HOLD. The operator must separately compare the
versioned package generation and its manifest, correct any deployment
or systemd-path drift by a reviewed **atomic deployment procedure**, and
verify Python module imports under the proper UID/environment. Do not
copy an arbitrary GitHub script into a live protected directory.
Do not run the memory guard itself as a "dry run": its evaluation can
stop allowlisted model units or change admission flags under pressure.

A future approved execution must independently establish memory/cgroup
health, effective service UID and script identity, signed/retained
diagnostic receipts, safe protected-service behavior, rollback and
scheduled-cycle acceptance. **NO live restart, `reset-failed`, model
execution, NAS write, token operation or authorization is part of this PR.**

## Evidence, limitations and no-go

Exact checked-in initial implementation and 16 stdlib regression cases
were accepted offline on both physical x1-370 and Xwing. The existing
four `test_recovery_memory_guard.py` tests also passed independently
on each host. **These results only prove synthetic source behavior.**
Hosted exact-head CI is a separate gate.

A success-shaped digest check does not prove installed Python package
imports, guard runtime, user privileges, systemd startability, memory
shedding safety or owner attestation. #446 remains open until the actual
versioned deployment is repaired and validated; all independent provider,
Xwing native-auth and NAS archival holds remain in force.
