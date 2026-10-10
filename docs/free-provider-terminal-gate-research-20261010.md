# Free-provider terminal acceptance — independent offline checkpoint

**Date:** October 10, 2026. **Decision:** RESEARCH / NO-GO for production admission.
**Implementation:** `scripts/free_provider_terminal_gate.py` and
`tests/test_free_provider_terminal_gate.py` on branch
`research/free-terminal-acceptance-20261010` from existing auto-assist
`origin/main`. No model calls, paid credits, provider keys or fleet deployment.

## Why this slice exists

The existing research launcher `scripts/guarded_free_subagent.py` is not
yet on auto-assist `main`. A previous guarded Kilo session incorrectly
reported completion despite **1,964 output tokens against a 1,800-token
hard acceptance limit**, because the original process polled tokens while
running but did not independently reconcile the final state.

The separate prototype branch `fix/guard-final-budget-gate-20261010`
contains commits `0d9eb661` and `ce2d3e5e`, adding terminal post-exit
budget/error rejection and model/manifest reconciliation. On x1-370 its
focused `test_guarded_free_subagent.py` and `test_post_exit_guard.py`
completed **40 passing tests**. That source and its prerequisites are not
proven to be in current `main`, so transplanting the whole launcher
would conflate research architecture and production authority.

This additive `main`-based module instead checks immutable final evidence
**without importing the prototype or changing any active route**. A consuming
launcher would still need a separately reviewed, fail-closed integration.

## Algorithm and assertions

Given a complete private manifest, raw stdout JSONL, raw stderr and
an explicit exact `:free` model ID and numerical limits, the evaluator:

1. Recomputes manifest/stdout/stderr SHA-256 and checks raw byte counts.
2. Checks final provider `kilo_free`, requested/effective exact model,
   `model_identity_complete=true`, completed status, stop reason and
   locally *reported* zero cost.
3. Replays `step_finish`, `tool_use`, `text` and `error` events.
   Rejects malformed JSON, non-object records, missing step tokens,
   missing text/completion and *any* provider error event.
4. Reconciles **input, output and reasoning** tokens from the raw stream
   against terminal manifest fields.
5. Applies **post-exit** input/output/step/tool caps (inclusive) to actual
   recorded counters. Rejects mismatches, missing counters, denied or paid
   identity and all other inconsistent or incomplete evidence.

The only positive status is `LOCAL_RECEIPT_PASS`. It never represents
verified upstream pricing, distributed quota authority, accepted model
output quality, signed off-host trace retention or safe agent delegation.

## Actual nonsecret replay (read-only, x1-370)

| Session | Model | Input / output / reasoning | Post-exit decision | Raw stdout SHA-256 |
| --- | --- | --- | --- | --- |
| `ses_edcb9c7d3ffeV2TIMB1kGuQrrS` | `cohere/north-mini-code:free` | 18,810 / 963 / 649 | `LOCAL_RECEIPT_PASS` at 50K/1.8K/3 steps/5 tools | `ba8001de86558b99017f827077b1bc2941dacdbb41b8e644d9b91be11051023a` |
| `ses_edcb74dbaffe1vfFJxGy8ujZcm` | `nvidia/nemotron-3.5-lightning:free` | 12,961 / **1,964** / 2,393 | `REJECT: budget_output` | `0c700f0f629c9e8c4ce10a87545aae07677d2073f4be977296340e5a6bdb7b77` |
| `ses_edc76151bffeGfMGrZ2jt5IlPV` | `cohere/north-mini-code:free` | 0 / 0 / 0 | `REJECT:` missing stop, incomplete manifest, missing text and step, provider error | `9573a14b76f2b239cd66272b2fcf8de565b9cfe5bff1ef2484e4d4d4ead3daaf` |

Manifest SHA-256 digests respectively:
`b51376281d98175a26647703f66d65ff8d5825c2bb8a8fe267bd99c6cd5adcfe`,
`911c2a5a620ef3506b57c50ab45bedec712a8f1fe9750ad54b310d9b5284d1c4`,
`8683447d2daa3ca8b1e9df1367c32a42a4a927d84b3428ad1100b8712b7f5913`.

Raw prompts, private logs and any credentials are *not* committed.
The third session's private event had one provider error, without a proved
specific authorization HTTP status; we do not invent a cause.

## Tests and boundaries

- `python3 -m pytest -q tests/test_free_provider_terminal_gate.py` —
  **24 synthetic tests passed** on October 10, 2026; initial test fixture
  `None` was corrected to an actual unknown-status error event.
- `python3 -m py_compile scripts/free_provider_terminal_gate.py` — PASS.
- Three actual retained private sessions replayed read-only — one local
  receipt pass and two rejection outcomes, as specified above.
- `git diff --check` — PASS.

**Remaining P0 gates:** upstream cost/credit entitlement and per-provider
quota are unverified; a host-local guard does not enforce fleet-wide shared
upstream quotas; final counter limits are admission *acceptance*, not a
guaranteed token-spend hard stop; OpenCode's tool permissions alone are not
an OS sandbox; independent archive and restore receipts are unverified.
A model output is not substantively correct just because its hash/usage pass.
Previous rejected Nemotron text incorrectly described `zfs receive -F`
as a read-only verifier. Do not execute that operation.

Keep x1's unattended free-agent execution disabled; maintain 15-minute
catalog health and read-only monitoring independently. This work authorizes
no live model call, production routing, source deletion, NAS5 write,
provider retry, credential release or merge.