# Free local inference: capacity vs residency vs output quality

November 3, 2026, morning Eastern time. DRAFT research only.
No existing scheduler, production router, model residency, lease, or
operator-signed admission was modified. No paid APIs were used.

## Why this read-only selector exists

API health, installed model catalogs, process counts and a successful
generation are four different measurements. Maximum token generation
is NOT itself productive utilization if the result is false, a
refusal, truncated, or fails an exact source-evidence constraint.
The existing Xwing manual quality hold remains intact. This tool
does not run or automatically admit any model or workload.

    python3 -B scripts/fleet_local_safe_slot_readonly.py

This command performs two short, spaced resource observations on
x1-370 and the five reviewed strict-SSH hosts xwing, Lenovo,
Deathstar, Joyner, Destroyer. It reads:
- MemAvailable and SwapFree in MiB from /proc/meminfo
- full memory/io PSI avg10 and IO full avg60
- root available disk in GiB
- fixed, bounded, loopback-only model catalog GETs

It prints a bounded JSON advisory and deliberately exits status
**2**, even if a manual research qualification candidate exists.
No POST, model load, generation, tool calls, task claims, worker
registration, model unload, file writes on remote nodes, sudo, paid
provider connection, route config changes or timer changes occur.

All SSH targets are hardcoded and strict host-key checking, no
agent forwarding, no control-master reuse, no local forwarding,
noninteractive auth and timeouts are enforced. The remote response
echoes a fresh nonce and host mode and is parsed with typed finite
bounded metrics, exact keys and duplicate-JSON-key rejection.

Hard limits per node (both measured samples must pass): >=1,024 MiB
free swap, >=5 GiB root, memory PSI full avg10 below 1 percent,
IO PSI full avg10/60 below 5 percent. RAM minimum is:
- 2,500 MiB Xwing
- 3,000 MiB Lenovo, Joyner and Destroyer
- 4,096 MiB Deathstar and x1-370

Those are conservative research qualification floors, not permission
to run inference. X1 always stays HOLD because GPU lease and
canonical authority are NOT established by this probe. Destroyer
stays HOLD without a reviewed serving endpoint. OpenAI-compatible
Lenovo/Joyner model IDs are advertisement, NOT physical model mapping.
Deathstar's installed Ollama 0.6B file is NOT the same as resident
inference; a transient model load still needs separate manual checks.

The three possible outcomes are:
- HOLD: resource, endpoint or lease-admission information is missing
  or explicitly blocked
- MANUAL_QUALITY_QUALIFICATION_ONLY: resources presently pass for
  a limited, separately quality-reviewed experiment on Lenovo or
  Deathstar (or Joyner if it recovers); no unattended work
- MANUAL_SHADOW_REQUALIFICATION_ONLY: Xwing has a current exact loaded
  instance and resources presently pass; its existing scheduled
  two-repeat quality regression HOLD stays active

In ALL states, can_run_scheduled_research=false,
can_dispatch_production=false, and
requires_human_quality_review=true. A report has no authority to
dismiss the existing Xwing manual hold.

## Direct actual fleet observations

Two-sample safe-slot probe verified 6/6 targets. The first pass
observed no immediate manual candidates due to Xwing RAM/PSI,
x1 and Joyner swap, and model residency limitations. A later pass
found manual QUALIFICATION candidates Xwing, Lenovo and Deathstar,
but not a single *scheduled or production-admitted* target. Resource
conditions changed over minutes, so this status cannot be cached.

Some bounded local inference DID run under separately checked
resource conditions:
- Xwing: earlier native reasoning-off issue triage had three
  completed jobs, 319 input + 287 output tokens, one syntactic
  human-review draft and two deterministic quality failures.
- Xwing additional private-source issue pair: 102 input + 160 output
  tokens, but the model incorrectly called real issue IDs fake and
  refused to perform the review. Original reference-only validator
  produced a false syntactic pass; post-run assistant review REJECTED
  that text. PR #511 is patched with regression tests to reject
  refusal/prompt meta and reaching the full output cap.
  The original append-only receipt is deliberately retained as
  historical filter output, not retroactively rewritten.
- Deathstar: two local 0.6B Ollama JSON classification attempts
  under resource checks, 179 prompt + 102 output tokens. BOTH were
  rejected by exact-evidence/label validation; one misclassified
  a RyzenAI NPU issue as node authentication. Model keep_alive=0s.
- Lenovo: two local LFM extraction attempts under resource checks,
  203 prompt + 66 output tokens. BOTH failed exact-format and
  verbatim-evidence checks.

**No claimed total fleet usage:** These are distinct, bounded direct
research executions with limited receipts; the existing earlier
9Router receipt chain and production router ledger are separate.
No additive 24h fleet total is proven. The larger audit observed
9/10 hosts and zero trusted direct per-host token-counter endpoints.

Current decision: DO NOT turn on autonomous recurring Deathstar
classification or Lenovo extraction. Their observed acceptance in
this round is 0/2 each. Even Xwing's recent syntactic pass was not
reliably useful. Work on factuality checks, source-grounded tasks
and independent human review before enabling unattended scheduling.

## Work necessary to increase useful utilization

1. Retain the installed model/lease/auth gates; select only nodes
   with current resource evidence, not available-model counts.
2. Improve source-grounded, independently checked workload success.
   An accepted draft is NOT permission to mutate an issue or graph.
3. Add verified, attributable per-request token metrics and
   quality outcomes to separate local research and production ledgers,
   without double counting or fabricating token counts for embeddings.
4. Keep existing Auto-Assist machine auth #501, signed canonical
   generation 896 expiry, independent custody and x1 runtime version
   gates separate. No production dispatcher can be activated from
   this advisory.
