#!/usr/bin/env bash
# Research-only: disposable, network-isolated PostgreSQL role/COMMIT falsification.
# Refuses image pulls and any existing container; never uses host volumes/ports.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="postgres:17-alpine"
name="assistx-convergence-acl-${$}-${RANDOM}"
container_created=0
cleanup() {
  if (( container_created )); then
    docker rm -f "$name" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

fail() { echo "POSTGRES_DISPOSABLE: BLOCKED $1" >&2; exit 1; }
# An explicit one-shot research opt-in is required even for this isolated lab.
[[ "${ALLOW_DISPOSABLE_PG_LAB:-}" == "YES_SYNTHETIC_ONLY" ]] || fail "explicit_synthetic_opt_in_required"
command -v docker >/dev/null || fail "docker_unavailable"
docker image inspect "$image" >/dev/null || fail "preloaded_image_missing_no_pull"
docker ps -a --format '{{.Names}}' | grep -Fxq "$name" && fail "container_name_collision"
echo "POSTGRES_DISPOSABLE: ISOLATED_START"

# Passwords are randomly generated for this disposable container only; never
# stored, printed, committed or reused for a real system.
local_admin_pw="$(python3 -c 'import secrets;print(secrets.token_hex(18))')"
operator_pw="$(python3 -c 'import secrets;print(secrets.token_hex(18))')"
signer_pw="$(python3 -c 'import secrets;print(secrets.token_hex(18))')"
verifier_pw="$(python3 -c 'import secrets;print(secrets.token_hex(18))')"
observer_pw="$(python3 -c 'import secrets;print(secrets.token_hex(18))')"

docker run --pull never --rm -d --name "$name" \
  --label research.only=disposable-postgres-role-contract \
  --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 64 --memory 512m --cpus 1.5 \
  --user 70:70 \
  --tmpfs /var/lib/postgresql/data:rw,size=256m,uid=70,gid=70,mode=0700 \
  --tmpfs /var/run/postgresql:rw,size=16m,uid=70,gid=70,mode=0770 \
  --tmpfs /tmp:rw,size=16m,uid=70,gid=70 \
  --env PGDATA=/var/lib/postgresql/data/pgdata \
  --env POSTGRES_PASSWORD="$local_admin_pw" \
  --env POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 \
  --env POSTGRES_HOST_AUTH_METHOD=scram-sha-256 \
  "$image" -c listen_addresses=127.0.0.1 >/dev/null
container_created=1

ready=0
for _ in $(seq 1 25); do
  if docker exec "$name" pg_isready -U postgres -h /var/run/postgresql >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
(( ready )) || fail "postgres_failed_to_start"

# No external mounts, ports or networks may be introduced by this run.
actual="$(docker inspect "$name" --format '{{.HostConfig.NetworkMode}}|{{.HostConfig.ReadonlyRootfs}}|{{len .Mounts}}|{{json .NetworkSettings.Ports}}')"
[[ "$actual" == 'none|true|0|{}' ]] || fail "container_isolation_contract"

admin_sql() {
  docker exec "$name" psql -h /var/run/postgresql -U postgres -d postgres \
    -Atq -v ON_ERROR_STOP=1 "$@"
}
as_role() {
  local role="$1" password="$2" sql="$3"
  docker exec --env PGPASSWORD="$password" "$name" psql \
    -h 127.0.0.1 -U "$role" -d postgres -Atq -v ON_ERROR_STOP=1 -c "$sql"
}
deny() {
  local role="$1" password="$2" sql="$3" evidence="$4"
  local observed
  if observed="$(as_role "$role" "$password" "$sql" 2>&1)"; then
    fail "${evidence}_unexpectedly_authorized"
  fi
  case "$observed" in
    *"permission denied"*|*"must be owner"*) echo "DENIED $evidence";;
    *) fail "${evidence}_unclassified_rejection";;
  esac
}
auth_denied() {
  local observed
  if observed="$(as_role lab_signer "deliberately-not-the-password" 'SELECT 1' 2>&1)"; then
    fail "wrong_password_accepted"
  fi
  case "$observed" in
    *"password authentication failed"*) echo "DENIED wrong_password";;
    *) fail "wrong_password_result_uncertain";;
  esac
}

docker exec -i "$name" psql -h /var/run/postgresql -U postgres -d postgres \
  -q -v ON_ERROR_STOP=1 \
  -v operator_password="$operator_pw" \
  -v signer_password="$signer_pw" \
  -v verifier_password="$verifier_pw" \
  -v observer_password="$observer_pw" \
  -f - < "$repo_root/scripts/disposable-postgres-role-contract.sql" >/dev/null

auth_denied
[[ "$(as_role lab_signer "$signer_pw" "SELECT current_user")" == "lab_signer" ]] || fail "signer_not_authenticated"
[[ "$(as_role lab_operator "$operator_pw" "SELECT current_user")" == "lab_operator" ]] || fail "operator_not_authenticated"
echo "PASS scram_roles_authenticate_inside_networkless_container"

