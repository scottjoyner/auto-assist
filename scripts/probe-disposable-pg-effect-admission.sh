#!/usr/bin/env bash
# One cached, disposable PG17. No remote network, permanent volumes or prod.
set -euo pipefail
[[ "${ALLOW_DISPOSABLE_PG_EFFECT_ADMISSION:-}" == "YES_SYNTHETIC_ONLY" ]] || {
  echo 'EFFECT_ADMISSION_LAB: BLOCKED explicit_synthetic_opt_in_required' >&2
  exit 1
}
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image=postgres:17-alpine
name="assistx-admission-disposable-${$}-${RANDOM}"
running=0
scratch=""
cleanup() {
  if [[ -n "$scratch" ]];then rm -rf -- "$scratch";fi
  if (( running ));then docker rm -f "$name" >/dev/null 2>&1 || true;fi
}
trap cleanup EXIT INT TERM
fail() { echo "EFFECT_ADMISSION_LAB: FAIL $1" >&2; exit 1; }
command -v docker >/dev/null || fail docker_unavailable
docker image inspect "$image" >/dev/null || fail image_not_cached_no_pull
key() { python3 -c 'import secrets; print(secrets.token_hex(24))'; }
admin_pw="$(key)"
executor_pw="$(key)"
issuer_pw="$(key)"
observer_pw="$(key)"
revoker_pw="$(key)"
reviewer_pw="$(key)"
[[ "$issuer_pw" != "$executor_pw" && "$admin_pw" != "$issuer_pw" ]] || fail credentials_not_unique
docker run --pull never --rm -d --name "$name" \
 --label research.only=disposable-pg-effect-admission \
 --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
 --pids-limit 64 --memory 512m --cpus 1.5 --user 70:70 \
 --tmpfs /var/lib/postgresql/data:rw,size=256m,uid=70,gid=70,mode=0700 \
 --tmpfs /var/run/postgresql:rw,size=16m,uid=70,gid=70,mode=0770 \
 --tmpfs /tmp:rw,size=16m,uid=70,gid=70 \
 --env PGDATA=/var/lib/postgresql/data/pgdata \
 --env POSTGRES_PASSWORD="$admin_pw" \
 --env POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 \
 --env POSTGRES_HOST_AUTH_METHOD=scram-sha-256 \
 "$image" -c listen_addresses=127.0.0.1 >/dev/null
running=1
ready=0
for _ in $(seq 1 30);do
 if docker exec "$name" pg_isready -U postgres -h /var/run/postgresql >/dev/null 2>&1;then ready=1;break;fi
 sleep 1
done
[[ "$ready" == 1 ]] || fail postgres_not_ready
settings="$(docker inspect "$name" --format '{{.HostConfig.NetworkMode}}|{{.HostConfig.ReadonlyRootfs}}|{{len .Mounts}}|{{json .NetworkSettings.Ports}}')"
[[ "$settings" == 'none|true|0|{}' ]] || fail isolation_not_proven
echo 'PASS disposable_postgres17_network_none_readonly_no_mounts'
admin_file() {
 docker exec -i "$name" psql -h /var/run/postgresql -U postgres -d postgres -Atq -v ON_ERROR_STOP=1 \
  "${@:2}" -f - < "$1" >/dev/null
}
admin_file "$root/scripts/disposable-pg-effect-time-authority.sql" \
  -v executor_password="$executor_pw" -v observer_password="$observer_pw" \
  -v reviewer_password="$reviewer_pw" -v revoker_password="$revoker_pw"
