# Runtime admission test evidence
Date: 20260730T202005Z
Runtime: xwing (lmstudio, qwen3.5-0.8b-claude-4.6-opus-reasoning-distilled)
Models: reasoning-distilled (content=empty on simple prompts, visible content on substantive queries)

## Direct completion
- finish=stop, content="Paris" (to "What is the capital of France?")
- 177 reasoning tokens + 4 visible tokens

## Routed completion (shadow router:18088)
- finish=stop, content="Paris"
- 140 reasoning tokens + 4 visible tokens

## Sequential stability
- 2 consecutive requests, both succeeded

## Concurrency (2 simultaneous, 1-slot runtime)
- Both requests completed successfully
- Router serialized correctly

## Cancellation safety
- Stream request cancelled mid-generation
- Runtime still alive after cancellation
- Router healthy (ok=True)

## Restart/rebuild
- Shadow stack stopped, rebuilt, all containers healthy
- No auto model loading
- Production stack unchanged (ok=True)

## Rollback rehearsal
- Shadow stack fully torn down and recreated
- Production unaffected