deny lab_signer "$signer_pw" "UPDATE lab.effect_state SET generation=9" "signer_effect_update"
deny lab_signer "$signer_pw" "INSERT INTO lab.commit_receipts VALUES (9,1,repeat('f',32),9,repeat('a',64),repeat('b',64))" "signer_receipt_insert"
deny lab_verifier "$verifier_pw" "UPDATE lab.effect_state SET generation=9" "verifier_effect_update"
deny lab_operator "$operator_pw" "UPDATE lab.effect_state SET generation=9" "operator_direct_update"
deny lab_operator "$operator_pw" "DELETE FROM lab.commit_receipts" "operator_direct_delete"
deny lab_signer "$signer_pw" "SELECT lab.apply_effect(1,1,2,repeat('e',32))" "signer_exec_procedure"
deny lab_verifier "$verifier_pw" "SELECT lab.apply_effect(1,1,2,repeat('e',32))" "verifier_exec_procedure"
deny lab_observer "$observer_pw" "UPDATE lab.highwater SET commit_seq=99" "observer_anchor_update"
deny lab_operator "$operator_pw" "UPDATE lab.highwater SET commit_seq=99" "operator_anchor_update"
deny lab_signer "$signer_pw" "UPDATE lab.highwater SET commit_seq=99" "signer_anchor_update"

[[ "$(as_role lab_observer "$observer_pw" "SELECT commit_seq FROM lab.effect_state")" == "1" ]] || fail "observer_cannot_read"
[[ "$(as_role lab_operator "$operator_pw" "SELECT lab.apply_effect(1,1,2,repeat('e',32))")" == "2" ]] || fail "authorized_commit_failed"
[[ "$(as_role lab_observer "$observer_pw" "SELECT commit_seq FROM lab.effect_state")" == "2" ]] || fail "fresh_readback_failed"
[[ "$(as_role lab_observer "$observer_pw" "SELECT count(*) FROM lab.commit_receipts WHERE commit_seq=2")" == "1" ]] || fail "receipt_not_committed"
echo "PASS bounded_operator_commit_and_independent_db_connection_readback"

# Negative authorized-caller CAS: a stale term/sequence must not reapply.
if as_role lab_operator "$operator_pw" "SELECT lab.apply_effect(1,1,3,repeat('9',32))" >/dev/null 2>&1; then
  fail "stale_operator_second_commit"
fi
echo "DENIED stale_operator_sequence"

# Same externally visible ACK uncertainty, different durable states.
# Each command intentionally discards its PostgreSQL output; independent
# observer readback is the only basis for the next decision.
as_role lab_operator "$operator_pw" \
 "BEGIN; SELECT lab.apply_effect(1,2,3,repeat('7',32)); ROLLBACK;" >/dev/null
echo "UNCERTAIN ACK_NOT_OBSERVED"
[[ "$(as_role lab_observer "$observer_pw" "SELECT commit_seq FROM lab.effect_state")" == "2" ]] || fail "rolled_back_transaction_persisted"
echo "PASS uncertainty_precommit_readback_uncommitted"

as_role lab_operator "$operator_pw" \
 "BEGIN; SELECT lab.apply_effect(1,2,3,repeat('7',32)); COMMIT;" >/dev/null
echo "UNCERTAIN ACK_NOT_OBSERVED"
[[ "$(as_role lab_observer "$observer_pw" "SELECT commit_seq FROM lab.effect_state")" == "3" ]] || fail "durable_transaction_not_observed"
echo "PASS uncertainty_postcommit_readback_committed"

# A signer/verifier cannot move the anchor, but the same postgres superuser
# who controls the resource can also rewrite the anchor. This is a NO-GO.
admin_sql -c "UPDATE lab.highwater SET commit_seq=3, last_nonce=repeat('7',32) WHERE resource_id='auto-router-runtime-projection'" >/dev/null
[[ "$(as_role lab_observer "$observer_pw" "SELECT commit_seq FROM lab.highwater")" == "3" ]] || fail "highwater_not_observed"
admin_sql -c "UPDATE lab.highwater SET commit_seq=1,last_nonce=repeat('f',32) WHERE resource_id='auto-router-runtime-projection'" >/dev/null
[[ "$(as_role lab_observer "$observer_pw" "SELECT commit_seq FROM lab.highwater")" == "1" ]] || fail "restore_not_reproduced"
echo "NO_GO shared_superuser_can_restore_highwater"

# A database superuser can forge *both* the resource effect and receipt and
# observer role will see a seemingly authentic transaction. NO-GO.
admin_sql -c "BEGIN; UPDATE lab.effect_state SET commit_seq=4,generation=4,nonce=repeat('6',32) WHERE resource_id='auto-router-runtime-projection'; INSERT INTO lab.commit_receipts VALUES (4,1,repeat('6',32),4,repeat('c',64),repeat('d',64)); COMMIT;" >/dev/null
[[ "$(as_role lab_observer "$observer_pw" "SELECT commit_seq FROM lab.effect_state")" == "4" ]] || fail "privileged_forgery_not_observed"
[[ "$(as_role lab_observer "$observer_pw" "SELECT count(*) FROM lab.commit_receipts WHERE commit_seq=4")" == "1" ]] || fail "privileged_receipt_not_observed"
echo "NO_GO shared_superuser_can_fabricate_commit_receipt"

echo "POSTGRES_DISPOSABLE: PASS_ROLE_DENIAL_AND_COUNTEREXAMPLES_NO_GO"
