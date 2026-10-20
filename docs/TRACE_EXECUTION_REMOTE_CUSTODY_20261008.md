# Remote trace custody: xwing + MacBook Air

**Decision date:** 2026-10-08 America/New_York
**Decision purpose:** Close the missing encrypted retention and independently validated restore gap for two synthetic trace-first node executors.
**Decision:** Extend the *disabled-for-live-dispatch* shadow executor with a locked, read-only remote journal exporter and node-isolated encrypted, signed custody on NAS. No AssistX task activation, no general shell, no new network exposure, no OpenTunnel installation, no worker restart, no scheduler authority change.

## Hypothesis and expected outcomes

**Hypothesis:** Remote journals can be exported via authenticated private SSH, independently verified, backed up as encrypted ciphertext with separate node keys, and safely rejected if an earlier remote generation is later replayed.

**Prospective acceptance expectations for repeat runs:** Each node's second synthetic no-op produces two additional valid journal receipts; its second encrypted snapshot must preserve the earlier hash-chain prefix. A rollback from the first generation must be rejected after signing generation two. Independent restore checks must detect any ciphertext, signed-manifest, journal, or ledger modification. Missing encryption keys, an unsafe key mode, a missing signed ledger alongside existing archives, or a non-CIFS destination must deny a backup.

The above expectations were written down after the initial exploratory work. Do **not** represent them as preregistration for the completed exploratory tests. For repeat trials, use the exact expectations stated here before performing the trial.

## Architecture, paths, custody

The shadow path registry is `config/trace_execution_shadow_nodes.json`. The new modules are:

- `src/assistx/trace_execution_adapter.py`: exclusive-locked, byte-exact verified snapshot; existing fail-closed synthetic probe adapter remains.
- `scripts/trace_execution_shadow_export.py`: read-only JSON/base64 envelope with journal SHA-256, head hash and record count. Export requires exact physical node ID.
- `scripts/trace_execution_shadow_bootstrap.py`: generates distinct local GPG passphrase and HMAC anchor keys per node (mode 0600, root directories mode 0700); existing keys are never overwritten.
- `scripts/trace_execution_shadow_backup.py`: validates the independent remote payload and entire journal chain; refuses rollback against signed local custody head; GPG AES-256-encrypts *before* NAS write; decrypts locally and from staged NAS ciphertext; writes signed local anchor plus matching NAS manifest. `--verify-archives-only` reruns independent restore verification without remote SSH.
- `tests/test_trace_execution_shadow_backup.py`: network-free simulated node export, encryption/restore, signature continuity, rollback and bad-CIFS negative tests.

**Local-only keys (NEVER copy to NAS):**
```text
/home/scott/.config/fleet-trace-shadow/{xwing,scotts-macbook-air}/
  encryption.passphrase  # distinct node GPG symmetric key
  anchor.key             # distinct node HMAC key
```

**Local signed anchors:**
```text
/home/scott/.local/state/fleet-trace-shadow/{xwing,scotts-macbook-air}/
  signed-heads.jsonl
```

**Encrypted-only NAS custody:**
```text
/nas/desktop-commander-traces/trace-execution-shadow/{xwing,scotts-macbook-air}/
  <node>-<UTC>-<cipher_sha_prefix>.jsonl.gpg
  <node>-<UTC>-<cipher_sha_prefix>.jsonl.gpg.manifest.json
```

Each manifest includes the source journal hash and end-of-chain hash, the ciphertext SHA-256 and the HMAC signature over the manifest. NAS does not receive plaintext trace journals or encryption/anchor keys. Old generations remain available; the backup code does not delete old records.

## Measured evidence — October 8, 2026

| Check | xwing | scotts-macbook-air |
|---|---|---|
| Remote platform | Linux / Python 3.12 | macOS / Python 3.9 |
| Synthetic attempts logged | 2 | 2 |
| Trace records | 4 | 4 |
| Encrypted NAS generations | 2 | 2 |
| Encrypted generations independently restored | 2/2 | 2/2 |
| Duplicate synthetic claim | Rejected | Rejected |
| Wrong-node synthetic target | Rejected | Rejected |
| Live AssistX claim verified | **No** | **No** |
| Older archived generation replay | Rejected | Rejected |
| Private node journal permissions | 0700 directory / 0600 file | 0700 directory / 0600 file |

