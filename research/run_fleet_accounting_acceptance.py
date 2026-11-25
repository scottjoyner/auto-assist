#!/usr/bin/env python3
"""Pinned synthetic acceptance; run in a trusted isolated checkout.
No network, live imports, production DB access, or execution authority.
Six tests reproduce legacy limitations rather than establish repairs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

PINS = {
    "src/assistx/fleet_executor.py": "187c11913bd7e787742e1c9babfe6188726fc3d7",
    "research/fleet_router_accounting_summary.py": "233bbac9cc7d5086a420d2b66918cd6578345135",
    "research/fleet_agent_run_accounting_export.py": "a872ecb4257315e6cb366850e122e3acc9243869",
    "research/fleet_router_request_observation.py": "cd454ca527a0656a1c1b0af7932e7f508e0db55a",
    "research/fleet_router_observation_journal.py": "e69fcc3a92e2e9928e69531c3685b27e5b97b3a6",
    "research/fleet_router_publication_witness.py": "08fed3c8534b1f82186d3d368908525222b9015f",
    "tests/test_fleet_router_response_accounting.py": "f62fa67b76972598f6ccc4b16753e537f141f612",
    "tests/test_fleet_router_accounting_summary.py": "def429f5f1eb00ffb63e08d269db540b5cc1b37b",
    "tests/test_fleet_agent_run_accounting_export.py": "af1ecbab321db8146bfe578b15e88db630cd2872",
    "tests/test_fleet_accounting_completion_pipeline.py": "143d7a3a4def084577b102fcf2b3f64a3751648a",
    "tests/test_router_request_evidence_limitations.py": "649c0683a0d3d68d854b28bbb6b3307b02390c84",
    "tests/test_fleet_router_request_observation.py": "34d43f1cfa1a3585d98d991c94be9d3a4b685baf",
    "tests/test_fleet_router_observation_journal.py": "ae02893ca4f745694d56ab7e10c52f112708c8dd",
    "tests/test_fleet_router_publication_witness.py": "a982f867b50731319e3d67d1a22b24963204de99"
}
ROUTER_PINS = {
    "src/auto_router/route_events.py": "d79dcb07c93aaa1d69383cf63dd3a7aea81b3fdb",
    "src/auto_router/event_outbox.py": "024bf48c7f2cf5f97965c5018e69711440abb939"
}
SUITES = [
    [
        "tests/test_fleet_router_response_accounting.py",
        9
    ],
    [
        "tests/test_fleet_router_accounting_summary.py",
        9
    ],
    [
        "tests/test_fleet_agent_run_accounting_export.py",
        11
    ],
    [
        "tests/test_fleet_accounting_completion_pipeline.py",
        7
    ],
    [
        "tests/test_router_request_evidence_limitations.py",
        6
    ],
    [
        "tests/test_fleet_router_request_observation.py",
        9
    ],
    [
        "tests/test_fleet_router_observation_journal.py",
        11
    ],
    [
        "tests/test_fleet_router_publication_witness.py",
        10
    ]
]
METHOD_SHA256 = "40ca57bbb47f5d7074513a43ed6ec57c63ccc30dd19a9ff15f1a31d455ec7832"
MAX_SOURCE_BYTES = 256 * 1024

def read_source(path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("source unavailable or oversized")
    with path.open("rb") as f:
        raw = f.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("source too large")
    return raw

def verify_blob(path, expected):
    raw = read_source(path)
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != expected:
        raise ValueError("source pin mismatch")
    return raw

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--router-source-root", type=Path, required=True)
    p.add_argument("--neo-method", type=Path, required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        with tempfile.TemporaryDirectory(prefix="accounting-acceptance-") as tmp:
            dest = Path(tmp)
            for relative, pin in PINS.items():
                raw = verify_blob(root / relative, pin)
                target = dest / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            for relative, pin in ROUTER_PINS.items():
                raw = verify_blob(args.router_source_root / relative, pin)
                target = dest / "router" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            method = read_source(args.neo_method)
            if hashlib.sha256(method).hexdigest() != METHOD_SHA256:
                raise ValueError("Neo method digest mismatch")
            method_path = dest / "neo_complete_task.py"
            method_path.write_bytes(method)
            env = {"PATH": os.defpath, "LANG":"C.UTF-8", "TMPDIR":str(dest),
                   "ROUTER_SOURCE_ROOT":str(dest / "router"),
                   "FLEET_NEO_METHOD":str(method_path)}
            results = []
            for relative, expected in SUITES:
                r = subprocess.run([sys.executable,"-B",str(dest / relative)],
                    cwd=dest,env=env,capture_output=True,text=True,timeout=30)
                match = re.search(r"^Ran (\d+) tests? in ",r.stderr,re.M)
                actual = int(match.group(1)) if match else None
                ok = r.returncode == 0 and actual == expected and bool(re.search(r"^OK$",r.stderr,re.M))
                results.append({"suite":relative,"expected":expected,"executed":actual,"pass":ok})
                if not ok:
                    print(json.dumps({"decision":"HOLD","suites":results,"accepted_work_units":None}))
                    return 1
            print(json.dumps({"decision":"SYNTHETIC_ACCEPTANCE_PASS","suites":results,
                "passing_test_methods":sum(x["executed"] for x in results),
                "legacy_limitation_reproductions":6,"production_acceptance":False,
                "physical_execution_verified":False,"actual_endpoint_calls":None,
                "accepted_work_units":None},sort_keys=True))
            return 0
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print(json.dumps({"decision":"HOLD","reason":type(exc).__name__}))
        return 1

if __name__ == "__main__":
    sys.exit(main())
