// OPERATOR-REVIEW REQUIRED. DO NOT execute on the production graph automatically.
// Enables atomic owner-scoped retry keys for strict-mode task creation.
// Review any existing schema/index conflicts and migration recovery plan first.
CREATE CONSTRAINT task_owner_idempotency_unique IF NOT EXISTS
FOR (t:Task) REQUIRE t.owner_idempotency_key IS UNIQUE;
