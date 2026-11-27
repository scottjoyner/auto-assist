#!/usr/bin/env python3
"""Research-only TWO-ROLE Ed25519 manifest witness, always production HOLD.

No key generation, network access, Docker access, NAS reads or custody writes.
Trust keys/checkpoint are injected by the caller, NOT independently held.
Never treat a positive synthetic proof as source authority or a release grant.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import verify_curated_knowledge as corpus

DOMAIN = "assistx.hermes.synthetic-curated-signature.v1"
ROLES = ("producer", "reviewer")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX128 = re.compile(r"^[0-9a-f]{128}$")
MAX_ENVELOPE = 16384

class WitnessRejected(ValueError):
    pass

def canonical(value: object) -> bytes:
    # Strict deterministic JSON; canonical shape enforced separately.
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")

def signed_body(envelope: object) -> dict[str, Any]:
    if not isinstance(envelope, dict) or set(envelope) != {
            "schema", "domain", "sequence", "previous_sha256",
            "manifest", "signatures"}:
        raise WitnessRejected("signed_envelope_schema")
    if type(envelope["schema"]) is not int or envelope["schema"] != 1:
        raise WitnessRejected("signed_envelope_schema")
    if envelope["domain"] != DOMAIN:
        raise WitnessRejected("wrong_signature_domain")
    seq = envelope["sequence"]
    if type(seq) is not int or not (1 <= seq <= 1000000):
        raise WitnessRejected("sequence_invalid")
    prev = envelope["previous_sha256"]
    if not isinstance(prev, str) or not HEX64.fullmatch(prev):
        raise WitnessRejected("previous_digest_invalid")
    # Validate manifest without reading fixture before authenticating.
    corpus.parse_manifest(envelope["manifest"])
    result = {key: envelope[key] for key in
              ("schema", "domain", "sequence", "previous_sha256", "manifest")}
    if len(canonical(result)) > MAX_ENVELOPE:
        raise WitnessRejected("oversized_signed_body")
    return result

def _validate_trust(trust: object) -> dict[str, Ed25519PublicKey]:
    if not isinstance(trust, dict) or set(trust) != set(ROLES):
        raise WitnessRejected("trust_role_set_invalid")
    if not all(isinstance(trust[x], str) and HEX64.fullmatch(trust[x])
               for x in ROLES):
        raise WitnessRejected("trust_key_format")
    if trust[ROLES[0]] == trust[ROLES[1]]:
        raise WitnessRejected("signers_not_distinct")
    try:
        return {role: Ed25519PublicKey.from_public_bytes(bytes.fromhex(trust[role]))
                for role in ROLES}
    except (ValueError, TypeError) as exc:
        raise WitnessRejected("invalid_ed25519_public_key") from exc

def _validate_checkpoint(checkpoint: object, body: dict) -> None:
    if not isinstance(checkpoint, dict) or set(checkpoint) != {
            "schema", "sequence", "head_sha256"}:
        raise WitnessRejected("checkpoint_schema")
    prior = checkpoint["sequence"]
    digest = checkpoint["head_sha256"]
    if (type(checkpoint["schema"]) is not int or checkpoint["schema"] != 1
            or type(prior) is not int or not (0 <= prior < 1000000)
            or not isinstance(digest, str) or not HEX64.fullmatch(digest)):
        raise WitnessRejected("checkpoint_schema")
    if prior == 0 and digest != "0" * 64:
        raise WitnessRejected("genesis_checkpoint_mismatch")
    if body["sequence"] != prior + 1:
        raise WitnessRejected("stale_or_skipped_sequence")
    if body["previous_sha256"] != digest:
        raise WitnessRejected("chain_head_mismatch")

def validate(root: object = None, envelope: object = None,
             trust: object = None, checkpoint: object = None) -> dict:
    """Pure signature/checkpoint validation first; content scan only after PASS.

    Caller supplies all trust roots and checkpoint. These are not authentic
    durable outside-worker custody; even success always returns HOLD.
    """
    result = {
        "schema": 1, "mode": "hermes_two_role_synthetic_manifest_witness",
        "synthetic_signatures_valid": False,
        "synthetic_checkpoint_link_valid": False,
        "synthetic_corpus_verified": False,
        "new_synthetic_head_sha256": None,
        "production_deployment_authorized": False,
        "independent_key_custody_verified": False,
        "rollback_resistant_external_checkpoint": False,
        "production_knowledge_mount_authorized": False,
        "real_nas_accessed": False,
        "decision": "HOLD", "violations": [],
    }
    if any(x is None for x in (root, envelope, trust, checkpoint)):
        result["violations"] = ["signed_synthetic_inputs_missing"]
        return result
    try:
        body = signed_body(envelope)
        _validate_checkpoint(checkpoint, body)
        public_keys = _validate_trust(trust)
        signatures = envelope["signatures"]
        if not isinstance(signatures, dict) or set(signatures) != set(ROLES):
            raise WitnessRejected("signature_roles_invalid")
        digest_input = b"assistx-hermes-synthetic\0" + canonical(body)
        for role in ROLES:
            sig = signatures[role]
            if not isinstance(sig, str) or not HEX128.fullmatch(sig):
                raise WitnessRejected("signature_format")
            try:
                public_keys[role].verify(bytes.fromhex(sig), digest_input)
            except (InvalidSignature, ValueError) as exc:
                raise WitnessRejected("ed25519_signature_invalid") from exc
        result["synthetic_signatures_valid"] = True
        result["synthetic_checkpoint_link_valid"] = True
        # Never even validate or traverse real storage without positive
        # synthetic role+chain qualification, and safe_root precedes scan.
        inspected = corpus.audit(root, body["manifest"])
        if not inspected["synthetic_curated_fixture_verified"]:
            raise WitnessRejected("synthetic_corpus_rejected")
        result["synthetic_corpus_verified"] = True
        result["new_synthetic_head_sha256"] = hashlib.sha256(digest_input).hexdigest()
    except (WitnessRejected, corpus.Reject, OSError, TypeError,
            ValueError, OverflowError) as exc:
        result["violations"] = [str(exc) if isinstance(exc, WitnessRejected)
                                else "synthetic_witness_rejected"]
        result["new_synthetic_head_sha256"] = None
    return result

def main() -> None:
    # CLI intentionally only supports fail-closed, side-effect-free default.
    # Test code supplies in-memory synthetic private keys; NEVER accept
    # externally supplied live keys or certificates through CLI options.
    print(json.dumps(validate(), sort_keys=True))

if __name__ == "__main__":
    main()
