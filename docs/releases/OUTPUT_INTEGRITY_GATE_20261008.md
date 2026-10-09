# Output integrity, October 8, 2026: prediction and observation

Before changes: 10 failures in tests/test_llm_output_integrity.py, including
two charset-less UTF-8 SSE corruption cases and eight success-credit assertions.
Prediction: both OpenAI-compatible streaming helpers pin UTF-8 only when the
server did not declare a charset. HTTP 200 with unusable completion must not
earn healthy credit, clear circuit/pair failures, or trigger routing fallthrough
or a transport-failure penalty. Caller-visible content remains unchanged.

Observation: 38/38 focused tests pass locally with fake upstream requests.
Only src/assistx/llm/client.py changes. No provider calls, model dispatch,
production authorization, hosted inference or configuration changes.
