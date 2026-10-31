# AssistX claimed-payload binding — draft negative acceptance

This is a development-only incremental patch **on top of** integrated draft PR #455 (exact base head `9f7094a4a208ab2b1205142ab2279116f650a5b3`). Do not merge or deploy on evidence from source or hosted CI alone.

## Implemented

1. A version/domain-separated SHA-256 digest over a canonical, bounded JSON Task payload. Reject malformed, duplicated, nonfinite, oversized and non-object payloads, as well as nested nonstring object keys. Explicit invalid canonical `payload_json` never falls back to legacy `payload`.
2. The issuing API includes `task_payload_digest`, `agent_session_id`, `execution_attempt`, and approved `projection_generation` in the signed executor JWT.
3. The strict Hermes worker compares the *claim response's source payload and claim identity* with the issuer response before storing the token, starting heartbeat, preparing a repository or invoking a model.
4. AssistX API middleware verifies the token signature, then re-reads the **current source Task** and projection state on every scoped context, heartbeat, or completion request. Changed payload, claim, session, attempt, projection, status and lease are denied; an unavailable database fails closed.
5. Synthetic exact-route negatives test mutation, replay, missing claims, invalid JSON and unavailable current-state verification.

## Important limitations

- A source DB read immediately before a downstream API action is **not an atomic fenced resource-side commit**. A concurrent claim mutation after the check can race with the downstream operation. The database's current state is also controlled by the same fleet administration domain.
- The auto-router service and external inference/tool/repository effect owners have **not** adopted this digest and do not prove current source ownership or revocation themselves. No externally anchored monotonic high-water or nonrestoreable effect receipt is provided.
- The claim issuer does not establish independently administered signer custody, real revocation authority, deployment source/image provenance, safe writable-mount restriction, or physical rollback.
- Migration of existing tasks that lack explicit `agent_session_id` / `execution_attempt` will be denied until deliberately reviewed, not silently granted.
- The current source Task and signed claim are checked under mutable current-state storage, not trusted external authority.

## Follow-on gates

Independently administered revocation authority (#426), authoritative claim census and transactionally fenced effects, auto-router verification, cross-service negative replay/crash/partition tests, hardened mounts (#414), approved model registry migration (#415), signed image custody, reviewer-approved rollback and physical validation remain **HOLD / NO-GO**.

Never promote source-only tests, synthetic JWTs or GitHub runner success into a production GO decision.
