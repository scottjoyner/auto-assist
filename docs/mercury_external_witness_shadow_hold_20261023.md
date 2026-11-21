# Mercury separate spend-witness shadow — research, NO-GO

This synthetic Python-stdlib model extends [PR #239](https://github.com/scottjoyner/auto-assist/pull/239).
It is NOT imported by the Mercury service, SDK, AssistX, router, provider
dispatch, NAS writer, or agent runtime. It opens ONLY disposable local SQLite
files. No network, credentials, services, model calls, or authority deployment.

## Question and evidence
The older physical two-host trial proved that copying the coordinator SQLite DB
made x1-370 and Xwing grant conflicting leases to the same upstream quota group.
This separate mock makes both coordinators consult one *external* witness for
each slot allocation, epoch and shadow effect-time check. Two calls on the same
witness cannot admit overlapping slots; losing witness reachability must deny.

The shadow result ALWAYS has dispatch_authorized=false and provider_calls=0.
"SHADOW_ADMITTED_ONLY" and "SHADOW_EFFECT_ONLY" are simulated policy decisions,
NOT signed grants, quotas, real provider or physical cancellation evidence.

## Intentional residual negative
The fixture's SQLite witness file is itself copyable. The offline regression
explicitly backs it up and proves two independent copies can BOTH return
SHADOW_ADMITTED_ONLY. There is NO distributed consensus, immutable/high-water
custody, real cryptographic client identity, trustworthy external clock, owner
revocation, vendor-side effect-time enforcement, multi-writer quorum, TLS or
partition-safe failover here. A provider already processing an in-flight stream
could keep billing after a simulated epoch change.

## Acceptance scope
Run without network or provider access:

    cd integrations/mercury/research
    PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q test_shadow_shared_spend_witness

Prove distinct authority replicas cannot both spend using an independently
authenticated, noncopyable global leader/fence first; reject ambiguous takeover
and uncertain ACK; then test signed multi-node cancellation and restore under
an external custodian. Keep production and all live free-provider admission on
HOLD until separately authorized.

