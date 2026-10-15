#!/usr/bin/env bash
# NO-GO research: two isolated disposable PostgreSQL clusters on ONE host.
set -euo pipefail

[[ "${ALLOW_DISPOSABLE_RECEIPT_LAB:-}" == "YES_SYNTHETIC_ONLY" ]] || {
  echo "MAC_RECEIPT_LAB: BLOCKED explicit_synthetic_opt_in_required" >&2; exit 1;
}
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image=postgres:17-alpine
resource="assistx-receipt-resource-${$}-${RANDOM}"
anchor="assistx-receipt-anchor-${$}-${RANDOM}"
created_resource=0
created_anchor=0
cas_scratch=""
cleanup() {
  if [[ -n "$cas_scratch" ]]; then rm -rf -- "$cas_scratch"; fi
  if (( created_anchor )); then docker rm -f "$anchor" >/dev/null 2>&1 || true; fi
  if (( created_resource )); then docker rm -f "$resource" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT INT TERM

fail() { echo "MAC_RECEIPT_LAB: BLOCKED $1" >&2; exit 1; }
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
    --label research.only=disposable-commit-receipt \
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

echo "MAC_RECEIPT_LAB: ISOLATED_PAIR_START"
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

# Strictly synthetic shared secret for this disposable cryptographic contract.
# Deliberately NOT independently custodied or asymmetrically attestable.
mac_secret="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
docker exec -i "$resource" psql -h /var/run/postgresql -U postgres -d postgres -q \
 -v ON_ERROR_STOP=1 -v mac_secret="$mac_secret" \
 -f - < "$root/scripts/disposable-resource-mac-receipts.sql" >/dev/null
docker exec -i "$anchor" psql -h /var/run/postgresql -U postgres -d postgres -q \
 -v ON_ERROR_STOP=1 -v mac_secret="$mac_secret" \
 -f - < "$root/scripts/disposable-anchor-mac-receipts.sql" >/dev/null
unset mac_secret
echo "PASS installed_synthetic_mac_verification_contract"

resource_seq() { user_sql "$resource" lab_observer "$resource_observer" 'SELECT commit_seq FROM lab.effect_state'; }
anchor_seq() { user_sql "$anchor" hw_reader "$anchor_reader" 'SELECT commit_seq FROM hw.highwater'; }

denied_sql "$anchor" hw_writer "$anchor_writer" \
 "SELECT hw.advance(1,1,1,5,repeat('8',32))" "legacy_unverified_anchor_function"
denied_sql "$resource" lab_operator "$resource_operator" \
 "SELECT lab.apply_effect(1,1,2,repeat('e',32))" "legacy_unprotected_resource_commit"
denied_sql "$anchor" hw_writer "$anchor_writer" \
 "SELECT secret FROM hw.receipt_verifier_key" "anchor_writer_proof_key_access"
denied_sql "$resource" lab_operator "$resource_operator" \
 "SELECT secret FROM lab.receipt_mac_key" "resource_operator_proof_key_access"

# With no source COMMIT, guessing or omitting the receipt MAC must fail.
if user_sql "$anchor" hw_writer "$anchor_writer" \
 "SELECT hw.advance_verified_receipt(1,1,2,repeat('e',32),2,repeat('c',64),repeat('d',64),repeat('0',64))" >/dev/null 2>&1;then
 fail "uncommitted_resource_receipt_accepted"
fi
[[ "$(anchor_seq)" == "1" && "$(resource_seq)" == "1" ]] || fail "uncommitted_anchor_advanced"
echo "DENIED anchor_advance_without_valid_resource_receipt"

[[ "$(user_sql "$resource" lab_operator "$resource_operator" \
 "SELECT lab.commit_with_mac(1,1,2,repeat('e',32))")" == "2" ]] \
 || fail "authorized_resource_commit_failed"
[[ "$(resource_seq)" == "2" ]] || fail "resource_readback_not_committed"

receipt="$(user_sql "$resource" lab_observer "$resource_observer" \
 "SELECT term||'|'||commit_seq||'|'||generation||'|'||checksum||'|'||revision_sha256||'|'||nonce||'|'||receipt_mac FROM lab.commit_receipts WHERE commit_seq=2")"
[[ "$receipt" =~ ^1\|2\|2\|[0-9a-f]{64}\|[0-9a-f]{64}\|[0-9a-f]{32}\|[0-9a-f]{64}$ ]] || fail "unexpected_receipt_shape"
IFS='|' read -r term seq gen sum rev nonce mac <<< "$receipt"
unset receipt

# Even a valid MAC must bind exactly to the committed generation and sequence.
if user_sql "$anchor" hw_writer "$anchor_writer" \
 "SELECT hw.advance_verified_receipt(1,1,2,'$nonce',99,'$sum','$rev','$mac')" >/dev/null 2>&1;then
 fail "receipt_generation_rebinding"
fi
if user_sql "$anchor" hw_writer "$anchor_writer" \
 "SELECT hw.advance_verified_receipt(1,1,4,'$nonce',2,'$sum','$rev','$mac')" >/dev/null 2>&1;then
 fail "nonadjacent_signed_receipt_advance"
fi
[[ "$(anchor_seq)" == "1" ]] || fail "tampering_updated_anchor"
echo "DENIED wrong_effect_binding_and_nonadjacent_sequence"

[[ "$(user_sql "$anchor" hw_writer "$anchor_writer" \
 "SELECT hw.advance_verified_receipt(1,1,2,'$nonce',2,'$sum','$rev','$mac')")" == "2" ]] \
 || fail "anchor_refused_genuine_committed_receipt"
[[ "$(anchor_seq)" == "2" ]] || fail "anchor_readback_mismatch"
echo "PASS genuine_resource_receipt_verified_and_atomic_anchor_advance"

if user_sql "$anchor" hw_writer "$anchor_writer" \
 "SELECT hw.advance_verified_receipt(1,1,2,'$nonce',2,'$sum','$rev','$mac')" >/dev/null 2>&1;then
 fail "genuine_receipt_replayed_twice"
fi
echo "DENIED repeat_receipt_from_old_anchor_position"
echo "MAC_RECEIPT_LAB: PASS_RESTRICTED_CONTRACT_WITH_SHARED_CUSTODY_NO_GO"
