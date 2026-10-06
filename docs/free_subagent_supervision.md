# Free Subagent Supervisor (Read-Only Slice)

Implemented in `scripts/free_subagent_supervisor.py` with fixtures in `tests/fixtures/`.

- Read-only with respect to routing/admission/dispatch/approval.
- Reuses `scripts/opencode-bridge` projection conventions (compact sorted JSON, `read_only`, advisory `suggested_scale_verdict`).
- Noninteractive stdin hazard documented: close stdin with `< /dev/null` when launching via fleet automation, else opencode init may wait.
- No secret values exposed; credential check returns boolean only.
