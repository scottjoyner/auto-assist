# Operator Task-list 500 — malformed graph priority and timestamps

**Production incident:** November 7, 2026, after the identity-only API fix.
A read-only authenticated GET `/api/agent/tasks?status=READY&limit=1`
returned HTTP 500 while `/health` and Neo4j container health were healthy.
The same 500 reproduced in a separate API `TestClient` process after the new
machine-header denial middleware was deliberately disabled. The exception
was `AttributeError` at `neo4j_client.py` in `list_agent_tasks`' sorting key:
`(t.get("priority") or "UNSET").upper()` assumes a string. Graph Task
nodes can contain legacy non-string properties, including numeric priority.

## Narrow fix and validation

- Keep graph query, selection, capability, targeted-agent, Paperclip exclusion,
  and result limit entirely unchanged. Never touch graph records.
- Recognized string priorities still sort with the existing rank mapping.
  Non-string and unknown priorities sort as rank 9 (UNSET), not HIGH.
- Valid integer/finite-float timestamps preserve their numeric ordering.
  Absent, string, boolean, nonnumeric, NaN and infinity timestamps are
  normalized to 0 to prevent mixed-type comparison failures.
- Synthetic fake-session regression first reproduced four failures on the
  original deployed source. Patched code passed 5/5 new cases.
- Independent no-network, read-only, capability-dropped application Docker
  image: **40 tests passed**, covering priority normalization, existing
  identity-only API, schema contracts, Task family routing and control-room
  runtime. No real Task payloads, graph mutations or credential values.

## Production gate

The fix is operator GET availability only and does **not** change machine
identity admission, signed runtime projection, Task claim, lease or COMMIT
policy. Before rollout, preserve a SHA-256 checked `neo4j_client.py` copy
and production commit, verify a clean exact deployed-base checkout, then
restart **only** `assistx-api`. Post-restart, require `/health=200`,
operator-authenticated Task GET `=200`, machine-header Task GET `=403`,
registered xwing identity-only GET `=200/HOLD`, and the signed projection
remaining unavailable `503` until signer review. If any security or health
regression occurs, restore prior source and controlled API restart.

**No machine Task authorization granted.** Five remote agent services still
run their old source; positive signed runtime capacity is absent.
