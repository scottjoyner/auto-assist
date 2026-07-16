# Execution Authority Reconciliation (W-22)

auto-assist historically ran **two** execution authorities in parallel:

1. **Paperclip cutover** — `src/assistx/paperclip_poller.py` polls Paperclip
   for issue/run state and reconciles it into AssistX task state.
2. **Direct `hermes_agent_adapter` poller** — `src/assistx/agents/hermes_agent_adapter.py`
   (`run_loop`) polls AssistX directly for available tasks and executes them via
   the local hermes binary.

Running both at once risks double-execution and divergent task state. This doc
records the reconciliation plan and the implemented single config-gated switch.

## Implemented switch

`config.py` exposes `settings.execution_backend`, sourced from the
`EXECUTION_BACKEND` env var (default `auto`). `worker.py:_start_execution_pollers()`
branches on it and starts the matching poller(s); `worker.main()` emits a single
startup log line stating the active backend and which pollers are live:

```
execution authority active backend=auto (paperclip=True, direct=True) pollers=2
```

The switch matrix:

| `EXECUTION_BACKEND` | Paperclip poller | Direct hermes poller |
|----------------------|------------------|----------------------|
| `paperclip`          | yes              | no                   |
| `direct`             | no               | yes                  |
| `auto` (default)    | yes              | yes                  |

Default is `auto` to preserve existing behavior (both were previously started
out-of-band). To converge, pick one:

- **Paperclip as permanent authority** → set `EXECUTION_BACKEND=paperclip` once
  the hermes adapter routes through Paperclip (see hermes-agent W-80).
- **Direct authority** → set `EXECUTION_BACKEND=direct` if the hermes adapter
  remains the execution host.

## Required follow-up

- Remove the `auto` fallback once a single authority is chosen in production.
- Ensure idempotency (`idempotency_store`) prevents double-execution if both are
  ever enabled.
- hermes-agent: finish Paperclip registration (W-80) so `paperclip` mode is the
  coherent long-term path.
