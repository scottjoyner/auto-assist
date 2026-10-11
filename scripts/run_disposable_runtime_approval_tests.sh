#!/usr/bin/env bash
# Run ONLY against a disposable, memory-bounded Neo4j 5.23 container.
# No volumes, no public port, no production DB or credentials. Always stop.
set -euo pipefail

if [[ "$(printenv ASSISTX_DISPOSABLE_NEO4J_TEST_ACK || true)" != "run-one-local-ephemeral-db" ]]; then
  echo "REJECTED: explicit disposable-run acknowledgement required" >&2
  exit 2
fi
for command_name in docker python3; do
  command -v "$command_name" >/dev/null || exit 2
done
if [[ ! -f tests/test_runtime_projection_real_neo4j.py ]] ||
   [[ ! -f scripts/approve-runtime-projection.py ]]; then
  echo "REJECTED: run from exact research-branch root" >&2
  exit 2
fi
if ! docker image inspect neo4j:5.23.0 >/dev/null 2>&1; then
  echo "REJECTED: Neo4j 5.23 local image missing; never pull implicitly" >&2
  exit 2
fi

test_id="assistx-cas-sandbox-$$"
password="TestOnlyEphemeralPwd_$$"
cleanup() {
  docker rm -f "$test_id" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run --detach --rm --name "$test_id" --restart no \
  --memory 1700m --memory-swap 1700m --cpus 1.25 --pids-limit 180 \
  --security-opt no-new-privileges \
  --publish 127.0.0.1::7687 \
  --env "NEO4J_AUTH=neo4j/$password" \
  --env NEO4J_server_http_enabled=false \
  --env NEO4J_server_https_enabled=false \
  --env NEO4J_server_memory_heap_initial__size=256m \
  --env NEO4J_server_memory_heap_max__size=512m \
  --env NEO4J_server_memory_pagecache_size=128m \
  neo4j:5.23.0 >/dev/null

port=$(docker port "$test_id" 7687/tcp | awk -F: '/127.0.0.1/ {print $NF; exit}')
if [[ ! "$port" =~ ^[0-9]{4,5}$ ]] || [[ "$port" == "7687" ]] ||
   [[ "$port" == "17687" ]]; then
  echo "REJECTED: no safe randomized loopback Bolt port" >&2
  exit 2
fi

export ASSISTX_TEST_DISPOSABLE_ACK=ephemeral-neo4j-only
export ASSISTX_TEST_NEO4J_URI="bolt://127.0.0.1:$port"
export ASSISTX_TEST_NEO4J_USER=neo4j
export ASSISTX_TEST_NEO4J_PASSWORD="$password"

ready=0
for attempt in $(seq 1 55); do
  if python3 - <<'PY' >/dev/null 2>&1
import os
from neo4j import GraphDatabase
d=GraphDatabase.driver(os.environ["ASSISTX_TEST_NEO4J_URI"], auth=(
    os.environ["ASSISTX_TEST_NEO4J_USER"],
    os.environ["ASSISTX_TEST_NEO4J_PASSWORD"],
), connection_timeout=2)
try:
    d.verify_connectivity()
finally:
    d.close()
PY
  then ready=1; break; fi
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  echo "FAIL: disposable database did not become ready; no other instance touched"
  exit 1
fi

echo "DISPOSABLE_NEO4J_RUNNING port=$port memory_limit=1700m"
python3 -m unittest discover -s tests \
  -p 'test_runtime_projection_real_neo4j.py' -v
echo "DISPOSABLE_NEO4J_TESTS_PASS cleanup=trap"
