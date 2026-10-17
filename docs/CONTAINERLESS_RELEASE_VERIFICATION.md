# Containerless release verification

Run `make release-verify` from a clean checkout with Python 3.12+, Bash and Git.
Override the interpreter with `PYTHON_BIN=/path/to/python`. No application imports,
package installation, Docker client, or daemon are required for this source gate.

The command checks Dockerfile failure propagation, COPY source existence, a final
`pip check`, deployment-script fail-fast settings, Python compilation, deployment
shell syntax, whitespace, and seven positive/negative build-contract tests.
CI runs it before dependency installation. Deployment also invokes the build
contract before Docker startup.

This is a source gate, not a complete release certification. It does **not**
resolve dependencies, build an image, verify external
downloads, exercise API routes, run the application suite, or establish live fleet
acceptance. Unsupported COPY syntax fails closed until the verifier is extended.
The existing CI unit and recovery jobs remain independent requirements.

## Real Compose rendering without containers

Run `make release-verify-compose` with Docker Compose 2.24.4+ installed. A standalone
Compose executable also works: `make release-verify-compose COMPOSE_COMMAND=/path/to/docker-compose`.
This uses Compose itself to merge all three canary files, including `!override`,
interpolate a temporary example environment, and produce normalized JSON. No daemon
is contacted and no containers are started. The renderer clears inherited environment
variables and prints only validation findings, never resolved configuration values.

CI runs this gate independently. Deployment validates its **actual** resolved JSON
through the same checker before building or starting any service. It checks service
identity, network isolation, loopback ports, internal service DNS, unsafe execution
flags, executor opt-in, and project-scoped volumes. Host binds and external volumes
are rejected. The canary overlay now removes inherited production source, SSH,
signing-key, NAS, SSD, and repository mounts. It uses the image's source code and
isolated durable state instead.

The executor profile is opt-in; the existing explicit `CANARY_START_HERMES=true`
service selection remains available. Code-improvement and signed executor stages
still need a separately reviewed contained workspace/key configuration; this gate
does not authorize adding production mounts. External Neo4j and inference endpoints
remain explicit configured dependencies, not proven isolated by this check. Runtime
health, secret validity, image dependency compatibility and actual canary workloads
still require their own acceptance evidence.

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
