#!/usr/bin/env python3
"""Free-model subagent supervision slice — read-only to routing/admission.

Purpose:
  Make direct OpenCode free-model subagent execution safer and observable
  to a supervising agent without modifying authoritative routing, admission,
  dispatch, or approval paths. Reuses scripts/opencode-bridge conventions.

Provider-pool policy (read-only, advisory):
  Free-provider orchestration MUST prefer truly zero-cost / anonymous pools
  before any reserved quota. Preference order (most preferred first):
    1. kilo-anonymous  — Kilo anonymous pool (OpenAI-compatible base
       https://api.kilo.ai/api/openrouter, anonymous bearer, no credential).
    2. lmstudio-local  — local LM Studio inference (zero-cost, no network).
    3. openrouter-free — OpenRouter :free / account-free models (zero-cost
       but a shared, rate-limited free quota that still needs a credential).
    4. cohere-reserved — reserved zero-cost quota.
    5. zai-reserve     — Z.AI, reserve-only by policy: never selected unless
       explicitly requested (allow_reserve=True).

  Every candidate is classified with a provider state —
  usable / rate_limited / quota_exhausted / payment_required / unqualified —
  derived WITHOUT spending. A non-zero-price model is payment_required and is
  never auto-selected. Exact-model routes (a concrete model id such as
  ``zai/glm-4.7-flash``) are distinguished from router aliases (a generic
  router-selection token such as ``openrouter/free`` or ``kilo/auto``).

Noninteractive stdin hazard (Fleet Commander-style launches):
  If launched noninteractively (e.g., via a fleet commander or automation
  wrapper), stdin must be closed or redirected from /dev/null. Example:
      opencode run ... < /dev/null
  Otherwise opencode may wait at init for interactive input.

Supervision loop (--supervise):
  When --supervise is set, the supervisor acts on the projection:
  - hold verdict: terminates stale, duplicate, and looping subagent processes
  - healthy verdict: admits new subagents from the free pool
  Use --dry-run (default) to preview actions without executing them.

No API key values are emitted. No production routing mutations occur.
Secrets are not stored.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

from free_subagent_discover import (
    detect_duplicate_worktrees,
    detect_looping_records,
    detect_stale_records,
    discover_live_sessions,
    _annotate_trace_session,
    _opencode_db_uri,
)
from free_subagent_projection import emit_projection
from free_subagent_routing import (
    ANONYMOUS_POOLS,
    KILO_ANONYMOUS_BASE_URL,
    KILO_ANONYMOUS_BEARER,
    POOL_PREFERENCE_ORDER,
    PROVIDER_STATES,
    RESERVE_ONLY_POOLS,
    _FREE_PREFIX_PROVIDERS,
    _is_clearly_free_model,
    _least_severe_state,
    _now_utc,
    _price_is_zero,
    classify_free_model,
    enumerate_free_models,
    is_anonymous_pool,
    order_free_pools,
    pool_preference_rank,
    pool_tier,
    provider_state,
    qualify_provider_pools,
    route_kind,
)
from free_subagent_actions import (
    admit_subagent,
    cleanup_duplicates,
    cleanup_looping,
    cleanup_stale,
    run_supervision_loop,
    terminate_subagent,
)
from free_subagent_state import (
    DEFAULT_FIXTURE_PATH,
    DEFAULT_PROVIDER_POOLS_FIXTURE_PATH,
    DEFAULT_STATE_PATH,
    credential_present,
    inspect_subagents,
    load_state,
    register_subagent,
    save_state,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Free subagent supervisor (read-only).")
    parser.add_argument("--enumerate-models", action="store_true", help="List free model IDs")
    parser.add_argument("--credential-present", action="store_true", help="Report credential presence (bool only)")
    parser.add_argument("--inspect-state", action="store_true", help="Show subagent records (JSON lines)")
    parser.add_argument("--register-subagent", type=str, default="", help="JSON record to register")
    parser.add_argument("--detect-duplicates", action="store_true", help="Detect duplicate worktrees")
    parser.add_argument("--detect-looping", action="store_true", help="Detect looping session registrations")
    parser.add_argument("--detect-stale", action="store_true", help="Detect stale subagent records")
    parser.add_argument("--projection", action="store_true", help="Emit supervision projection")
    parser.add_argument("--supervise", action="store_true", help="Act on projection: terminate stale/duplicate/looping (hold) or admit subagents (healthy)")
    parser.add_argument("--max-age-minutes", type=int, default=30, help="Stale record threshold for supervise (default 30)")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Preview actions without executing (default True)")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Actually execute supervise actions")
    parser.add_argument("--admit-count", type=int, default=1, help="Max subagents to admit when healthy (default 1)")
    parser.add_argument("--provider-pools", action="store_true", help="Emit qualified provider pools (read-only)")
    parser.add_argument(
        "--allow-reserve", action="store_true", help="Explicitly request reserve-only pools (e.g. Z.AI)"
    )
    parser.add_argument("--trace", type=pathlib.Path, default=None, help="Use trace exporter for projection (read-only)")
    parser.add_argument("--fixture", type=str, default=str(DEFAULT_FIXTURE_PATH), help="Fixture path")
    parser.add_argument("--pools-fixture", type=str, default=str(DEFAULT_PROVIDER_POOLS_FIXTURE_PATH), help="Provider-pools fixture path")
    parser.add_argument("--state", type=str, default=str(DEFAULT_STATE_PATH), help="State file path")
    args = parser.parse_args(argv)

    state_path = pathlib.Path(args.state)
    fixture_path = pathlib.Path(args.fixture) if args.fixture else None

    if args.enumerate_models:
        for m in enumerate_free_models(fixture_path):
            print(
                json.dumps(
                    {
                        "id": m.get("id"),
                        "tags": m.get("tags", []),
                        "free_model_type": m.get("free_model_type", classify_free_model(m)),
                    }
                )
            )
        return 0

    if args.credential_present:
        print("true" if credential_present() else "false")
        return 0

    if args.register_subagent:
        try:
            rec = json.loads(args.register_subagent)
            records = register_subagent(rec, state_path)
            print(json.dumps({"registered": True, "total_records": len(records)}))
        except Exception as e:
            print(json.dumps({"registered": False, "error": str(e)}))
            return 1
        return 0

    if args.inspect_state:
        for rec in inspect_subagents(state_path):
            print(json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.detect_duplicates:
        dups = detect_duplicate_worktrees(inspect_subagents(state_path))
        print(json.dumps({"duplicates": dups}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.detect_looping:
        loops = detect_looping_records(inspect_subagents(state_path))
        print(json.dumps({"looping": loops}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.detect_stale:
        stale = detect_stale_records(inspect_subagents(state_path))
        print(json.dumps({"stale": stale}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.provider_pools:
        pools_path = pathlib.Path(args.pools_fixture) if args.pools_fixture else None
        models = enumerate_free_models(pools_path)
        qualified = qualify_provider_pools(models, allow_reserve=args.allow_reserve)
        print(
            json.dumps(
                {
                    "read_only": True,
                    "allow_reserve": args.allow_reserve,
                    "kilo_anonymous_base_url": KILO_ANONYMOUS_BASE_URL,
                    "preferred_pool": next(
                        (p["pool"] for p in qualified if p.get("state") == "usable" and not p.get("reserve_only")),
                        None,
                    ),
                    "provider_pools": qualified,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        return 0

    if args.projection:
        proj = emit_projection(
            free_models=enumerate_free_models(fixture_path),
            state_path=state_path,
            trace_exporter_path=args.trace,
            allow_reserve=args.allow_reserve,
            discover_fn=discover_live_sessions,
        )
        print(json.dumps(proj, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.supervise:
        proj = emit_projection(
            free_models=enumerate_free_models(fixture_path),
            state_path=state_path,
            trace_exporter_path=args.trace,
            allow_reserve=args.allow_reserve,
            discover_fn=discover_live_sessions,
        )
        result = run_supervision_loop(
            projection=proj,
            state_path=state_path,
            max_age_minutes=args.max_age_minutes,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    proj = emit_projection(
        free_models=enumerate_free_models(fixture_path),
        state_path=state_path,
        trace_exporter_path=args.trace,
        allow_reserve=args.allow_reserve,
        discover_fn=discover_live_sessions,
    )
    print(json.dumps(proj, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())