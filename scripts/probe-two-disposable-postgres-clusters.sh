#!/usr/bin/env bash
# NO-GO research: two isolated disposable PostgreSQL clusters on ONE host.
set -euo pipefail

[[ "${ALLOW_TWO_DISPOSABLE_PG_LABS:-}" == "YES_SYNTHETIC_ONLY" ]] || {
  echo "TWO_PG_LABS: BLOCKED explicit_synthetic_opt_in_required" >&2; exit 1;
}
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image=postgres:17-alpine
resource="assistx-resource-research-${$}-${RANDOM}"
anchor="assistx-anchor-research-${$}-${RANDOM}"
created_resource=0
created_anchor=0
cas_scratch=""
cleanup() {
  if [[ -n "$cas_scratch" ]]; then rm -rf -- "$cas_scratch"; fi
  if (( created_anchor )); then docker rm -f "$anchor" >/dev/null 2>&1 || true; fi
  if (( created_resource )); then docker rm -f "$resource" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT INT TERM

fail() { echo "TWO_PG_LABS: BLOCKED $1" >&2; exit 1; }
command -v docker >/dev/null || fail "docker_absent"
docker image inspect "$image" >/dev/null || fail "cached_image_unavailable_no_pull"
for name in "$resource" "$anchor";do
 if docker ps -a --format '{{.Names}}' | grep -Fxq "$name"; then fail "name_collision"; fi
done

# All credentials are synthetic, generated for a single short-lived test.
# They must not appear in a repo, file, output or user-visible report.
random_key() { python3 -c 'import secrets;print(secrets.token_hex(24))'; }
resource_admin="$(random_key)"
anchor_admin="$(random_key)"
resource_operator="$(random_key)"
resource_observer="$(random_key)"
resource_signer="$(random_key)"
resource_verifier="$(random_key)"
anchor_writer="$(random_key)"
anchor_reader="$(random_key)"

launch() {
  local name="$1" admin_secret="$2"
  docker run --pull never --rm -d --name "$name" \
    --label research.only=two-disposable-postgres \
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
}
admin_sql() {
 docker exec "$1" psql -h /var/run/postgresql -U postgres -d postgres \
    -Atq -v ON_ERROR_STOP=1 "${@:2}"
}
user_sql() {
 local container="$1" user="$2" password="$3" statement="$4"
 docker exec --env PGPASSWORD="$password" "$container" \
   psql -h 127.0.0.1 -U "$user" -d postgres -Atq -v ON_ERROR_STOP=1 -c "$statement"
}
denied_sql() {
 local container="$1" user="$2" password="$3" sql="$4" label="$5"
 local result
 if result="$(user_sql "$container" "$user" "$password" "$sql" 2>&1)";then
   fail "${label}_unexpectedly_authorized"
 fi
 case "$result" in
  *"permission denied"*|*"must be owner"*) echo "DENIED $label";;
  *) fail "${label}_unclassified_failure";;
 esac
}
denied_password() {
 local container="$1" role="$2" credential="$3" label="$4"
 local result
 if result="$(user_sql "$container" "$role" "$credential" 'SELECT 1' 2>&1)";then
   fail "${label}_wrong_password_accepted"
 fi
 case "$result" in
  *"password authentication failed"*|*"role "*' does not exist'*) echo "DENIED $label";;
  *) fail "${label}_unclassified_auth_failure";;
 esac
}
ready() {
 local container="$1" found=0
 for _ in $(seq 1 25);do
  if docker exec "$container" pg_isready -U postgres -h /var/run/postgresql >/dev/null 2>&1;then
    found=1;break
  fi
  sleep 1
 done
 (( found )) || fail "cluster_not_ready"
 local description
 description="$(docker inspect "$container" --format '{{.HostConfig.NetworkMode}}|{{.HostConfig.ReadonlyRootfs}}|{{len .Mounts}}|{{json .NetworkSettings.Ports}}')"
 [[ "$description" == 'none|true|0|{}' ]] || fail "physical_isolation_contract"
}

echo "TWO_PG_LABS: ISOLATED_PAIR_START"
launch "$resource" "$resource_admin";created_resource=1
launch "$anchor" "$anchor_admin";created_anchor=1
ready "$resource"
ready "$anchor"
echo "PASS independent_postgres_cluster_processes_network_none_no_volumes"

# The resource uses the audited schema/permissions from draft #375. Its
# internal toy highwater table is NOT authoritative in this experiment.
docker exec -i "$resource" psql -h /var/run/postgresql -U postgres -d postgres -q \
  -v ON_ERROR_STOP=1 \
  -v operator_password="$resource_operator" \
  -v signer_password="$resource_signer" \
  -v verifier_password="$resource_verifier" \
  -v observer_password="$resource_observer" \
  -f - < "$root/scripts/disposable-postgres-role-contract.sql" >/dev/null

