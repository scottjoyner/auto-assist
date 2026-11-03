# Xwing useful free-local issue triage — November 3, 2026

**Manual one-shot, research-only, nonproduction. DRAFT; production HOLD.**

This is NOT a worker registration, agent claim, scheduler patch, model
loader, GPU lease override, external API adapter, independently
administered witness, or authorization to change AssistX/Auto-Router
routing. The existing 9Router Xwing manual-review quality hold is NOT lifted.

## Verified fleet context

On x1-370, LM Studio showed 11 available models and one native loaded
instance at ~09:09 EST, but neither production runtime approval nor
physical process attestation is established. Swap free was ~16 MiB;
both observed GPU leases remain owned by predictive-finetuning.
Production Auto-Router was degraded with zero enabled free hosted
providers, and signed canonical generation 896 remained expired.
Do NOT add load or restart K2 recovery on x1.

Xwing has an already-loaded Ornith 1.5 35B A3B APEX MTP instance
with four configured parallel slots and a Nomic embedding instance.
Its native model metadata permits per-request reasoning off; on is
the default. The native reasoning-off call generated useful visible
issue-review notes where OpenAI-compatible requests consumed 72 or
256 tokens in reasoning with no visible answer.

Strict pinned-host SSH to Xwing succeeds. At the start, ~2.7 GiB
MemAvailable, ~16.5 GiB SwapFree, memory and I/O pressure zero
were observed. Later, MemAvailable fell to ~2.4 GiB, below the
required 2,500 MiB floor: the next task was NOT sent.

## Manual invocation

Default dry run sends ZERO model requests and writes no receipts:

    printf '%s\n' '{"pairs":[{"a":{"id":"AA428","title":"Enforce Hermes claim ID and revocation fencing"},"b":{"id":"AR76","title":"Caller metadata can suppress claim fencing"}}]}' |
        python3 -B scripts/xwing_useful_shadow_triage.py

Explicit manual one-shot research call, NO schedule installed:

    printf '%s\n' '{"pairs":[{"a":{"id":"AA428","title":"Enforce Hermes claim ID and revocation fencing"},"b":{"id":"AR76","title":"Caller metadata can suppress claim fencing"}}]}' |
        python3 -B scripts/xwing_useful_shadow_triage.py --execute

Use only already independently verified, non-sensitive GitHub issue
titles. Never feed secrets, user records, protected internal issue
bodies or attachments to this tool. Results are review-only
suggestions, not unattended work. At most TWO distinct issue pairs
per invocation, processed sequentially, no parallel inference.

The sender uses pinned xwing SSH host keys, no agent forwarding,
no dynamic host selection, no shell task commands, and one fixed
already-loaded native model ID. The remote worker rechecks the
current host's RAM, swap, memory/IO pressure, root disk and ONE
exactly loaded native Ornith instance before making a request.
Hard floors: at least 2500 MiB RAM, 1024 MiB swap and 5 GiB
root free; memory PSI full avg10 below 1 percent; IO PSI full
avg10 and avg60 below 5 percent. Missing evidence forces HOLD.

The ONLY generation request uses local native POST /api/v1/chat,
fixed model, reasoning off, 160 maximum output tokens, temperature
zero, store false, no tools/integrations and no streaming.
It never invokes model load/unload, hosted APIs or production routes.

Output must contain both issue IDs and OPEN status, sufficient
text, measured prompt/completion counts and zero reasoning tokens;
unsupported approval and restoration claims are rejected.
Passing this filter is NOT a truth guarantee. Outputs are
HUMAN_REVIEW_REQUIRED drafts, not deployment approvals.

## Research receipt ownership and unknown outcomes

Local research-only append+fsync SHA-256 linked receipt file:

    ~/.local/state/assistx-xwing-useful-shadow/receipts.jsonl

Owner-only directory 0700 and regular file 0600, no symlinks,
exclusive flock. Receipts store only issue pair SHA-256,
START/DONE stages, success boolean, tokens, timestamp and hashes.
NO raw issue title, prompt, generated completion, private credential
or URL is placed in the receipt ledger. Malformed/altered history
blocks more work. Repeated issue-pair digests are deduplicated,
including failed quality checks, so no repeated token spend.

An unambiguous pre-inference HOLD writes no START. Unknown outcome
after START leaves an unmatched START and forces subsequent runs
to HOLD pending independent review; it does not forge DONE or retry.
A later task's resource HOLD preserves earlier completed results
and returns exit 2. The receipt chain is under shared fleet
administration, NOT independently custodied or tamperproof, and
must not be mixed with existing 9Router or production receipts.

## Measured bounded results

Three distinct public open-issue pair tasks completed while
resources permitted. They produced 319 prompt + 287 output = 606
reported tokens, with ONE accepted human-review draft and TWO
quality rejections. A fourth task was stopped before inference
when RAM fell under 2,500 MiB. No incomplete START remains.
Repeating the original pair set used ZERO additional tokens.

The accepted draft connected Auto-Assist issue 428 and
Auto-Router issue 76 as unresolved claim-fencing blockers.

Previous ad hoc Xwing native research produced three short
visible notes (243 output tokens), and Nomic embedded 24 current
open issue titles. Those earlier calls were NOT in this receipt
ledger. The broader fleet read-only audit surveyed nine of ten
known nodes and found zero direct token-counter endpoints:
TOTAL 24-hour fleet-wide tokens cannot be established from
these partial counters.

## Next utilization improvement with explicit guardrails

1. Review this research-only source and tests. The old scheduled
   9Router two-repeat-failure hold remains in effect.
2. Require independently checked results on multiple real tasks
   plus healthy RAM/swap/PSI/disk and stable model identity before
   any separate approval for ongoing/scheduled research use.
3. Make per-request attributable token receipts available across
   fleet nodes, without adding incompatible ledgers together.
4. Solve node machine-principal authentication (Auto-Assist #501),
   expired signed admission, physical runtime witnesses and
   independent anti-rollback custody before PRODUCTION dispatch.

This work did not use paid providers or alter production graph,
routes, credentials, timers, services, model loads, GPU leases,
signing keys, source security policy or private trace data.
