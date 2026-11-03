"""Fixed PhoneLog-only, image-pinned native Neo4j privilege census.

Uses the already-running PhoneLog Python py2neo client. Collects only fixed
SHOW-query grant counts; never emits usernames, roles, URIs or secret values.
This does not certify an exhaustive writer census or grant production GO.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

from read_only_native_graph_privileges import _validate_native

_TARGET = "phonelog"
_PIN = re.compile(r"sha256:[a-f0-9]{64}\Z")

# Immutable source-only query, no command-line inputs or production mutations.
# py2neo v2021 does not expose neo4j.Driver READ_ACCESS; both fixed commands
# are read-only SHOW queries on the system database.
_PHONE_PROBE = r"""
import json,os
from collections import Counter
from py2neo import Graph
uri=os.environ.get('NEO4J_URI','')
user=os.environ.get('NEO4J_USER','')
pw=os.environ.get('NEO4J_PASSWORD','')
if not all((uri,user,pw)):raise SystemExit(12)
g=Graph(uri,auth=(user,pw),name='system')
roles=list(g.run('SHOW CURRENT USER YIELD roles RETURN roles').data())
grants=list(g.run('SHOW USER PRIVILEGES').data())
if len(roles)!=1 or type(roles[0].get('roles')) is not list:raise SystemExit(13)
actions=Counter(str(r.get('action','unknown')).lower()
                for r in grants if str(r.get('access','')).upper()=='GRANTED')
print(json.dumps({
 'native_query_scope':'CURRENT_USER_PRIVILEGES_READ_ONLY',
 'granted_privilege_count':sum(actions.values()),
 'wildcard_graph_grant_count':sum(
     r.get('graph')=='*' and str(r.get('access','')).upper()=='GRANTED'
     for r in grants),
 'write_action_count':actions.get('write',0),
 'role_count':len(roles[0]['roles']),
 'has_admin_role':any(str(role).lower()=='admin' for role in roles[0]['roles']),
 'no_mutations':True,
},sort_keys=True))
"""


class PhoneCensusHold(RuntimeError):
    """Unverifiable native evidence is always a HOLD, with no raw stderr."""


def _docker(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["docker", *args], check=False, capture_output=True, text=True, timeout=12
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PhoneCensusHold("native_phone_probe_unavailable") from exc
    if proc.returncode != 0:
        raise PhoneCensusHold("native_phone_probe_failed")
    max_chars = 256000 if args[:2] == ("container", "inspect") else 8000
    if len(proc.stdout) > max_chars:
        raise PhoneCensusHold("oversized_native_phone_result")
    return proc.stdout


def _inspect() -> dict[str, Any]:
    try:
        item = json.loads(_docker("container", "inspect", _TARGET))
    except (ValueError, TypeError) as exc:
        raise PhoneCensusHold("invalid_phone_inspection_envelope") from exc
    if (
        type(item) is not list or len(item) != 1
        or item[0].get("Name") != "/phonelog"
        or (item[0].get("State") or {}).get("Status") != "running"
    ):
        raise PhoneCensusHold("phone_container_not_exactly_running")
    return item[0]


def collect(*, expected_image: str) -> dict[str, Any]:
    """Image and process pinned BEFORE and AFTER fixed read-only query."""
    if type(expected_image) is not str or not _PIN.fullmatch(expected_image):
        raise PhoneCensusHold("invalid_or_missing_phone_image_pin")
    before = _inspect()
    if before.get("Image") != expected_image:
        raise PhoneCensusHold("phone_image_mismatch")
    cid = before.get("Id")
    if type(cid) is not str or not re.fullmatch(r"[a-f0-9]{64}", cid):
        raise PhoneCensusHold("invalid_phone_process_identity")
    try:
        value = json.loads(
            _docker("exec", "-i", _TARGET, "python3", "-B", "-c", _PHONE_PROBE)
        )
        native = _validate_native(value)
    except (ValueError, TypeError) as exc:
        raise PhoneCensusHold("invalid_phone_native_evidence") from exc
    except Exception:
        # Fixed source validator has its own exception type, potentially
        # containing a raw receipt. Never relay it to the CLI.
        raise PhoneCensusHold("untrusted_phone_native_evidence") from None
    after = _inspect()
    if (
        after.get("Id") != cid or after.get("Image") != expected_image
        or (after.get("State") or {}).get("Status") != "running"
    ):
        raise PhoneCensusHold("phone_runtime_drift")
    return {
        "evidence_tier": "PRODUCTION_READ_ONLY_NATIVE_NEO4J",
        "container_id": cid,
        "image_id": expected_image,
        **native,
        "production_decision": "HOLD",
        "promotion_authorized": False,
        "effective_privilege_cutover_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--expected-phone-image", required=True)
    ap.add_argument("--json", action="store_true", required=True)
    args = ap.parse_args(argv)
    try:
        receipt = collect(expected_image=args.expected_phone_image)
    except PhoneCensusHold:
        print(json.dumps({
            "production_decision": "HOLD",
            "dispatch_authorized": False,
            "error_type": "phone_custody_or_native_readback_unverified",
        }))
        return 3
    # Fixed sanitized envelope, no container ID emitted to avoid unnecessary
    # operational metadata beyond the approved image/pin count.
    print(json.dumps({
        "source": "phonelog",
        "image_pinned": True,
        "has_admin_role": receipt["has_admin_role"],
        "role_count": receipt["role_count"],
        "grant_count": receipt["granted_privilege_count"],
        "wildcard_graph_grants": receipt["wildcard_graph_grant_count"],
        "write_action_grants": receipt["write_action_count"],
        "production_decision": "HOLD",
        "dispatch_authorized": False,
    }, sort_keys=True))
    return 3 if (
        receipt["has_admin_role"]
        or receipt["wildcard_graph_grant_count"]
        or receipt["write_action_count"]
    ) else 0


if __name__ == "__main__":
    sys.exit(main())
