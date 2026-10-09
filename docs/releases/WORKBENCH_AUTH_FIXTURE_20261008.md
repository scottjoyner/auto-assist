# Workbench auth fixture, October 8, 2026

CI unit tests expect 200 for authorized workbench; observed 401 because
the test hardcoded a local password while CI explicitly sets a different
BASIC_AUTH_USER and BASIC_AUTH_PASS. Prediction: read the credentials actually
configured in test process and require 200; additionally assert wrong password
gets HTTP 401. Test-only change; NO auth middleware or runtime changes.

Local isolated Python lacks LangGraph, so this API test requires the complete
GitHub CI dependency environment. Do not claim passing without CI proof.

## Second CI observation and root-cause correction

The first draft #153 fixed the 401 by using the Basic Auth credentials
actually configured in CI, but the authenticated HTTP 200 still returned
the default Fleet Control Room markup instead of the chat-first workbench.
This failure is real and the original assertion must remain strict.

Root cause: workbench.html extends base.html, but base.html supplied no Jinja
block content/head/title/heading/scripts boundaries. The child blocks were
never inserted, so inheritance rendered the base control-room document.
We added those extension points while keeping base/control-room default
content and making workbench use only its own script.

Measured in isolated Jinja fixture:
- workbench.html renders chat messages, composer, drawer and workbench CSS/JS
- workbench.html does not include the control_room.js polling script
- base.html and control_room.html retain the default control-room page and JS
- wrong-password HTTP 401 must remain enforced by the unchanged API auth
  dependency; full FastAPI runtime acceptance still requires GitHub CI
No live endpoint, provider, or fleet execution was used in the fixture.
