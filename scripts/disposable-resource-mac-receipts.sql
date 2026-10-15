-- DO NOT apply to a real DB. Install ONLY atop disposable-postgres-role-contract.sql.
-- `mac_secret` is a synthetic ephemeral 32-byte HMAC key provided by runner.
\set ON_ERROR_STOP on
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
REVOKE EXECUTE ON FUNCTION lab.apply_effect(bigint,bigint,bigint,text) FROM lab_operator;
SET ROLE lab_owner;
CREATE TABLE lab.receipt_mac_key (id integer PRIMARY KEY, secret bytea NOT NULL);
INSERT INTO lab.receipt_mac_key VALUES (1, decode(:'mac_secret','hex'));
ALTER TABLE lab.commit_receipts ADD COLUMN receipt_mac text;

-- Both clusters use a pinned, domain-separated, strictly ordered message.
CREATE FUNCTION lab.receipt_payload(
 p_term bigint,p_seq bigint,p_gen bigint,p_sum text,p_revision text,p_nonce text
) RETURNS text LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $BODY$
 SELECT concat_ws('|', 'convergence-receipt-hmac-v1',
   'auto-router-runtime-projection', p_term::text, p_seq::text,
   p_gen::text, p_sum, p_revision, p_nonce)
$BODY$;

UPDATE lab.commit_receipts r SET receipt_mac=encode(
 public.hmac(pg_catalog.convert_to(lab.receipt_payload(
   r.term,r.commit_seq,r.generation,r.checksum,r.revision_sha256,r.nonce
 ),'UTF8'), (SELECT secret FROM lab.receipt_mac_key WHERE id=1), 'sha256'), 'hex'
);
ALTER TABLE lab.commit_receipts ALTER COLUMN receipt_mac SET NOT NULL;

CREATE FUNCTION lab.commit_with_mac(
 p_term bigint,p_expected_seq bigint,p_gen bigint,p_nonce text
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $FUNC$
DECLARE accepted bigint;
DECLARE proof text;
BEGIN
 IF p_term <= 0 OR p_expected_seq <= 0 OR p_gen <= 0
    OR p_nonce !~ '^[0-9a-f]{32}$' THEN
   RAISE EXCEPTION 'invalid_commit_claim' USING ERRCODE='P0001';
 END IF;
 UPDATE lab.effect_state
    SET commit_seq=commit_seq+1, generation=p_gen,
        checksum=repeat('c',64),revision_sha256=repeat('d',64),nonce=p_nonce
  WHERE resource_id='auto-router-runtime-projection'
    AND term=p_term AND commit_seq=p_expected_seq
  RETURNING commit_seq INTO accepted;
 IF NOT FOUND THEN
   RAISE EXCEPTION 'stale_commit_sequence' USING ERRCODE='P0001';
 END IF;
 SELECT encode(public.hmac(pg_catalog.convert_to(
    lab.receipt_payload(p_term,accepted,p_gen,repeat('c',64),repeat('d',64),p_nonce),
    'UTF8'),secret,'sha256'),'hex')
   INTO proof FROM lab.receipt_mac_key WHERE id=1;
 IF proof IS NULL THEN
   RAISE EXCEPTION 'proof_key_unavailable' USING ERRCODE='P0001';
 END IF;
 INSERT INTO lab.commit_receipts
   (commit_seq,term,nonce,generation,checksum,revision_sha256,receipt_mac)
 VALUES (accepted,p_term,p_nonce,p_gen,repeat('c',64),repeat('d',64),proof);
 RETURN accepted;
END;
$FUNC$;
RESET ROLE;
REVOKE EXECUTE ON FUNCTION lab.receipt_payload(bigint,bigint,bigint,text,text,text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION lab.commit_with_mac(bigint,bigint,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION lab.commit_with_mac(bigint,bigint,bigint,text) TO lab_operator;
COMMIT;