admin_file "$root/scripts/disposable-pg-effect-admission.sql" -v issuer_password="$issuer_pw"
user_sql() {
 local role="$1" pw="$2" statement="$3"
 docker exec --env PGPASSWORD="$pw" "$name" \
  psql -h 127.0.0.1 -U "$role" -d postgres -Atq -v ON_ERROR_STOP=1 -c "$statement"
}
admin_sql() {
 docker exec "$name" psql -h /var/run/postgresql -U postgres -d postgres -Atq -v ON_ERROR_STOP=1 -c "$1"
}
deny() {
 local role="$1" pw="$2" statement="$3" label="$4" output
 if output="$(user_sql "$role" "$pw" "$statement" 2>&1)";then
   fail "${label}_unexpectedly_authorized"
 fi
 case "$output" in
  *'permission denied'*|*'issuer_receipt_missing_or_spent'*|*'executor_stale_or_revoked'*|*'issuer_stale_or_revoked'*|*'duplicate key value'*) echo "DENIED $label";;
  *) fail "${label}_unexpected_failure";;
 esac
}
claim="4,9,'owner-a',12,repeat('e',32),repeat('b',64)"
deny fx_executor "$executor_pw" "SELECT fx.execute_effect($claim)" legacy_arbitrary_digest_path_revoked
deny fx_executor "$executor_pw" "SELECT fx.issue_claim($claim)" executor_cannot_issue
deny fx_issuer "$issuer_pw" "SELECT fx.execute_issued($claim)" issuer_cannot_execute
deny fx_issuer "$issuer_pw" "INSERT INTO fx.authorizations VALUES(99,4,9,'owner-a',repeat('9',32),repeat('b',64),false)" issuer_no_direct_authorization_table_write
deny fx_executor "$executor_pw" "UPDATE fx.authorizations SET spent=false" executor_no_direct_unspend
deny fx_revoker "$revoker_pw" "SELECT fx.issue_claim($claim)" revoker_cannot_issue
deny fx_observer "$observer_pw" "SELECT fx.issue_claim($claim)" observer_cannot_issue
deny fx_executor "$executor_pw" "SELECT fx.execute_issued($claim)" missing_issuer_receipt
[[ "$(user_sql fx_issuer "$issuer_pw" "SELECT fx.issue_claim($claim)")" == 12 ]] || fail issuer_admission_failed
deny fx_issuer "$issuer_pw" "SELECT fx.issue_claim($claim)" duplicate_admission
deny fx_executor "$executor_pw" "SELECT fx.execute_issued(4,9,'owner-a',12,repeat('e',32),repeat('c',64))" substituted_digest
deny fx_executor "$executor_pw" "SELECT fx.execute_issued(4,9,'owner-a',12,repeat('f',32),repeat('b',64))" substituted_nonce
[[ "$(user_sql fx_observer "$observer_pw" 'SELECT spent FROM fx.authorizations WHERE commit_seq=12')" == f ]] || fail denied_calls_consumed_receipt
[[ "$(user_sql fx_executor "$executor_pw" "SELECT fx.execute_issued($claim)")" == 12 ]] || fail authorized_effect_denied
[[ "$(user_sql fx_observer "$observer_pw" 'SELECT count(*) FROM fx.applied WHERE commit_seq=12')" == 1 ]] || fail effect_not_visible
[[ "$(user_sql fx_observer "$observer_pw" 'SELECT spent FROM fx.authorizations WHERE commit_seq=12')" == t ]] || fail receipt_not_consumed
deny fx_executor "$executor_pw" "SELECT fx.execute_issued($claim)" already_consumed_receipt
echo 'PASS restricted_issuer_executor_separation_and_atomic_consume'
# Revocation between issuance and effect should deny; the auth row stays unused.
pending="4,9,'owner-a',13,repeat('a',32),repeat('c',64)"
[[ "$(user_sql fx_issuer "$issuer_pw" "SELECT fx.issue_claim($pending)")" == 13 ]] || fail pending_issuer
[[ "$(user_sql fx_revoker "$revoker_pw" "SELECT fx.rotate(4,9,5,10,'owner-b',true)")" == 10 ]] || fail revocation_failed
deny fx_executor "$executor_pw" "SELECT fx.execute_issued($pending)" revocation_after_issuance
[[ "$(user_sql fx_observer "$observer_pw" 'SELECT spent FROM fx.authorizations WHERE commit_seq=13')" == f ]] || fail stale_consumption
[[ "$(user_sql fx_revoker "$revoker_pw" "SELECT fx.rotate(5,10,6,11,'owner-b',false)")" == 11 ]] || fail new_owner_regrant
# The issued row is now stale, even though its hash matches.
deny fx_executor "$executor_pw" "SELECT fx.execute_issued($pending)" wrong_term_after_regrant
echo 'PASS source_revocation_and_epoch_fencing_at_effect_time'
# Real simultaneous executor connections consume the same issuer row.
race="6,11,'owner-b',14,repeat('d',32),repeat('f',64)"
[[ "$(user_sql fx_issuer "$issuer_pw" "SELECT fx.issue_claim($race)")" == 14 ]] || fail race_claim_issuance
scratch="$(mktemp -d /tmp/disposable-admission-race.XXXXXXXX)"
for i in 1 2;do
 (
  if user_sql fx_executor "$executor_pw" "SELECT fx.execute_issued($race)" >/dev/null 2>&1;then
    echo WON > "$scratch/out.$i"
  else
    echo DENIED > "$scratch/out.$i"
  fi
 ) &
 if [[ "$i" == 1 ]];then first=$!;else second=$!;fi
