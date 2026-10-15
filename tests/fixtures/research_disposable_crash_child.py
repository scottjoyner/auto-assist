#!/usr/bin/env python3
"""Research-only subprocess crash worker; terminates its own process only.

Explicit test sentinel and disposable SQLite filenames are mandatory.
No external database, fleet, network, credentials, or production paths.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import signal
import sqlite3
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/research-consume-asymmetric-receipt.py"
SPEC = importlib.util.spec_from_file_location("synthetic_crash_consumer", PATH)
assert SPEC and SPEC.loader
consumer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(consumer)
RESOURCE_ID = "auto-router-runtime-projection"
NOW = 1792000000000


def fatal_stage(stage: str) -> None:
    print("RESEARCH_STAGE=" + stage, flush=True)
    os.kill(os.getpid(), signal.SIGKILL)


def run() -> int:
    if os.environ.get("ALLOW_SYNTHETIC_CHILD_KILL") != "TEST_FIXTURE_ONLY":
        return 4
    if len(sys.argv) != 4:
        return 5
    anchor = Path(sys.argv[1])
    resource = Path(sys.argv[2])
    stage = sys.argv[3]
    if stage not in {
        "source_pre_commit", "source_after_commit_before_anchor",
        "anchor_after_updates_before_commit", "anchor_after_commit_before_ack",
        "no_crash",
    }:
        return 6
    if not (
        anchor.name == "research-anchor.sqlite"
        and resource.name == "research-resource.sqlite"
        and anchor.parent == resource.parent
        and (anchor.parent / ".synthetic-crash-lab-only").is_file()
        and anchor.is_file() and resource.is_file()
    ):
        return 7

    effect = {
        "resource_id": RESOURCE_ID, "term": 4, "commit_seq": 12,
        "generation": 27, "checksum": "b" * 64,
        "revision_sha256": "d" * 64, "committed": True,
    }
    with sqlite3.connect(resource) as writer:
        writer.execute("PRAGMA synchronous=FULL")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO effect VALUES (?,?,?,?,?,?,?)",
            (RESOURCE_ID, 4, 12, 27, "b" * 64, "d" * 64, 1),
        )
        writer.execute(
            "INSERT INTO receipt VALUES (?,?,?,?,?,?)",
            (4, 12, 27, "b" * 64, "d" * 64, "e" * 32),
        )
        if stage == "source_pre_commit":
            fatal_stage(stage)
        writer.commit()
    if stage == "source_after_commit_before_anchor":
        fatal_stage(stage)

    # Synthetic signing key never enters the real DB or verifier.
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    claim = {
        "schema_version": 1, "audience": "synthetic-resource-commit-observation",
        "resource_id": RESOURCE_ID, "term": 4, "commit_seq": 12,
        "generation": 27, "checksum": "b" * 64,
        "revision_sha256": "d" * 64, "nonce": "e" * 32,
        "challenge": "a" * 64, "key_epoch": 7,
        "issued_at_ms": NOW - 1000, "expires_at_ms": NOW + 5000,
    }
    verifier = consumer._verifier()
    envelope = {
        "key_id": "independent-resource-commit-research-v1",
        "receipt": claim,
        "signature": base64.b64encode(
            key.sign(verifier.canonical(claim))
        ).decode("ascii"),
    }

    def readback() -> dict | None:
        with sqlite3.connect(
            f"{resource.resolve().as_uri()}?mode=ro", uri=True
        ) as observer:
            observer.execute("PRAGMA query_only=ON")
            row = observer.execute(
                "SELECT * FROM effect WHERE resource_id=?", (RESOURCE_ID,)
            ).fetchone()
            receipt = observer.execute(
                "SELECT * FROM receipt WHERE commit_seq=12"
            ).fetchone()
        if (
            row is None or receipt is None
            or tuple(row[1:6]) != tuple(receipt[:5])
        ):
            return None
        return dict(zip(
            ("resource_id", "term", "commit_seq", "generation",
             "checksum", "revision_sha256", "committed"),
            (*row[:6], row[6] == 1), strict=True,
        ))

    def on_anchor_stage(name: str) -> None:
        if stage == name:
            fatal_stage(name)

    result = consumer.consume_once(
        anchor, envelope,
        pinned_public_keys={"independent-resource-commit-research-v1": public},
        expected_effect=effect, expected_challenge="a" * 64,
        expected_previous={"term": 4, "commit_seq": 11, "last_nonce": "c" * 32},
        readback=readback, now_ms=NOW,
        _research_crash_hook=on_anchor_stage,
    )
    print(json.dumps({"research_only": result}), flush=True)
    return 0 if result == (True, "RESEARCH_DISPOSABLE_ANCHOR_CAS_ONLY") else 8


if __name__ == "__main__":
    raise SystemExit(run())
