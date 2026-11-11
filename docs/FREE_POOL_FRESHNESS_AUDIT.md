# Free-pool local evidence freshness (read-only)

## Purpose

The historical free-pool projection and its refresh receipt are **not** the
15-minute passive provider-presence state. A fresh provider health check or
subagent lifecycle event cannot renew model qualification evidence.

The current-main supervisor now exposes an *audit-only* subcommand:

```sh
python3 scripts/free_subagent_supervisor.py --free-pool-freshness
```

In offline tests you may supply a disposable fixture directory using
`--free-pool-state-root /path/to/fixture`. This does not modify that directory.

## Evidence

The audit checks only two local JSON objects under
`~/.local/state/auto-assist/subagents/` (resolved through the system account):

- `free-pool-projection.json`: the expected v1 schema, `read_only=true`,
  `authoritative_routing_unchanged=true`, regular bounded file and recency.
- `last-free-pool-refresh.json`: expected v1 schema, successful historical
  execution, `paid_fallback=false`, regular bounded file and recency,
  plus independently checked `finished_at` recency.

Missing, malformed, symlinked, oversized, stale, and future-dated local
files produce `local_freshness=stale_or_unknown`. Merely touching an old
refresh file does not make its old `finished_at` current.

Even if all locally examined fields are recent, the decision is always
`HOLD` with `dispatch_allowed=false`, no account entitlement or
provider billing proof, no shared-lease proof, and no inference.

Only fixed status labels are printed. The audit never emits model lists,
prompts, raw JSON, account/credential contents, endpoints or file paths.

## Boundaries and adoption

- The flag is observational only. Its handler does not invoke provider
  enumeration, generation, supervisor loops, admission, or restarts.
- `--free-pool-freshness` combined with active-supervision flags is denied.
- Existing `--supervise --no-dry-run` denial remains unchanged.
- No new systemd timer or daemon; no live route/credential/billing changes.
- This code lives on a review branch; it is not deployed to production.
- Local recency is an advisory observation, never independently signed source
  custody or permission for routing. The provider/account/lease gates stay HOLD.

## Validation

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest -q -p no:cacheprovider \
  tests/test_free_pool_freshness.py tests/test_free_subagent_supervisor.py
```

Issue: https://github.com/scottjoyner/auto-assist/issues/584
