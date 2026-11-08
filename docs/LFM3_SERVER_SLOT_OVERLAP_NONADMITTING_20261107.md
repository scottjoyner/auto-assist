# LFM3 actual server-side parallelism witness — non-admitting

**November 7, 2026, x1-370. Physical capacity evidence PASS; productive Task
admission HOLD.** The witness observes live server-side `/slots` state while
two already-loaded `llama-server` requests run simultaneously. It issues no
operator approval, no signed generation, no Task API call or resource claim.

## Physical execution and reproducible source

The LFM3 1.7B service on host loopback `127.0.0.1:19180` advertises model
alias `lfm3-local-test` and **four configured server slots**. Before each
experiment, all slot entries were idle; the existing backing GGUF had
already been independently read-only hashed to SHA-256
`3793e1e6e2266919d6e015287221b41100175a98eddaba92e111aed3b05c93c4`.
The separate host-loopback service `19181` and production AssistX containers
were not modified, restarted, reconfigured or granted any new access.

A first bounded ad-hoc test submitted two 96-output-token synthetic requests
on `19180`. Over **62 sampled `/slots` observations**, peak actual busy count
was **two simultaneously processing slots**. Both returned HTTP 200 and 96
completion tokens, taking **2551 / 2535 ms**; the slots returned idle. This
independently observed server-worker overlap, but did **not** prove the
responses had usable final text: follow-up metadata showed that LFM3 could
use 96 tokens entirely for `reasoning_content`, leaving final content empty.

The source script `scripts/lfm3_server_slot_overlap_preflight.py` therefore
adds **final nonblank-content validation** and checks the exact model alias,
JSON response shape, positive bounded token count, explicit four-slot boolean
occupancy, model health and idle state before starting. Initial strict tests
correctly returned HOLD at a 96- and sometimes 128-token ceiling. An isolated
single short-prompt trial at 128 tokens produced final content in 99 tokens,
but a parallel trial still exhausted 128 tokens with empty final content.
The final preflight uses the fixed minimal synthetic `Reply OK.` prompt with
**maximum 256 output tokens per request**; it does not log generated text or
reasoning content. Token ceilings limit work and are not latency SLOs.

**Final independent live script result** (`--live`):

| Physical observation | Value |
|---|---|
| Configured server slots | 4 |
| Actual `/slots` samples during requests | **88** |
| Maximum simultaneously busy server slots | **2** |
| First bounded generation | HTTP 200; **256 tokens; 5480 ms**; nonblank final text |
| Second bounded generation | HTTP 200; **91 tokens; 2443 ms**; nonblank final text |
| After final request | All four slots idle |
| Physical observation decision | **PASS_TWO_SERVER_SLOTS** |
| Signed Task slots granted | **0** |
| Task admission / process exit | **HOLD / code 2** |

The first response reached its configured maximum; it is *not* evidence of a
complete long-form answer or an output-quality pass. This is short local
physical overlap evidence only. Slot IDs are observed from the server but
**not cryptographically correlated** to the two request IDs; independent
outside work could theoretically race with the two requests. The idle
precondition and both completed request results reduce, but do not eliminate,
that uncertainty. A realistic sustained concurrency/thermal/memory soak and
fenced resource owner are still unverified. Configured four slots are not
four signed Task allocations.

## How to run (operator opt-in, host local only)

The command makes **no network requests by default** and intentionally exits
2/HOLD. Explicit `--live` performs two bounded local synthetic requests to
exact `http://127.0.0.1:19180`, with direct GET `/health`, `/v1/models`,
`/slots` and POST `/v1/chat/completions` only:

```bash
python3 scripts/lfm3_server_slot_overlap_preflight.py         # HOLD, no network
python3 scripts/lfm3_server_slot_overlap_preflight.py --live  # 2 local requests; exit 2/HOLD
```

No alternative URLs, model IDs, port remapping, Task IDs, credentials,
graph updates, dynamic prompts, arbitrary content or `--apply` option exist.
Responses are capped at 128 KiB, use no proxies and refuse redirects. Any
malformed model inventory, occupied slot before the test, missing slot signal,
invalid response or failure to observe overlap returns HOLD with a bounded
static error code, not generated content or exception strings.

The paired 34-case synthetic regression suite covers actual server-slot
interpretation, false-positive client-only parallelism, busy-after cleanup,
zero/boolean/malformed counts, absent/blank final responses, wrong model IDs,
strict route pinning, host-network-free default, and no change to signed Task
authority. The dedicated GitHub workflow has only `contents: read` and runs
**synthetic tests**, never a live model generation or fleet Task.

## Real release boundary remains HOLD

The canonical projection generation **896** is expired, with zero fresh
approved runtime/model/access/capacity/WorkSupply records. This witness may
support a **one-slot physical capacity proposal** but does not sign a slot,
provide a durable authenticated model route, prove signer/credential custody,
complete rollback, or authorize the CAS/COMMIT Task resource fence. Continue
to deny legacy machine Task calls. A designated owner must review fresh
attested source/profile evidence, approve a monotonic new generation and
actual resource budget, and supervise one Task before any productive rollout.
