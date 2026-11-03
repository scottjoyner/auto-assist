#!/usr/bin/env python3
"""Off-host K2 witness TECHNICAL readiness. Never approves independent custody.

No signer, TPM use, hardware quote, model inference, DB writes or deployments.
All three aliases use existing SSH credentials: a successful probe is NOT an
independently administered signing or anti-rollback trust domain.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import shlex
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

SCHEMA = "assistx.k2-custody-readiness-readonly.v1"
HOST_ALIASES = ("lenovo", "destroyer", "xwing")
REMOTE_FIELDS = {
    "challenge", "python", "sqlite", "tpm0", "tpmrm0",
    "tpm_pcr_sysfs_present", "tpm2_getcap_present", "ntp_synchronized",
    "secure_boot_variables_visible", "kernel_lockdown_file_visible",
    "clock_unix",
}
MAX_OUTPUT = 2048
MAX_CLOCK_ERROR_SECONDS = 10.0
NONCE = re.compile(r"[0-9a-f]{32}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?\Z")
# This is not user input. No write commands, user-controlled shell fragments,
# TCP listeners, key material, host fingerprints or security secrets.
REMOTE = """
import json,os,platform,shutil,sqlite3,subprocess,time
from pathlib import Path
try:
 p=subprocess.run(['timedatectl','show','-p','NTPSynchronized','--value'],
                  capture_output=True,text=True,timeout=2,check=False)
 synced=(p.returncode==0 and p.stdout.strip()=='yes')
except (OSError,subprocess.TimeoutExpired):
 synced=False
