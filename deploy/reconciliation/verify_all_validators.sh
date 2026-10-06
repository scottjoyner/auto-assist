#!/usr/bin/env bash

set -euo pipefail

cd /home/scott/git/reconciliation-20260730/auto-assist

echo "=== Starting Reconciliation State Verification ==="
echo

echo "1. Validating Runtime Evidence..."
python3 scripts/validate-runtime-evidence.py deploy/reconciliation/runtime-projection.yaml > /dev/null 2>&1
RUNTIME_EVIDENCE_STATUS=$?
if [ $RUNTIME_EVIDENCE_STATUS -eq 0 ]; then
    echo "   ✓ RUNTIME_EVIDENCE: PASS"
else
    echo "   ✗ RUNTIME_EVIDENCE: BLOCKED"
    python3 scripts/validate-runtime-evidence.py deploy/reconciliation/runtime-projection.yaml
fi
echo

echo "2. Validating Hermes External Config..."
python3 scripts/validate-hermes-external-config.py deploy/reconciliation/runtime-projection.yaml > /dev/null 2>&1
HERMES_CONFIG_STATUS=$?
if [ $HERMES_CONFIG_STATUS -eq 0 ]; then
    echo "   ✓ HERMES_EXTERNAL_CONFIG: PASS"
else
    echo "   ✗ HERMES_EXTERNAL_CONFIG: BLOCKED"
    python3 scripts/validate-hermes-external-config.py deploy/reconciliation/runtime-projection.yaml
fi
echo

echo "3. Validating External Dependencies..."
python3 scripts/validate-external-dependencies.py deploy/reconciliation/external-dependencies.yaml > /dev/null 2>&1
EXTERNAL_DEPS_STATUS=$?
if [ $EXTERNAL_DEPS_STATUS -eq 0 ]; then
    echo "   ✓ EXTERNAL_DEPENDENCY_GATE: PASS"
else
    echo "   ✗ EXTERNAL_DEPENDENCY_GATE: BLOCKED"
    python3 scripts/validate-external-dependencies.py deploy/reconciliation/external-dependencies.yaml
fi
echo

echo "4. Validating Final Cutover Evidence..."
python3 scripts/validate-final-cutover-evidence.py deploy/reconciliation/final-cutover-evidence.yaml > /dev/null 2>&1
FINAL_CUTOVER_STATUS=$?
if [ $FINAL_CUTOVER_STATUS -eq 0 ]; then
    echo "   ✓ FINAL_CUTOVER_EVIDENCE: PASS"
else
    echo "   ✗ FINAL_CUTOVER_EVIDENCE: BLOCKED"
    python3 scripts/validate-final-cutover-evidence.py deploy/reconciliation/final-cutover-evidence.yaml
fi
echo

echo "5. Validating Reconciliation State..."
python3 scripts/validate-reconciliation-state.py deploy/reconciliation/migration-state.yaml > /dev/null 2>&1
RECONCILIATION_STATE_STATUS=$?
if [ $RECONCILIATION_STATE_STATUS -eq 0 ]; then
    echo "   ✓ RECONCILIATION_STATE: PASS"
else
    echo "   ✗ RECONCILIATION_STATE: BLOCKED"
    python3 scripts/validate-reconciliation-state.py deploy/reconciliation/migration-state.yaml
fi
echo

echo "=== Verification Complete ==="
if [ $RUNTIME_EVIDENCE_STATUS -eq 0 ] && [ $HERMES_CONFIG_STATUS -eq 0 ] && \
   [ $EXTERNAL_DEPS_STATUS -eq 0 ] && [ $FINAL_CUTOVER_STATUS -eq 0 ] && \
   [ $RECONCILIATION_STATE_STATUS -eq 0 ]; then
    echo "✅ ALL VALIDATORS PASS - Shadow stack ready for cutover"
    exit 0
else
    echo "❌ SOME VALIDATORS BLOCKED - Cutover not ready"
    exit 1
fi
