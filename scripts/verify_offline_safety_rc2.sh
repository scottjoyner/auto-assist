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
# A clean HOME must not depend on an operator's OpenCode history.
clean_home="$(mktemp -d /tmp/assistx-free-session-ci-XXXX)"
trap 'rmdir "$clean_home" 2>/dev/null || true' EXIT
# Locally, pytest may live in HOME-scoped user-site packages; a caller can
# explicitly select a preinstalled venv. CI uses its setup-python interpreter.
HOME="$clean_home" "${FREE_SUPERVISOR_PYTHON:-$PYTHON}" -m pytest --noconftest -q \
    tests/test_free_subagent_supervisor.py
"$PYTHON" -m py_compile \
    src/assistx/contracts/schemas/repository_source_binding.py \
    src/assistx/llm/client.py \
    src/assistx/intent_orchestrator.py
# Mobile runtime catalog must use the signed projection and deny absent keys.
"$PYTHON" -m pytest --noconftest -q \
    tests/test_mobile_runtime_catalog.py \
    tests/test_runtime_projection_v2.py
"$NODE" --test tests/test_provider_burn_model.cjs tests/test_trace_investigation_ui.cjs
# Render the real inherited Jinja documents without importing FastAPI.
"$PYTHON" - <<'PYTEST'
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader(str(Path.cwd() / "templates")), autoescape=True)
workbench = env.get_template("workbench.html").render()
control = env.get_template("control_room.html").render()
assert all(s in workbench for s in ("Agent Workbench", "workbench-messages", "workbench-composer", "workbench-drawer", "/static/js/workbench.js"))
assert "/static/js/control_room.js" not in workbench
assert 'id="summary-strip"' in control and "/static/js/control_room.js" in control
print("JINJA_WORKBENCH_INHERITANCE_AND_CONTROL_ROOM_REGRESSION_PASS")
PYTEST
printf 'OFFLINE_SAFETY_RC2_FOCUSED_TESTS_PASS; NO_PRODUCTION_ACTIVATION\n'
