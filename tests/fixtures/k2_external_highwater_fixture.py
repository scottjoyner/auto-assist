"""Separate synthetic high-water database for rollback/fork tests ONLY.

This is deliberately NOT an independently custodied signer or a production
external CAS. Both files remain under the test runner's sole control.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

NONCE = re.compile(r"[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


class SyntheticAnchorHold(ValueError):
    """Static no-authority error for anchored research experiments."""


class SyntheticExternalHighwater:
    """A distinct disposable SQLite high-water file, with no live adapter."""

    def __init__(self, path: Path, test_key: Ed25519PrivateKey | None = None):
        self.path = Path(path)
        # This key exists ONLY in the disposable test process.
        self._test_key = test_key

    @property
    def test_public_key_sha256(self) -> str:
        if self._test_key is None:
            raise SyntheticAnchorHold("test_only_signing_key_missing")
        return hashlib.sha256(self._public_key_bytes()).hexdigest()

    def _public_key_bytes(self) -> bytes:
        if self._test_key is None:
            raise SyntheticAnchorHold("test_only_signing_key_missing")
        return self._test_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @classmethod
    def bootstrap_new(cls, path: Path) -> SyntheticExternalHighwater:
        path = Path(path)
        if path.is_symlink() or path.exists() or not path.parent.is_dir():
            raise SyntheticAnchorHold("anchor_file_exists_or_invalid")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as exc:
            raise SyntheticAnchorHold("anchor_create_failed") from exc
        os.close(fd)
        anchor = cls(path, Ed25519PrivateKey.generate())
        with anchor.session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE monotonic(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    sequence INTEGER NOT NULL CHECK(sequence >= 0)
                )
            """)
            db.execute("INSERT INTO monotonic(id,sequence) VALUES (1,0)")
            db.execute("""
                CREATE TABLE spends(
                    nonce TEXT PRIMARY KEY,
                    sequence INTEGER UNIQUE NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    CHECK(length(nonce)=64 AND length(receipt_sha256)=64)
                )
            """)
        return anchor

    @contextmanager
    def session(self):
        if self.path.is_symlink() or not self.path.is_file():
            raise SyntheticAnchorHold("anchor_missing_or_symlink")
        try:
            db = sqlite3.connect(
                self.path.absolute().as_uri() + "?mode=rw",
                uri=True, isolation_level=None, timeout=3.0,
            )
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=3000")
        except sqlite3.Error as exc:
            raise SyntheticAnchorHold("anchor_unavailable") from exc
        try:
            yield db
        finally:
            db.close()

    def reserve(self, nonce: str, receipt_sha256: str, expected_sequence: int) -> dict:
        """Advance before local ledger commit. Uncertain outcomes fail closed.

        This models why a separately administered monotonic CAS is necessary.
        It does not authenticate a real operator, signer or witness.
        """
        if (not isinstance(nonce, str) or not NONCE.fullmatch(nonce)
                or not isinstance(receipt_sha256, str) or not SHA.fullmatch(receipt_sha256)
                or type(expected_sequence) is not int or expected_sequence < 0):
            raise SyntheticAnchorHold("anchor_input_invalid")
        try:
            with self.session() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT sequence FROM monotonic WHERE id=1").fetchone()
                if row is None or type(row[0]) is not int:
                    raise SyntheticAnchorHold("anchor_highwater_missing")
                if row[0] != expected_sequence:
                    raise SyntheticAnchorHold("external_highwater_rollback_or_fork")
                if db.execute(
                    "SELECT 1 FROM spends WHERE nonce=?", (nonce,)
                ).fetchone():
                    raise SyntheticAnchorHold("external_nonce_already_spent")
                next_seq = row[0] + 1
                db.execute(
                    "INSERT INTO spends(nonce,sequence,receipt_sha256) VALUES (?,?,?)",
                    (nonce, next_seq, receipt_sha256),
                )
                changed = db.execute(
                    "UPDATE monotonic SET sequence=? WHERE id=1 AND sequence=?",
                    (next_seq, row[0]),
                )
                if changed.rowcount != 1:
                    raise SyntheticAnchorHold("external_cas_race")
                db.execute("COMMIT")
        except sqlite3.Error as exc:
            raise SyntheticAnchorHold("external_cas_unavailable") from exc
        # The signature models wire-level authenticity only. The private
        # key is controlled by THIS test runner, not independent custody.
        report = {
            "kind": "synthetic-untrusted-highwater-reservation-v1",
            "sequence": next_seq,
            "nonce": nonce,
            "receipt_sha256": receipt_sha256,
            "operator_signed": False,
            "independent_custody_verified": False,
            "can_dispatch": False,
            "decision": "HOLD",
            "test_public_key_b64": base64.b64encode(
                self._public_key_bytes()
            ).decode(),
        }
        if self._test_key is None:
            raise SyntheticAnchorHold("test_only_signing_key_missing")
        report["test_signature_b64"] = base64.b64encode(
            self._test_key.sign(json.dumps(
                report, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode())
        ).decode()
        return report

    def audit(self) -> dict:
        with self.session() as db:
            row = db.execute("SELECT sequence FROM monotonic WHERE id=1").fetchone()
            if row is None:
                raise SyntheticAnchorHold("anchor_highwater_missing")
            return {
                "highwater": row[0],
                "spends": db.execute("SELECT COUNT(*) FROM spends").fetchone()[0],
            }
