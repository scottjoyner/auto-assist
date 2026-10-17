# Containerless release verification

Run `make release-verify` from a clean checkout with Python 3.12+, Bash and Git.
Override the interpreter with `PYTHON_BIN=/path/to/python`. No application imports,
package installation, Docker client, or daemon are required for this source gate.

The command checks Dockerfile failure propagation, COPY source existence, a final
`pip check`, deployment-script fail-fast settings, Python compilation, deployment
shell syntax, whitespace, and seven positive/negative build-contract tests.
CI runs it before dependency installation. Deployment also invokes the build
contract before Docker startup.

This is a source gate, not a complete release certification. It does **not** render
the merged Compose model, resolve dependencies, build an image, verify external
downloads, exercise API routes, run the application suite, or establish live fleet
acceptance. Unsupported COPY syntax fails closed until the verifier is extended.
The existing CI unit and recovery jobs remain independent requirements.

## Remaining release evidence

- Resolve and lock the current runtime dependency set, including Hermes and the
  Python image digest. Do not restore the historical proposed Hermes 0.19.0 pin
  without proving compatibility with today's source.
- Build the final image and pass `pip check` after Hermes installation. A conflict
  now fails the build instead of silently shipping an inconsistent environment.
- Render the exact Compose overlays with deployment environment values and check
  network, port, mount, identity, and secret isolation. Do not publish rendered secrets.
- Run the deployed route/readiness probes and staged workload canaries from the
  deployment runbook. Preserve rollback and evidence requirements.

PR #3 was merged on July 30, 2026. These checks are a follow-up against current
main, not a recovery of the uncommitted historical verifier. Passing this command
does not authorize deployment, recovery mutation, or patch promotion.
