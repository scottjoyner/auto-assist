# Experiment prospectus — bounded event-type timeline exploration
Date: October 9, 2026 EDT. Frozen before implementing the new trace UI changes.

## Hypothesis
The existing reconciled AssistX trace workbench renders all events returned by GET /api/traces/{correlation_id} into its timeline, even for long histories. The UI can remain an accurate read-only investigator while initially rendering only the latest 80 event summaries, allowing keyboard-accessible progression into earlier events and searching **event types** within the already-loaded trace.

## Predictions
1. A synthetic detail with at least 1,000 events creates **at most 80** timeline event cards on initial display, while labeling the total returned by the API (not claiming all historical records are retained). The latest records are visible first in initial viewport, preserving chronological order among shown events.
2. "Show 80 earlier events" increases visible summaries by at most 80 per click and cannot accidentally fetch backend detail, copy raw payloads, mutate graph, or change task/agent admission.
3. A search input scoped to the selected trace matches only exact event_type text (case-insensitive substring) and never event.payload_json. The count and empty states must label **loaded trace only**; global outcome index stays unchanged.
4. Existing context chip selection and opt-in evidence inspector remain separately functional. Changing selected traces resets local timeline filter and expansion; stale async details remain fenced.
5. UI never includes event payload JSON in the DOM until user explicitly opens the event disclosure; closing clears it. Type search must not materialize or index private payloads.
6. New tests cover 1,000-event rendering boundedness, incremental earlier reveal, type filter+context intersection, direct-link interaction, focus, empty states, bad input, and opt-in evidence unchanged. Actual Chromium synthetic 375/768/1440 layouts remain no-overflow and no unauthorized requests.
7. No production service modification or automatic deployment. This is stacked on draft PR #140 and all its unresolved auth/attestation/custody gates.

## Predicted limitations
The current detail endpoint still **fetches all events** from Neo4j and includes payload_json in the response. Bounded DOM rendering alone does **not** solve network, database, privacy transmission or nontrimming archive custody. A separate server-side pagination/read budget would need a separate data-contract and authenticated review before release.

## Scope and safety
New isolated git worktree, frontend-only JS/CSS plus synthetic tests and documentation. No real private trace inputs, NAS writes, remote shell dispatch, provider calls, production Neo4j queries, changes to auth, or client-side unverified host/agent identity inference.
