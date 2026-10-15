-- DO NOT apply to a real DB. Install ONLY atop disposable-external-highwater.sql.
\set ON_ERROR_STOP on
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
REVOKE EXECUTE ON FUNCTION hw.advance(bigint,bigint,bigint,bigint,text) FROM hw_writer;
SET ROLE hw_owner;
CREATE TABLE hw.receipt_verifier_key (id integer PRIMARY KEY, secret bytea NOT NULL);
INSERT INTO hw.receipt_verifier_key VALUES (1, decode(:'mac_secret','hex'));

-- Evidence and CAS validation happen IN ONE anchor transaction. This does
-- not prove independently governed source authority; both admins hold HMAC.
CREATE FUNCTION hw.advance_verified_receipt(
 p_term bigint,p_expected_seq bigint,p_new_seq bigint,p_nonce text,
 p_gen bigint,p_sum text,p_revision text,p_receipt_mac text
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $FUNC$
DECLARE signed_value text;
DECLARE accepted bigint;
BEGIN
 IF p_term <= 0 OR p_expected_seq <= 0 OR p_new_seq != p_expected_seq + 1
    OR p_gen <= 0 OR p_nonce !~ '^[0-9a-f]{32}$'
    OR p_sum !~ '^[0-9a-f]{64}$' OR p_revision !~ '^[0-9a-f]{64}$'
    OR p_receipt_mac !~ '^[0-9a-f]{64}$' THEN
   RAISE EXCEPTION 'malformed_receipt_or_nonadjacent_advance' USING ERRCODE='P0001';
 END IF;
 SELECT encode(public.hmac(pg_catalog.convert_to(concat_ws('|',
   'convergence-receipt-hmac-v1','auto-router-runtime-projection',
   p_term::text,p_new_seq::text,p_gen::text,p_sum,p_revision,p_nonce
 ),'UTF8'),secret,'sha256'),'hex')
   INTO signed_value FROM hw.receipt_verifier_key WHERE id=1;
 IF signed_value IS NULL OR signed_value != p_receipt_mac THEN
   RAISE EXCEPTION 'unverified_resource_commit_receipt' USING ERRCODE='P0001';
 END IF;
 UPDATE hw.highwater SET term=p_term,commit_seq=p_new_seq,last_nonce=p_nonce
 WHERE resource_id='auto-router-runtime-projection'
   AND term=p_term AND commit_seq=p_expected_seq
 RETURNING commit_seq INTO accepted;
 IF NOT FOUND THEN
   RAISE EXCEPTION 'anchor_cas_stale' USING ERRCODE='P0001';
 END IF;
 RETURN accepted;
END;
$FUNC$;
RESET ROLE;
REVOKE EXECUTE ON FUNCTION hw.advance_verified_receipt(bigint,bigint,bigint,text,bigint,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hw.advance_verified_receipt(bigint,bigint,bigint,text,bigint,text,text,text) TO hw_writer;
COMMIT;
