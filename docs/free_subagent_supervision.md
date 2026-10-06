# Free Subagent Supervisor (Read-Only Slice)

Implemented in `scripts/free_subagent_supervisor.py` with fixtures in `tests/fixtures/`.

- Read-only with respect to routing/admission/dispatch/approval.
- Reuses `scripts/opencode-bridge` projection conventions (compact sorted JSON, `read_only`, advisory `suggested_scale_verdict`).
- Noninteractive stdin hazard documented: close stdin with `< /dev/null` when launching via fleet automation, else opencode init may wait.
- No secret values exposed; credential check returns boolean only.

## Kilo anonymous OpenCode lane

The reusable OpenCode provider fragment lives at `config/opencode-kilo-anonymous.provider.json`.

The pilot exposes two selectors:

- `kilo/kilo-auto/free` for resilient free routing; resolved-model attribution is required before treating real work as model-attributed.
- `kilo/nvidia/nemotron-3-ultra-550b-a55b:free` for an exact free route when concrete model attribution is required.

Do not infer that arbitrary `kilo/*` models are free. Plain-model discovery only special-cases the exact `kilo/kilo-auto/free` alias; other Kilo models require an explicit `:free` suffix or zero-cost pricing evidence.