**Latest journal head hashes** (not signing keys):
- xwing: `dfe12bb89629a73ca670ad5f222020d2bb2092d149b19eb1b64c941365d8aa1e`
- scotts-macbook-air: `5138828f34bde19f46b3c61628a18e29a5039435b3190aa444a2cee40c211dcf`

**Latest signed custody heads** (HMAC values):
- xwing: `f1ed0dcd456183a750d08bf26d4bb597ac10c997ab48f44fd56ebd0d0c4a22ad`
- scotts-macbook-air: `d27237c8566f8725bb699da0986b30242ed299c0ad0b51517123732bacc1cacb`

The checked NAS filesystem was CIFS `//192.168.1.202/fileserver` mounted at `/nas`. The first and second backups for both nodes were verified via GPG decrypt-and-hash. The read-only historical-archive replay returned `remote_journal_rollback_or_rewrite` on **both** nodes. The code and tests do not claim independent immutable legal-grade custody.

## Repeatable checks

```bash
cd /home/scott/git/wt-assistx-trace-execution-20261007
PYTHONPATH=src python3 -m pytest -q \
  tests/test_trace_execution_shadow_backup.py \
  tests/test_trace_execution_adapter.py \
  tests/test_trace_execution_shadow_paths.py \
  tests/test_fleet_node_shell_gate.py \
  tests/test_fleet_node_recovery.py \
  tests/test_safe_fleet_executor.py \
  tests/test_fleet_executor_concurrency.py

# Read-only verification of remote live journals:
for node in xwing scotts-macbook-air; do
  PYTHONPATH=src python3 scripts/trace_execution_shadow_backup.py \
    --config config/trace_execution_shadow_nodes.json \
    --node "$node" --verify-remote-only

  # Read-only independent verification of all retained encrypted copies:
  PYTHONPATH=src python3 scripts/trace_execution_shadow_backup.py \
    --config config/trace_execution_shadow_nodes.json \
    --node "$node" --verify-archives-only
done
```

To capture **another** remote snapshot, omit the `--verify-archives-only` switch; this writes a new encrypted generation. The intentionally synthetic no-op is separately and explicitly invoked with `trace_execution_shadow_control.py --run-synthetic-noop`. Do not wire either into a live scheduling path yet.

## Remaining blockers / promotion requirements

1. **Authoritative execution:** No actual AssistX/Neo4j-issued, node-bound, expiration-checked and revocation-checked claim was used; shadow synthetic claim IDs explicitly identify themselves as not authoritative. Add real claim tokens with mutually authenticated node identity and stale lease rejection before expanding capabilities.
2. **Key recovery:** Encryption and anchor secrets are on x1-370 only. Escrow these securely on an independently owned root filesystem, verify an independent restore, and define rotation; no production disaster-recovery claim until then.
3. **Custody immutability:** Local HMAC heads are not external timestamping or write-once storage. Anchor heads to an independent immutable or WORM destination to resist privileged local tampering.
4. **Failure injection:** Full-disk, fsync failure, loss of connection mid-flight, crash between prepared and completed, and replay across revocation must be proven on the actual nodes.
5. **Scalability:** Current file reread/rehash per append and full snapshots per generation are O(n); move to bounded verified incremental sealing with capacity/error budget monitoring before high-rate traces.
6. **Reconciliation:** Keep physical node placement authority in AssistX, not in the shadow CLI or auto-router; no OpenTunnel exposure or unrestricted commands until promotion gates are signed off.

**Status:** Remote shadow custody and deterministic replay denial proven, live execution authorization NOT ACCEPTED. Published GitHub branch/PR NOT established; local isolated branch must be reconciled against remote history before publishing.
