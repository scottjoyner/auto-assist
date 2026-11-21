# Mercury model-egress census -- research HOLD

Upstream source: cosmicstack-labs/mercury-agent, exact revision
1be98293ace567092b961346d95363284f6e71cf.

A read-only source scan found 20 apparent direct AI SDK invocation sites,
five provider-method calls, and two provider-local direct fetch sites.
The 27 total sites are code locations, not 27 distinct billed requests.

The checker compares a clean, exact source commit and line-oriented callsite
manifest. It denies unexpected changes, but even a matching manifest emits
SOURCE_MATCH_RESEARCH_HOLD and provider_execution_authorized=false.
It does not execute or wrap any provider, instrument streaming transport,
verify internal SDK networking, or prove complete egress discovery. Ordinary
fetch outside the providers directory is not classified and must separately
be reviewed before any release claim.

Execute only against the matching clean upstream repository:
python3 scripts/mercury_provider_egress_census.py --root UPSTREAM_PATH \
  --check-manifest docs/evidence/mercury-egress-census-20261024.json

Tests need only Python and git; no model, node installation, network, or
runtime worker. Release requires actual resource-side quota authority and
fail-closed guards across every audited and unaudited provider path, with
provider failures and fallbacks tied to independently witnessed traces.
Research pass is not production authorization.

