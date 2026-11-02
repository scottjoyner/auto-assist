# AssistX runtime-admission live-proof freshness — draft

Production admission remains NO-GO. This patch does not deploy or approve
a fleet runtime or change the canonical Neo4j graph.

## Existing proof-age gap

The candidate builder previously accepted an old runtime/model/path
observation as long as its supplied expiry was future-dated. A stale
PID or network path could thus be presented as fresh enough to create
a new lease. The timing check did not bind lease expiry to observed age.

## Non-self-authorizing hardening

- Accept an observation at most **5 minutes** old at candidate creation.
- Reject proofs with more than **15 minutes** of declared lifetime.
- Cap the candidate lease to the EARLIEST of: requested lease end,
  supplied proof expiry, and observation time plus five minutes.
- Revalidate age, proof expiry and lease bound on candidate consumption.
- Preserve the existing minimum 30 seconds of remaining lease.
- Reject future observations, expired proofs and tampered candidate data.

## Evidence scope and limitations

Freshness is NOT authenticity. The existing contract still accepts
supplied proof fields; this patch does not establish a signed
source-attested PID, socket owner, loaded GGUF model, resource claim,
runtime version, operator approval or boot-recovery evidence. An API
catalog alone is not physical runtime attestation.

At last read x1-370's serving API advertised zero K2 instances.
Installed LM Studio 0.4.28+2 differs from reviewed K2 restorer version
0.4.26+4. Canonical generation 896 is expired, so production remains
NO-GO independently of this research patch.

Synthetic regression tests cover valid, stale, future, excessive-lifetime,
expired and tampered snapshots, minimum lease duration, and candidate
revalidation. No graph writes, real model loads, paid APIs or live traces
are part of the test.

Before any production integration, independently verify the five-minute
bound fits end-to-end physical attestation latency and ensure all
consuming services enforce equivalent constraints. Do not relax
security gates solely to unblock dispatch.
