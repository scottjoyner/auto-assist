# Workbench auth fixture, October 8, 2026

CI unit tests expect 200 for authorized workbench; observed 401 because
the test hardcoded a local password while CI explicitly sets a different
BASIC_AUTH_USER and BASIC_AUTH_PASS. Prediction: read the credentials actually
configured in test process and require 200; additionally assert wrong password
gets HTTP 401. Test-only change; NO auth middleware or runtime changes.

Local isolated Python lacks LangGraph, so this API test requires the complete
GitHub CI dependency environment. Do not claim passing without CI proof.