done
wait "$first";wait "$second"
[[ "$(grep -hxc WON "$scratch"/out.* | awk -F: '{s+=$NF}END{print s+0}')" == 1 ]] || fail race_multiple_winners
[[ "$(grep -hxc DENIED "$scratch"/out.* | awk -F: '{s+=$NF}END{print s+0}')" == 1 ]] || fail race_no_loser
[[ "$(user_sql fx_observer "$observer_pw" 'SELECT count(*) FROM fx.applied WHERE commit_seq=14')" == 1 ]] || fail race_effect_count
echo 'PASS actual_concurrent_executor_connections_single_consumption'
# CRITICAL: an issuer can authorize a fictitious digest not bound to a
# resource-side COMMIT, and executor can consume it. The issuer is a
# separately restricted SQL login but not a protected real resource owner.
fiction="6,11,'owner-b',15,repeat('9',32),repeat('0',64)"
[[ "$(user_sql fx_issuer "$issuer_pw" "SELECT fx.issue_claim($fiction)")" == 15 ]] || fail fictitious_issuer_denied
[[ "$(user_sql fx_executor "$executor_pw" "SELECT fx.execute_issued($fiction)")" == 15 ]] || fail fictitious_issuer_not_consumed
echo 'NO_GO issuer_can_authorize_fictional_digest_without_real_resource_commit'
# CRITICAL: superuser bypasses both issuance and effect-time procedures.
admin_sql "INSERT INTO fx.applied VALUES(77,6,11,'owner-b',repeat('7',32),repeat('a',64))" >/dev/null
[[ "$(user_sql fx_observer "$observer_pw" 'SELECT count(*) FROM fx.applied WHERE commit_seq=77')" == 1 ]] || fail privileged_effect_not_observed
echo 'NO_GO superuser_can_fabricate_effect_without_issuer'
# CRITICAL: database superuser rewinds revocation and can use old-era
# permissions again; distinct role passwords do not prevent this.
admin_sql "UPDATE fx.authority SET term=4,epoch=9,owner_id='owner-a',revoked=false" >/dev/null
[[ "$(user_sql fx_issuer "$issuer_pw" "SELECT fx.issue_claim(4,9,'owner-a',16,repeat('8',32),repeat('b',64))")" == 16 ]] || fail privileged_rewind_not_reproduced
[[ "$(user_sql fx_executor "$executor_pw" "SELECT fx.execute_issued(4,9,'owner-a',16,repeat('8',32),repeat('b',64))")" == 16 ]] || fail privileged_rewind_exec_not_reproduced
echo 'NO_GO superuser_rewinds_owner_term_and_reopens_stale_epoch'
echo 'EFFECT_ADMISSION_LAB: PASS_RESTRICTED_ISSUER_WITH_COMMON_ADMIN_NO_GO'