docker exec -i "$anchor" psql -h /var/run/postgresql -U postgres -d postgres -q \
  -v ON_ERROR_STOP=1 \
  -v writer_password="$anchor_writer" \
  -v reader_password="$anchor_reader" \
  -f - < "$root/scripts/disposable-external-highwater.sql" >/dev/null

[[ "$resource_admin" != "$anchor_admin" ]] || fail "superuser_secrets_equal"
[[ "$resource_operator" != "$anchor_writer" ]] || fail "cross_role_secrets_equal"

# Source roles have no anchor-cluster roles and cannot authenticate to the
# separate anchor with their existing passwords.
denied_password "$anchor" hw_writer "$resource_operator" "resource_operator_to_anchor_writer"
denied_password "$anchor" hw_reader "$resource_observer" "resource_observer_to_anchor_reader"
denied_password "$resource" lab_operator "$anchor_writer" "anchor_writer_to_resource_operator"
denied_password "$resource" lab_observer "$anchor_reader" "anchor_reader_to_resource_observer"
denied_password "$anchor" lab_signer "$resource_signer" "resource_signer_cross_cluster_role_absent"

denied_sql "$resource" lab_signer "$resource_signer" \
 'UPDATE lab.effect_state SET generation=99' "resource_signer_effect_write"
denied_sql "$anchor" hw_reader "$anchor_reader" \
 'UPDATE hw.highwater SET commit_seq=99' "anchor_reader_write"
denied_sql "$anchor" hw_writer "$anchor_writer" \
 'UPDATE hw.highwater SET commit_seq=99' "anchor_writer_direct_write"
denied_sql "$anchor" hw_reader "$anchor_reader" \
 "SELECT hw.advance(1,1,1,2,repeat('e',32))" "anchor_reader_procedure"

# Independent observer fetches each cluster's state under its own login.
resource_seq() { user_sql "$resource" lab_observer "$resource_observer" 'SELECT commit_seq FROM lab.effect_state'; }
anchor_seq() { user_sql "$anchor" hw_reader "$anchor_reader" 'SELECT commit_seq FROM hw.highwater'; }
[[ "$(resource_seq)" == "1" && "$(anchor_seq)" == "1" ]] || fail "initial_isolated_readback"
[[ "$(user_sql "$resource" lab_operator "$resource_operator" "SELECT lab.apply_effect(1,1,2,repeat('e',32))")" == "2" ]] || fail "resource_commit_denied"
[[ "$(resource_seq)" == "2" && "$(anchor_seq)" == "1" ]] || fail "resource_effect_not_visible"
[[ "$(user_sql "$resource" lab_observer "$resource_observer" 'SELECT count(*) FROM lab.commit_receipts WHERE commit_seq=2')" == "1" ]] || fail "receipt_not_visible"
echo "PASS resource_commit_newer_than_separately_hosted_anchor_consistency_only"

[[ "$(user_sql "$anchor" hw_writer "$anchor_writer" "SELECT hw.advance(1,1,1,2,repeat('e',32))")" == "2" ]] || fail "anchor_cas_advance_failed"
[[ "$(anchor_seq)" == "2" ]] || fail "anchor_current_not_visible"
if user_sql "$anchor" hw_writer "$anchor_writer" "SELECT hw.advance(1,1,1,2,repeat('7',32))" >/dev/null 2>&1;then fail "anchor_stale_cas_allowed";fi
if user_sql "$anchor" hw_writer "$anchor_writer" "SELECT hw.advance(1,2,1,2,repeat('7',32))" >/dev/null 2>&1;then fail "anchor_nonmonotonic_allowed";fi
echo "DENIED stale_or_nonmonotonic_anchor_cas_and_resource_replay"

# Simulate a partial *resource* snapshot restore using privileged SQL inside
# the disposable resource cluster only. The anchor in another container stays.
admin_sql "$resource" -c "UPDATE lab.effect_state SET commit_seq=1,generation=1,checksum=repeat('a',64),revision_sha256=repeat('b',64),nonce=repeat('f',32) WHERE resource_id='auto-router-runtime-projection'" >/dev/null
[[ "$(resource_seq)" == "1" && "$(anchor_seq)" == "2" ]] || fail "independent_anchor_failed_to_survive_resource_restore"
echo "DENIED resource_rollback_detectable_from_independent_anchor"