print(json.dumps({
 'challenge': CHALLENGE,
 'python':platform.python_version(),
 'sqlite':sqlite3.sqlite_version,
 'tpm0':Path('/dev/tpm0').exists(),
 'tpmrm0':Path('/dev/tpmrm0').exists(),
 'tpm_pcr_sysfs_present':Path('/sys/class/tpm/tpm0').exists(),
 'tpm2_getcap_present':shutil.which('tpm2_getcap') is not None,
 'ntp_synchronized':synced,
 'secure_boot_variables_visible':Path('/sys/firmware/efi/efivars').is_dir(),
 'kernel_lockdown_file_visible':Path('/sys/kernel/security/lockdown').exists(),
 'clock_unix':time.time(),
},separators=(',',':')))
"""


def deny(code: str) -> None:
    raise ValueError(code)


def parse_report(raw: str, *, challenge: str) -> dict:
    if not isinstance(raw, str) or not 0 < len(raw) <= MAX_OUTPUT:
        deny("missing_or_oversized_remote_data")

    def unique(pairs: list[tuple[str, Any]]) -> dict:
        result: dict = {}
        for k, v in pairs:
            if k in result:
                deny("duplicate_remote_json_key")
            result[k] = v
        return result

    try:
        report = json.loads(
            raw, object_pairs_hook=unique,
            parse_constant=lambda _: deny("nonfinite_remote_value"),
        )
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_remote_json") from exc
    if not isinstance(report, dict) or set(report) != REMOTE_FIELDS:
        deny("unknown_or_missing_remote_fields")
    if report["challenge"] != challenge:
        deny("remote_nonce_mismatch")
    if not all(
        isinstance(report[k], str) and VERSION.fullmatch(report[k])
        for k in ("python", "sqlite")
    ):
        deny("invalid_remote_versions")
    for k in (
        "tpm0", "tpmrm0", "tpm_pcr_sysfs_present",
        "tpm2_getcap_present", "ntp_synchronized",
        "secure_boot_variables_visible", "kernel_lockdown_file_visible",
    ):
        if type(report[k]) is not bool:
            deny("invalid_remote_capability_type")
    clock = report["clock_unix"]
    if type(clock) not in (int, float) or not 1_600_000_000 < clock < 2_500_000_000:
        deny("invalid_remote_clock")
    return report


def ssh_command(alias: str, challenge: str) -> list[str]:
    if alias not in HOST_ALIASES:
        deny("unreviewed_host_alias")
    if not isinstance(challenge, str) or not NONCE.fullmatch(challenge):
        deny("invalid_fresh_probe_nonce")
    remote = "CHALLENGE = " + repr(challenge) + "\n" + REMOTE
    return [
        "ssh", "-T", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ForwardAgent=no",
        "-o", "ClearAllForwardings=yes",
        "-o", "ControlMaster=no", "-o", "ControlPath=none",
        "-o", "UpdateHostKeys=no", "-o", "PermitLocalCommand=no",
        "-o", "ConnectTimeout=4", "-o", "ServerAliveInterval=3",
        alias, "python3 -c " + shlex.quote(remote),
    ]


def probe(
    alias: str, *,
    runner: Any = subprocess.run,
    nonce_source: Any = secrets.token_hex,
    wall_clock: Any = time.time,
    monotonic: Any = time.monotonic,
) -> dict:
    result: dict[str, Any] = {
        "alias": alias, "status": "FAIL_CLOSED",
        "observed": False, "tpm_device_visible": False,
        "tpm2_tool_available": False,
        "clock_synchronized": False,
        "secure_boot_status_verified": False,
        "tpm_quote_verified": False,
        "independent_administration_verified": False,
        "independent_key_custody_verified": False,
        "external_antirollback_verified": False,
        "can_dispatch": False,
        "decision": "HOLD",
    }
    try:
        challenge = nonce_source(16)
        command = ssh_command(alias, challenge)
        before_wall, before_mono = wall_clock(), monotonic()
        output = runner(
            command, capture_output=True, text=True, timeout=9, check=False
        )
        after_mono, after_wall = monotonic(), wall_clock()
        if output.returncode != 0:
            deny("ssh_unavailable_or_untrusted_host")
        report = parse_report(output.stdout, challenge=challenge)
        round_trip = max(0, after_mono - before_mono)
        midpoint = (before_wall + after_wall) / 2
        error_bound = abs(report["clock_unix"] - midpoint) + round_trip / 2
        p = tuple(int(i) for i in report["python"].split("."))
        sql = tuple(int(i) for i in report["sqlite"].split("."))
        tpm = (report["tpm0"] or report["tpmrm0"]) and report["tpm_pcr_sysfs_present"]
        if error_bound > MAX_CLOCK_ERROR_SECONDS:
            status = "CLOCK_SKEW_HOLD"
        elif not report["ntp_synchronized"]:
            status = "CLOCK_SYNC_HOLD"
        elif p < (3, 10) or sql < (3, 35):
            status = "RUNTIME_CAPABILITY_HOLD"
        elif not tpm:
            status = "NO_VISIBLE_TPM_ALTERNATE_CUSTODY_REQUIRED"
        elif not report["tpm2_getcap_present"]:
            status = "TPM_TOOLING_NOT_VALIDATED"
        else:
            status = "TPM_TECHNICAL_CANDIDATE_ONLY"
        result.update({
            "status": status,
            "observed": True,
            "tpm_device_visible": bool(tpm),
            "tpm2_tool_available": report["tpm2_getcap_present"],
            "clock_synchronized": report["ntp_synchronized"],
            # Sysfs presence only. Secure Boot state was NOT read or verified.
            "secure_boot_variables_visible": report["secure_boot_variables_visible"],
            "kernel_lockdown_file_visible": report["kernel_lockdown_file_visible"],
            "clock_error_bound_ms": round(error_bound * 1000, 1),
        })
    except (ValueError, OSError, subprocess.TimeoutExpired):
        # Do not include stderr, SSH keys, command output or exception details.
        pass
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("aliases", nargs="+", choices=HOST_ALIASES)
    args = parser.parse_args(argv)
    if len(args.aliases) > 3 or len(set(args.aliases)) != len(args.aliases):
        raise SystemExit("HOLD: unique reviewed host aliases required")
    report = {
        "schema": SCHEMA,
        "observed_utc": datetime.now(UTC).isoformat(),
        "decision": "NO_GO_RESEARCH_ONLY",
        "can_dispatch": False,
        "independent_administration_verified": False,
        "independent_key_custody_verified": False,
        "external_antirollback_verified": False,
        "witness_signing_keys_provisioned": False,
        "production_trust_root_pinned": False,
        "custody_scope": "existing shared fleet admin SSH; not independent",
        "hosts": [probe(alias) for alias in args.aliases],
    }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    # Always a nonzero exit: candidate readiness is never admission.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
