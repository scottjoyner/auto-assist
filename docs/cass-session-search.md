# CASS session-search adoption

CASS is an observational search index over coding-agent history. It is not an authoritative memory store, scheduler, router, or execution record.

The fleet pilot pin is cass 0.10.0. The upstream project currently labels itself alpha and uses an MIT plus OpenAI/Anthropic rider license, so rollout remains isolated until that rider is reviewed for fleet-wide use.

CassSessionSearch verifies the pinned binary and runs CASS selftest, which must report functional=true and archive_accessed=false. Searches always use lexical mode, JSON robot output, a bounded result/token budget, and --no-maintenance. The adapter never invokes cass index, doctor --fix, model installation, daemon auto-spawn, refresh, or other mutating maintenance paths.

A timed-out CASS budget is reported as partial-timeout, not as proof that no matching session exists. maintenance-required is likewise surfaced as an incomplete state and requires a separate operator-approved indexing/maintenance action.

The existing cass-memory adapter remains a separate optional procedural-evidence layer. Session search should be proven useful first before any learned-memory promotion is considered.
For fleet deployments, pass the dedicated derived index directory with data_dir; the adapter emits the CASS --data-dir flag together with --no-maintenance, so searches remain read-only and never refresh or repair the index implicitly.