# Restore the resource under the separate cluster admin. It can only change
# its own instance, NOT change the anchor with resource account credentials.
admin_sql "$resource" -c "UPDATE lab.effect_state SET commit_seq=2,generation=2,checksum=repeat('c',64),revision_sha256=repeat('d',64),nonce=repeat('e',32) WHERE resource_id='auto-router-runtime-projection'" >/dev/null
[[ "$(resource_seq)" == "2" && "$(anchor_seq)" == "2" ]] || fail "resource_return_to_committed_state_failed"
denied_password "$anchor" postgres "$resource_admin" "resource_superuser_password_on_anchor"

# CRITICAL NO-GO: the common Docker host administrator can still execute as
# anchor's superuser, and independently restore the anchor marker.
admin_sql "$anchor" -c "UPDATE hw.highwater SET commit_seq=1,last_nonce=repeat('f',32) WHERE resource_id='auto-router-runtime-projection'" >/dev/null
[[ "$(resource_seq)" == "2" && "$(anchor_seq)" == "1" ]] || fail "common_docker_admin_restore_not_observed"
echo "NO_GO shared_host_administrator_can_restore_separate_anchor"

# Separate container DBAs are distinct, but common Docker admin can also
# fabricate a resource receipt directly in the source resource cluster.
admin_sql "$resource" -c "BEGIN; UPDATE lab.effect_state SET commit_seq=3,generation=3,nonce=repeat('7',32) WHERE resource_id='auto-router-runtime-projection'; INSERT INTO lab.commit_receipts VALUES (3,1,repeat('7',32),3,repeat('c',64),repeat('d',64)); COMMIT;" >/dev/null
[[ "$(resource_seq)" == "3" ]] || fail "direct_privileged_fabrication_not_observed"
[[ "$(user_sql "$resource" lab_observer "$resource_observer" 'SELECT count(*) FROM lab.commit_receipts WHERE commit_seq=3')" == "1" ]] || fail "fabricated_receipt_not_visible"
echo "NO_GO resource_dba_fabricates_receipt_even_with_separate_anchor"

# The restricted anchor writer can advance high-water *without* any actual
# resource COMMIT. The SQL CAS enforces order, but not source provenance.
[[ "$(user_sql "$anchor" hw_writer "$anchor_writer"  "SELECT hw.advance(1,1,1,4,repeat('8',32))")" == "4" ]]   || fail "anchor_writer_advance_without_commit_failed"
[[ "$(anchor_seq)" == "4" && "$(resource_seq)" == "3" ]]   || fail "unanchored_resource_sequence_not_observed"
echo "NO_GO anchor_writer_can_advance_beyond_uncommitted_resource"

# Two distinct, concurrent psql connections attempt the identical CAS from
# seq4 to seq5. One MUST win; the other must fail with stale CAS. Both calls
# are in the same ephemeral anchor instance; the host admin remains in charge.
cas_scratch="$(mktemp -d /tmp/convergence-pg-cas.XXXXXXXX)"   || fail "concurrent_test_scratch_unavailable"
for idx in 1 2; do
  nonce="$(if [[ "$idx" == 1 ]]; then printf '9%.0s' {1..32};else printf 'a%.0s' {1..32};fi)"
  (
    if user_sql "$anchor" hw_writer "$anchor_writer"       "BEGIN; SELECT pg_sleep(0.2); SELECT hw.advance(1,4,1,5,'$nonce'); COMMIT;"       >/dev/null 2>&1; then
      printf 'PASS\n' > "$cas_scratch/outcome.$idx"
    else
      printf 'REJECTED\n' > "$cas_scratch/outcome.$idx"
    fi
  ) &
  if [[ "$idx" == 1 ]]; then pid1=$!; else pid2=$!; fi
done
wait "$pid1"
wait "$pid2"
[[ -s "$cas_scratch/outcome.1" && -s "$cas_scratch/outcome.2" ]]   || fail "concurrent_cas_result_missing"
wins="$(grep -hxc 'PASS' "$cas_scratch"/outcome.* | awk -F: '{sum+=$NF} END {print sum+0}')"
denials="$(grep -hxc 'REJECTED' "$cas_scratch"/outcome.* | awk -F: '{sum+=$NF} END {print sum+0}')"
[[ "$wins" == "1" && "$denials" == "1" && "$(anchor_seq)" == "5" ]]   || fail "concurrent_cas_not_single_winner"
echo "PASS concurrent_anchor_cas_one_winner_one_denial"

echo "TWO_PG_LABS: PASS_ROLE_ISOLATION_WITH_SHARED_HOST_NO_GO"
