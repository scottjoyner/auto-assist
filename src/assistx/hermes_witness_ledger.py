"""Research reference high-water store; durable SQLite != independent custody.

Only a separately controlled owner/admin may provision/publish/revoke.
This module has NO network listener, no stored signing keys, and NO production
enablement. Whole-database rollback by its administrator remains detectable
only with a further independent append-only publication witness.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from assistx.hermes_high_water import canonical, OWNER, DIGEST

MAX_GENERATION = (1 << 63) - 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_highwater (
  owner TEXT PRIMARY KEY NOT NULL,
  generation INTEGER NOT NULL CHECK(generation >= 1),
  checkpoint_sha256 TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  revoked INTEGER NOT NULL CHECK(revoked IN (0,1))
);
"""
_GRANTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS applied_owner_grants (
  nonce TEXT PRIMARY KEY NOT NULL,
  owner TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL
);
"""
_REQUEST_KEYS = {"schema", "nonce", "owner", "generation",
                 "checkpoint_sha256", "manifest_sha256"}

class LedgerDenied(ValueError):
    pass


def _need(ok: bool, why: str) -> None:
    if not ok:
        raise LedgerDenied("Hermes witness ledger: " + why)


def _validate(owner: str, generation: int, checkpoint: str, manifest: str) -> None:
    _need(isinstance(owner, str) and bool(OWNER.fullmatch(owner)),
          "owner identity not approved")
    _need(type(generation) is int and 1 <= generation <= MAX_GENERATION,
          "generation outside monotonic domain")
    _need(isinstance(checkpoint, str) and bool(DIGEST.fullmatch(checkpoint))
          and isinstance(manifest, str) and bool(DIGEST.fullmatch(manifest)),
          "untrusted checkpoint or manifest digest")


def _validate_database_path(path: str) -> str:
    _need(isinstance(path, str) and os.path.isabs(path)
          and not path.endswith("/") and ".." not in path.split("/")
          and os.path.realpath(path) == path, "untrusted ledger path")
    parent = Path(path).parent
    _need(parent.is_dir() and not parent.is_symlink()
          and parent.stat().st_mode & 0o077 == 0,
          "ledger directory must already exist and be private")
    node = Path("/")
    for part in Path(path).parts[1:]:
        node /= part
        _need(not node.is_symlink(), "symlink in ledger path")
    if os.path.exists(path):
        meta = os.stat(path)
        _need(stat.S_ISREG(meta.st_mode)
              and meta.st_mode & 0o077 == 0,
              "ledger must be a private regular file")
    return path


class ResearchHighWaterLedger:
    """SQLite transactions provide crash durability within this FS only.

    No auto-enrollment. No identity/authentication occurs in this module:
    API callers MUST independently authorize all publisher operations.
    The request handler never updates state and never signs a success for
    an unknown owner.
    """

    def __init__(self, path: str):
        self.path = _validate_database_path(path)
        if not os.path.exists(self.path):
            try:
                fd=os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                           | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileExistsError:
                _validate_database_path(self.path)
            else:
                os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=2.0, isolation_level=None)
        try:
            self.db.execute("PRAGMA trusted_schema=OFF")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(_SCHEMA)
            self.db.execute(_GRANTS_SCHEMA)
            self.db.execute("COMMIT")
            os.chmod(self.path, 0o600)
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _get(self, owner: str):
        return self.db.execute(
            "SELECT generation,checkpoint_sha256,manifest_sha256,revoked "
            "FROM owner_highwater WHERE owner=?", (owner,)).fetchone()

    def publish(self, *, owner: str, generation: int,
                checkpoint_sha256: str, manifest_sha256: str) -> None:
        """Explicit publisher-only operation; no implicit worker promotion."""
        _validate(owner,generation,checkpoint_sha256,manifest_sha256)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self._get(owner)
            if old:
                _need(not bool(old[3]), "revocation is sticky")
                _need(generation >= old[0], "generation rollback denied")
                if generation == old[0]:
                    _need((checkpoint_sha256,manifest_sha256) == (old[1],old[2]),
                          "same-generation equivocation denied")
            self.db.execute(
                "INSERT INTO owner_highwater"
                "(owner,generation,checkpoint_sha256,manifest_sha256,revoked)"
                " VALUES(?,?,?,?,0) "
                "ON CONFLICT(owner) DO UPDATE SET "
                "generation=excluded.generation,"
                "checkpoint_sha256=excluded.checkpoint_sha256,"
                "manifest_sha256=excluded.manifest_sha256",
                (owner,generation,checkpoint_sha256,manifest_sha256))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def revoke(self, owner: str) -> None:
        _need(isinstance(owner,str) and bool(OWNER.fullmatch(owner)),
              "invalid revocation owner")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cursor=self.db.execute(
                "UPDATE owner_highwater SET revoked=1 WHERE owner=?",(owner,))
            _need(cursor.rowcount==1,"cannot revoke unenrolled owner")
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def apply_signed_grant(
        self, *, raw_grant: bytes, authorizer_public_key: bytes,
        witness_signing_public_key: bytes | None = None,
    ) -> None:
        """Atomic, signed owner-scoped mutation; never expose raw publish.

        Signer identity is trusted only when authorizer_public_key has been
        pinned by a separate administrator outside worker/ledger custody.
        An explicit prior state blocks concurrent overwrites/equivocation.
        Nonces are one-shot within this SQLite database. Whole-DB rollback
        still defeats replay protection without an external anchor.
        """
        from assistx.hermes_witness_grants import verify_owner_grant, EMPTY
        grant = verify_owner_grant(
            raw_grant, authorizer_public_key, witness_signing_public_key)
        owner = grant["owner"]
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self._get(owner)
            if old is None:
                _need(grant["action"] == "publish",
                      "cannot revoke unregistered owner")
                _need(grant["prior_generation"] == 0
                      and grant["prior_checkpoint_sha256"] == EMPTY
                      and grant["prior_manifest_sha256"] == EMPTY,
                      "bootstrap must bind nonexistent prior state")
            else:
                _need(not bool(old[3]), "revocation is sticky")
                _need(grant["prior_generation"] == old[0]
                      and grant["prior_checkpoint_sha256"] == old[1]
                      and grant["prior_manifest_sha256"] == old[2],
                      "signed predecessor does not match durable owner state")
            cursor = self.db.execute(
                "SELECT 1 FROM applied_owner_grants WHERE nonce=?",
                (grant["nonce"],))
            _need(cursor.fetchone() is None, "signed grant nonce already used")
            if grant["action"] == "publish":
                if old is not None:
                    _need(grant["generation"] > old[0],
                          "published grant must advance generation")
                self.db.execute(
                    "INSERT INTO owner_highwater"
                    "(owner,generation,checkpoint_sha256,manifest_sha256,revoked)"
                    " VALUES(?,?,?,?,0) "
                    "ON CONFLICT(owner) DO UPDATE SET "
                    "generation=excluded.generation,"
                    "checkpoint_sha256=excluded.checkpoint_sha256,"
                    "manifest_sha256=excluded.manifest_sha256",
                    (owner, grant["generation"],
                     grant["checkpoint_sha256"],grant["manifest_sha256"]))
            else:
                _need(old is not None
                      and grant["generation"] == old[0]
                      and grant["checkpoint_sha256"] == old[1]
                      and grant["manifest_sha256"] == old[2],
                      "revocation must bind exact currently retained state")
                updated = self.db.execute(
                    "UPDATE owner_highwater SET revoked=1 WHERE owner=?",
                    (owner,))
                _need(updated.rowcount == 1,
                      "cannot revoke missing owner")
            self.db.execute(
                "INSERT INTO applied_owner_grants(nonce,owner,payload_sha256)"
                " VALUES(?,?,?)",
                (grant["nonce"],owner,
                 hashlib.sha256(raw_grant).hexdigest()))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def respond(self, *, raw_request: bytes, signer) -> bytes:
        """Sign an exact fresh request's current state, never mutate ledger.

        A mismatched checkpoint yields revoked=True, not an affirmative
        approval. A genuine higher high-water is returned to force denial.
        """
        _need(isinstance(raw_request,bytes) and 0<len(raw_request)<=2048,
              "missing or oversized challenge")
        try:
            request=json.loads(raw_request)
        except (ValueError,UnicodeError):
            raise LedgerDenied("Hermes witness ledger: malformed challenge")
        _need(isinstance(request,dict) and set(request)==_REQUEST_KEYS
              and type(request["schema"]) is int and request["schema"]==1,
              "unrecognized challenge")
        _validate(request["owner"],request["generation"],
                  request["checkpoint_sha256"],request["manifest_sha256"])
        nonce=request["nonce"]
        _need(isinstance(nonce,str) and bool(re.fullmatch("[0-9a-f]{64}",nonce)),
              "invalid nonce")
        self.db.execute("BEGIN")
        try:
            row=self._get(request["owner"])
            _need(row is not None,"unenrolled owner denied")
            generation,checkpoint,manifest,revoked=row
            response={**request,
                "high_water_generation":generation,
                "revoked":bool(revoked or request["generation"] != generation
                               or request["checkpoint_sha256"] != checkpoint
                               or request["manifest_sha256"] != manifest)}
            signature=signer.sign(canonical(response))
            _need(isinstance(signature,bytes) and len(signature)==64,
                  "invalid witness signer")
            self.db.execute("COMMIT")
            return canonical({**response,"signature":signature.hex()})
        except Exception:
            self.db.execute("ROLLBACK")
            raise
