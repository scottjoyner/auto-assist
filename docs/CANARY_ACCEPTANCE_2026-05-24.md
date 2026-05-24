# Canary Acceptance Record (May 24, 2026)

## Environment

- Host: this device (`/home/scott/git/auto-assist`)
- AssistX API: `http://localhost:8000` (`assistx-api` container)
- Neo4j: containerized (`neo4j:5.24-enterprise`)
- Hermes adapter: host systemd service (`hermes-agent-adapter.service`)

## Phase 6 Gates

Executed:

```bash
src/scripts/phase6_preflight.sh
src/scripts/phase6_callback_smoke.sh
src/scripts/phase6_canary_gate.sh
```

All passed.

Latest gate snapshot:

- `neo4j.status=ok`
- `queue.depth=0`
- `queue.failed=16` (under threshold 20)
- `sessions.stale=2` (under threshold 20)
- `dispatches.failed_or_cancelled=0`

## Live Task Canary Transaction

Created and executed a real canary task end-to-end.

- `task_id`: `5aca60c7cb5e4a39833edbafed04af18`
- `dispatch_id`: `acfb3db19b4d4470adc9d801a7ff0f50`
- `context_packet_id`: `e09e54d770054c6388becc7e1062ccbf`
- Final task status: `DONE`
- Completion agent: `hermes-local`
- Run ID: `0bc5dec25702430998dff20bcb69f729`

Task lifecycle observed:

1. `READY`
2. `RUNNING` (claimed by Hermes adapter)
3. `DONE` (completion + run persisted)

## Notes

- Dispatch creation attempted Paperclip issue creation but returned a Paperclip
  `400 Bad Request` for this specific request; local Neo4j dispatch record still
  created and task execution completed via local trigger path.
- This does not block canary acceptance for local execution, but Paperclip agent
  targeting should be tightened before relying on Paperclip as the primary
  assignment path in production.

## Follow-up Fix (May 24, 2026)

Paperclip dispatch targeting was hardened and re-verified:

- Added Paperclip agent reference resolution in `PaperclipClient`:
  - accepts UUIDs, exact names, and aliases like `hermes-local`
  - resolves to canonical Paperclip UUID before issue creation
- Fixed list-response parsing for Paperclip APIs that return raw arrays
- Improved Paperclip HTTP error diagnostics to include response body details

Validation dispatch after fix:

- `task_id`: `8d968385597d439bad6a08d956d7f425`
- `dispatch_id`: `8d6958089e5f45cda7139a8949844ba2`
- `paperclip_issue_id`: `29c710bb-9d37-4946-85d8-4ce6e8156435`
- `paperclip_error`: `null`

## Full Paperclip-Linked E2E Canary (May 24, 2026)

Executed with host Hermes adapter + containerized AssistX/Neo4j.

- `task_id`: `134d0052141e4035b537dd722a24af2e`
- `dispatch_id`: `184bbac63b924716be7a66d177ae8404`
- `paperclip_issue_id`: `32f08bb2-4944-4ae4-a2cc-001ef87e176a`
- Paperclip issue fields:
  - `assigneeAgentId`: `cfecc886-befc-4fa9-a91e-3e9a707b4a4f`
  - `priority`: `medium`
  - `status`: `blocked` (Paperclip-side state)
- AssistX task lifecycle:
  - `READY` -> `RUNNING` -> `DONE`
  - `completed_by`: `hermes-local`
  - `run_id`: `7466c8f5801141bf909d1f0eb9591743`

### Operational Cleanup

The RQ failed-job registry had accumulated timeout failures from prior
experiments (`37`). Cleared registry for queue `assistx` inside `assistx-api`
container, then re-ran canary gate:

- `queue.failed=0`
- overall canary gate: **PASS**
