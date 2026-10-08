#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pytest -q tests/test_mercury_shadow_specialists.py tests/test_mercury_mock_authority_acceptance.py tests/test_mercury_deny_only_mock.py
python3 -m py_compile scripts/mercury_shadow_specialists.py scripts/mercury_deny_only_mock.py
node --check static/js/provider_burn_model.js
node --check static/js/traces.js
node --test tests/test_provider_burn_model.cjs tests/test_trace_investigation_ui.cjs
printf 'OFFLINE_RC_FOCUSED_SMOKE_PASS; NOT PRODUCTION_AUTHORITY\n'
