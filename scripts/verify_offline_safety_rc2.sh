#!/usr/bin/env bash
# Only offline fixture acceptance; no production provider, graph or task calls.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${PYTHON:=python3}"
: "${NODE:=node}"
"$PYTHON" -m pytest --noconftest -q \
    tests/test_repository_source_binding.py \
    tests/test_repository_source_binding_wire_compat.py \
    tests/test_llm_output_integrity.py \
    tests/test_system_one_receipt.py \
    tests/test_mercury_shadow_specialists.py \
    tests/test_mercury_mock_authority_acceptance.py \
    tests/test_mercury_deny_only_mock.py
"$PYTHON" -m py_compile \
    src/assistx/contracts/schemas/repository_source_binding.py \
    src/assistx/llm/client.py \
    src/assistx/intent_orchestrator.py
"$NODE" --test tests/test_provider_burn_model.cjs tests/test_trace_investigation_ui.cjs
printf 'OFFLINE_SAFETY_RC2_FOCUSED_TESTS_PASS; NO_PRODUCTION_ACTIVATION\n'
