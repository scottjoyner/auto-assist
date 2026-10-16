#!/usr/bin/env bash
# Explicitly opted-in one-cluster physical negative test. NO production calls.
set -euo pipefail
[[ "${ALLOW_DISPOSABLE_PG_EFFECT_AUTHORITY:-}" == "YES_SYNTHETIC_ONLY" ]] || {
 echo 'EFFECT_AUTHORITY_LAB: BLOCKED explicit_synthetic_opt_in_required' >&2; exit 1; }
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image=postgres:17-alpine
name="assistx-fx-disposable-${$}-${RANDOM}"
running=0
cleanup() {
 if (( running )); then docker rm -f "$name" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT INT TERM
fail() { echo "EFFECT_AUTHORITY_LAB: FAIL $1" >&2; exit 1; }
command -v docker >/dev/null || fail docker_missing
docker image inspect "$image" >/dev/null || fail cached_image_missing_no_pull
key() { python3 -c 'import secrets;print(secrets.token_hex(24))'; }
admin_secret="$(key)"
executor_secret="$(key)"
observer_secret="$(key)"
reviewer_secret="$(key)"
revoker_secret="$(key)"
[[ "$executor_secret" != "$revoker_secret" ]] || fail nonunique_synthetic_auth
docker run --pull never --rm -d --name "$name" \
  --label research.only=disposable-fx-authority \
  --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 64 --memory 512m --cpus 1.5 --user 70:70 \
  --tmpfs /var/lib/postgresql/data:rw,size=256m,uid=70,gid=70,mode=0700 \
  --tmpfs /var/run/postgresql:rw,size=16m,uid=70,gid=70,mode=0770 \
  --tmpfs /tmp:rw,size=16m,uid=70,gid=70 \
  --env PGDATA=/var/lib/postgresql/data/pgdata \
  --env POSTGRES_PASSWORD="$admin_secret" \
  --env POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 \
  --env POSTGRES_HOST_AUTH_METHOD=scram-sha-256 \
  "$image" -c listen_addresses=127.0.0.1 >/dev/null
running=1
for _ in $(seq 1 35); do
 if docker exec "$name" pg_isready -U postgres -h /var/run/postgresql >/dev/null 2>&1;then break; fi
 sleep 1
done
docker exec "$name" pg_isready -U postgres -h /var/run/postgresql >/dev/null 2>&1 || fail not_ready
isolation="$(docker inspect "$name" --format '{{.HostConfig.NetworkMode}}|{{.HostConfig.ReadonlyRootfs}}|{{len .Mounts}}|{{json .NetworkSettings.Ports}}')"
[[ "$isolation" == 'none|true|0|{}' ]] || fail invalid_isolation
echo 'PASS cached_postgres17_network_none_readonly_no_mounts'

docker exec -i "$name" psql -h /var/run/postgresql -U postgres -d postgres -q \
 -v ON_ERROR_STOP=1 -v executor_password="$executor_secret" \
 -v observer_password="$observer_secret" -v reviewer_password="$reviewer_secret" \
 -v revoker_password="$revoker_secret" -f - \
 < "$repo/scripts/disposable-pg-effect-time-authority.sql" >/dev/null
user_sql() {
 local role="$1" secret="$2" stmt="$3"
 docker exec --env PGPASSWORD="$secret" "$name" \
  psql -h 127.0.0.1 -U "$role" -d postgres -Atq -v ON_ERROR_STOP=1 -c "$stmt"
}
admin_sql() { docker exec "$name" psql -h /var/run/postgresql -U postgres -d postgres -Atq -v ON_ERROR_STOP=1 -c "$1"; }
denied() {
 local role="$1" secret="$2" stmt="$3" label="$4" output
 if output="$(user_sql "$role" "$secret" "$stmt" 2>&1)";then
   fail "${label}_unexpectedly_authorized"
 fi
 case "$output" in
  *'permission denied'*|*'stale_or_revoked_at_effect_time'*|*'duplicate key value'*|*'invalid_authority_advance'*|*'stale_authority_advance'*) echo "DENIED $label";;
  *) fail "${label}_unclassified_denial";;
 esac
}
denied fx_executor "$executor_secret" "INSERT INTO fx.applied VALUES(99,4,9,'owner-a',repeat('f',32),repeat('0',64))" executor_direct_forge
denied fx_executor "$executor_secret" "UPDATE fx.authority SET revoked=false,term=1" executor_direct_revocation_rewind
denied fx_revoker "$revoker_secret" "INSERT INTO fx.applied VALUES(99,4,9,'owner-a',repeat('f',32),repeat('0',64))" revoker_direct_forge
denied fx_observer "$observer_secret" "SELECT fx.execute_effect(4,9,'owner-a',77,repeat('f',32),repeat('0',64))" observer_execute
denied fx_reviewer "$reviewer_secret" "SELECT fx.rotate(4,9,5,10,'owner-b',true)" reviewer_revoke
denied fx_executor "$executor_secret" "SELECT fx.rotate(4,9,5,10,'owner-b',true)" executor_revoke
denied fx_revoker "$revoker_secret" "SELECT fx.execute_effect(4,9,'owner-a',77,repeat('f',32),repeat('0',64))" revoker_execute
[[ "$(user_sql fx_executor "$executor_secret" "SELECT fx.execute_effect(4,9,'owner-a',12,repeat('e',32),repeat('b',64))")" == '12' ]] || fail authorized_effect_rejected
[[ "$(user_sql fx_observer "$observer_secret" 'SELECT count(*) FROM fx.applied')" == '1' ]] || fail independent_restricted_readback
denied fx_executor "$executor_secret" "SELECT fx.execute_effect(4,9,'owner-a',12,repeat('e',32),repeat('b',64))" duplicate_execution
[[ "$(user_sql fx_revoker "$revoker_secret" "SELECT fx.rotate(4,9,5,10,'owner-b',true)")" == '10' ]] || fail authority_rotation
denied fx_executor "$executor_secret" "SELECT fx.execute_effect(4,9,'owner-a',13,repeat('a',32),repeat('b',64))" stale_owner_term
denied fx_executor "$executor_secret" "SELECT fx.execute_effect(5,10,'owner-b',13,repeat('a',32),repeat('b',64))" revoked_owner
denied fx_revoker "$revoker_secret" "SELECT fx.rotate(5,10,4,9,'owner-a',false)" nonmonotonic_revoke
echo 'PASS restricted_authority_term_revocation_uniqueness'
# A 2nd valid rotation to non-revoked owner is required before another effect.
[[ "$(user_sql fx_revoker "$revoker_secret" "SELECT fx.rotate(5,10,6,11,'owner-b',false)")" == '11' ]] || fail unrevocation_monotonic
[[ "$(user_sql fx_executor "$executor_secret" "SELECT fx.execute_effect(6,11,'owner-b',13,repeat('a',32),repeat('b',64))")" == '13' ]] || fail new_owner_cannot_execute
[[ "$(user_sql fx_observer "$observer_secret" 'SELECT count(*) FROM fx.applied')" == '2' ]] || fail valid_new_owner_effect_missing
echo 'PASS restricted_new_owner_can_apply_after_monotonic_regrant'

