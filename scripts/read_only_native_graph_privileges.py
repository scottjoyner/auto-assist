"""Read-only, redacted native Neo4j privilege census via fixed AssistX API.

Only the allowlisted running `assistx-api` container is inspected. The
embedded Python code uses its existing Neo4j environment *inside* that
container without emitting usernames, URIs, passwords, tokens or result rows.
The host command checks a caller-supplied immutable image pin, performs no
writes and returns a HOLD receipt, not deployment approval.

No generic query flag, arbitrary shell command, grant mutation or fallback.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

TARGET = "assistx-api"
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
# A fixed interpreter payload. No user-controlled query/input string is passed
# into Neo4j; all credential lookup occurs within the already-running process.
CONTAINER_PROBE = r'''
import json, os
from collections import Counter
from neo4j import GraphDatabase, READ_ACCESS

uri=os.environ.get("NEO4J_URI","")
user=os.environ.get("NEO4J_USER","")
password=os.environ.get("NEO4J_PASSWORD","")
if not uri or not user or not password:
    raise SystemExit(12)
driver=GraphDatabase.driver(uri,auth=(user,password),connection_timeout=4.0)
try:
    with driver.session(database="system",default_access_mode=READ_ACCESS) as session:
        privileges=[r.data() for r in session.run("SHOW USER PRIVILEGES")]
        actual_roles=[r.data() for r in session.run("SHOW CURRENT USER YIELD roles RETURN roles")]
    if len(actual_roles)!=1 or not isinstance(actual_roles[0].get("roles"),list):
        raise SystemExit(13)
    roles=actual_roles[0]["roles"]
    actions=Counter(
        str(row.get("action","unknown")).lower()
        for row in privileges
        if str(row.get("access","")).upper()=="GRANTED"
    )
    report={
        "native_query_scope":"CURRENT_USER_PRIVILEGES_READ_ONLY",
        "granted_privilege_count":sum(actions.values()),
        "wildcard_graph_grant_count":sum(
            row.get("graph")=="*" and str(row.get("access","")).upper()=="GRANTED"
            for row in privileges
        ),
        "write_action_count":sum(n for a,n in actions.items() if a=="write"),
        "role_count":len(roles),
        "has_admin_role":any(str(r).lower()=="admin" for r in roles),
        "no_mutations":True,
    }
    print(json.dumps(report,sort_keys=True))
finally:
    driver.close()
'''


class CensusHold(RuntimeError):
    """The current runtime cannot provide valid trustworthy read-only evidence."""


def _docker(args: list[str], timeout: int = 9) -> str:
    try:
        proc = subprocess.run(
            ["docker", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CensusHold("docker inspection or native readback unavailable") from exc
    if proc.returncode:
        # Never print stdout/stderr: driver errors might contain credentials.
        raise CensusHold("docker inspection or native readback failed")
    # Docker inspect includes lots of unrelated configuration metadata,
    # never print it. Only the native in-container JSON is tightly bounded.
    max_chars = 256000 if args[:2] == ["container", "inspect"] else 8000
    if len(proc.stdout) > max_chars:
        raise CensusHold("oversized census output denied")
    return proc.stdout


def _inspect() -> dict[str, Any]:
    try:
        values = json.loads(_docker(["container", "inspect", TARGET]))
    except (ValueError, TypeError) as exc:
        raise CensusHold("invalid Docker inspect envelope") from exc
    if not isinstance(values, list) or len(values) != 1:
        raise CensusHold("ambiguous Docker identity")
    obj = values[0]
    if obj.get("Name") != "/" + TARGET or (
        (obj.get("State") or {}).get("Status") != "running"
    ):
        raise CensusHold("unexpected runtime container state")
    return obj


def _validate_native(value: Any) -> dict[str, Any]:
    expected = {
        "native_query_scope", "granted_privilege_count",
        "wildcard_graph_grant_count", "write_action_count",
        "role_count", "has_admin_role", "no_mutations",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise CensusHold("malformed or untrusted native privilege readback")
    if value["native_query_scope"] != "CURRENT_USER_PRIVILEGES_READ_ONLY":
        raise CensusHold("query domain mismatch")
    for key in (
        "granted_privilege_count", "wildcard_graph_grant_count",
        "write_action_count", "role_count",
    ):
        if type(value[key]) is not int or not 0 <= value[key] <= 10000:
            raise CensusHold("invalid native privilege counts")
    if (type(value["has_admin_role"]) is not bool
            or value["no_mutations"] is not True):
        raise CensusHold("native readback lacks read-only proof flag")
    if (value["wildcard_graph_grant_count"] > value["granted_privilege_count"]
            or value["write_action_count"] > value["granted_privilege_count"]):
        raise CensusHold("inconsistent native privilege counts")
    return value


def collect(*, expected_image: str) -> dict[str, Any]:
    if not isinstance(expected_image, str) or not IMAGE_ID.fullmatch(expected_image):
        raise CensusHold("caller must pin an immutable sha256 image ID")
    obj = _inspect()
    if obj.get("Image") != expected_image:
        raise CensusHold("runtime image mismatch, refusing native readback")
    before_id = obj.get("Id")
    if not isinstance(before_id, str) or not re.fullmatch(r"[0-9a-f]{64}", before_id):
        raise CensusHold("invalid running container identity")
    try:
        value = json.loads(_docker(["exec", "-i", TARGET, "python", "-c", CONTAINER_PROBE]))
    except (TypeError, ValueError) as exc:
        raise CensusHold("invalid native privilege JSON") from exc
    detail = _validate_native(value)
    after = _inspect()
    if (after.get("Id") != before_id or after.get("Image") != expected_image
            or (after.get("State") or {}).get("Status") != "running"):
        raise CensusHold("runtime drift during privilege readback")
    return {
        "evidence_tier": "PRODUCTION_READ_ONLY_NATIVE_NEO4J",
        "container_id": before_id,
        "image_id": expected_image,
        **detail,
        "production_decision": "HOLD",
        "promotion_authorized": False,
        "effective_privilege_cutover_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--expected-image", required=True)
    ap.add_argument("--json", action="store_true", required=True)
    ap.add_argument("--deny-on-broad-privilege", action="store_true")
    args = ap.parse_args(argv)
    try:
        receipt = collect(expected_image=args.expected_image)
    except CensusHold as exc:
        print(json.dumps({"production_decision": "HOLD",
                          "promotion_authorized": False,
                          "error": str(exc)}))
        return 3
    print(json.dumps(receipt, sort_keys=True))
    if args.deny_on_broad_privilege and (
        receipt["has_admin_role"] or receipt["wildcard_graph_grant_count"] > 0
        or receipt["write_action_count"] > 0
    ):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
