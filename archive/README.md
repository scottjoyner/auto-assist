# archive/

Dead, orphan, duplicate, or secret-bearing files are moved here (never deleted)
during the unified-fleet remediation. See `docs/LLD_UNIFIED_FLEET.md` and
`docs/HLD_UNIFIED_FLEET.md`.

## Contents

- `.env.committed-SECRETS-REMOVED` — A copy of the previously committed `.env`
  with all real secret values scrubbed to `<REQUIRED>`. The live `.env` is
  gitignored and must be injected via a secret manager. Real secrets were
  formerly baked into `config.py:14` and `runtime.py:61` defaults; those
  defaults were removed (see W-25).

## Why files are moved, not deleted

Preserving history-of-intent: these files may still inform later cleanup or
debugging. They are excluded from the package and CI.
