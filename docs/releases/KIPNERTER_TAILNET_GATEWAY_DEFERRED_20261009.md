# Kipnerter Tailnet gateway — explicitly deferred from AssistX RC2

Decision status: **review pending; feature not authorized for production deployment**.

- gateway_deferred=true
- production_deployment_authorized=false
- automatic_environment_mutation_authorized=false

## Why this test contract changed

On 2026-10-05 commit b31e6f86, a broad cleanup intentionally removed the
historical scripts/deploy-kipnerter-tailnet-gateway.sh and
.env.kipnerter-gateway.example. Tests for their contents survived and
therefore failed with FileNotFoundError. These files were not lost in the
October 9 routing/dashboard candidate.

Restoring the old helper would resurrect environment-file rewrites, API
service recreation and Tailnet Serve configuration behind an operator-supplied
Caddy-fence boolean, without proving that every ingress strips user-supplied
trusted identity headers. That is not acceptable evidence for production.

For this RC only, the **negative release contract** requires both obsolete
artifacts to remain absent. The existing configure and verify shell helpers
are still checked for syntax, scoped paths, identity verification and absence
of tailscale serve reset; no live invocation or gateway deployment is
authorized. The two obsolete content-based tests are replaced with explicit
retirement assertions rather than bypassed, skipped or marked xfail.

## Before enabling this feature in a later review

- Resolve [AssistX trusted-header issue #149](https://github.com/scottjoyner/auto-assist/issues/149)
  and verify a genuine, source-attested ingress/identity stripping policy.
- Validate exact source SHA, clean source checkout, independently approved
  Caddy policy, strict Tailnet authorization, and no unrelated Serve root
  mutation in disposable staging. A boolean environment flag alone is not
  acceptable proxy-provenance evidence.
- Produce a new safe deployment helper, placeholder-only config template,
  independent negative tests and an explicit operator approval artifact.
  Remove/replace this deferred assertion as part of that separately reviewed
  promotion, not as an incidental side effect of resolving CI.
- Keep [Kipnerter coordination #139](https://github.com/scottjoyner/auto-assist/issues/139)
  separate from the AssistX RC2 maintenance authorization. Raspberry Pi
  excluded from fleet outage and benchmark scope.

This document is **not a security exception**, deployment permission,
authorization to create real .env credentials, or approval to change running
services. Related CI blocker [#145](https://github.com/scottjoyner/auto-assist/issues/145).