# CRITICAL NO-GO: the server/database superuser can fabricate effects
# WITHOUT fx.execute_effect; its row appears authentic to SQL observers.
admin_sql "INSERT INTO fx.applied VALUES(77,6,11,'owner-b',repeat('7',32),repeat('d',64))" >/dev/null
[[ "$(user_sql fx_observer "$observer_secret" 'SELECT count(*) FROM fx.applied')" == '3' ]] || fail superuser_forgery_not_visible
echo 'NO_GO database_superuser_forges_applied_row_bypassing_executor'
# CRITICAL NO-GO: bypass monotonic rotation by superuser table UPDATE.
admin_sql "UPDATE fx.authority SET term=4,epoch=9,owner_id='owner-a',revoked=false" >/dev/null
[[ "$(user_sql fx_executor "$executor_secret" "SELECT fx.execute_effect(4,9,'owner-a',14,repeat('8',32),repeat('b',64))")" == '14' ]] || fail superuser_rewind_not_reproduced
echo 'NO_GO database_superuser_rewinds_authority_and_reopens_old_receipt'

# CRITICAL NO-GO: executor can submit arbitrary matched term/owner and SHA
# data. The function enforces DB row structure and ACL, not a signed
# resource-owned COMMIT from an independent effect authority.
[[ "$(user_sql fx_executor "$executor_secret" "SELECT fx.execute_effect(4,9,'owner-a',15,repeat('9',32),repeat('f',64))")" == '15' ]] || fail executor_arbitrary_claim_not_reproduced
echo 'NO_GO executor_can_claim_arbitrary_digest_without_resource_attestation'

# Two actual authenticated executor connections race to INSERT the same
# synthetic sequence with different nonces. No shared shell SQL connection.
race_scratch="$(mktemp -d /tmp/disposable-fx-cas.XXXXXXXX)"
race_cleanup() { rm -rf -- "$race_scratch"; }
for i in 1 2;do
  if [[ "$i" == 1 ]];then nonce="$(printf 'a%.0s' {1..32})"
  else nonce="$(printf 'b%.0s' {1..32})";fi
  (
    if user_sql fx_executor "$executor_secret" \
       "SELECT fx.execute_effect(4,9,'owner-a',16,'$nonce',repeat('d',64))" \
       >/dev/null 2>&1;then echo WON > "$race_scratch/$i"
    else echo DENIED > "$race_scratch/$i";fi
  ) &
  if [[ "$i" == 1 ]];then one=$!;else two=$!;fi
done
wait "$one";wait "$two"
[[ "$(grep -hxc WON "$race_scratch"/* | awk -F: '{n+=$NF}END{print n+0}')" == 1 ]] || fail parallel_effect_multiple_winners
[[ "$(grep -hxc DENIED "$race_scratch"/* | awk -F: '{n+=$NF}END{print n+0}')" == 1 ]] || fail parallel_effect_not_denied
[[ "$(user_sql fx_observer "$observer_secret" 'SELECT count(*) FROM fx.applied WHERE commit_seq=16')" == 1 ]] || fail parallel_effect_row_count
race_cleanup
echo 'PASS real_concurrent_executor_connections_one_effect_winner'

echo 'EFFECT_AUTHORITY_LAB: PASS_RESTRICTED_ROLES_WITH_SUPERUSER_AND_PROVENANCE_NO_GO'
