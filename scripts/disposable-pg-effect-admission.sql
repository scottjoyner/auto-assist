-- Apply only to a disposable PG17 cluster already holding #399 fx roles/schema.
-- The issuer may assert ANY digest; this is NOT independently owned COMMIT.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE fx_issuer LOGIN PASSWORD :'issuer_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT USAGE ON SCHEMA fx TO fx_issuer;

SET ROLE fx_owner;
CREATE TABLE fx.authorizations (
  commit_seq bigint PRIMARY KEY CHECK(commit_seq > 0),
  term bigint NOT NULL CHECK(term > 0),
  epoch bigint NOT NULL CHECK(epoch > 0),
  owner_id text NOT NULL,
  nonce text NOT NULL UNIQUE,
  digest text NOT NULL,
  spent boolean NOT NULL DEFAULT false,
  CHECK(nonce ~ '^[0-9a-f]{32}$'),
  CHECK(digest ~ '^[0-9a-f]{64}$')
);

CREATE FUNCTION fx.issue_claim(
  p_term bigint,p_epoch bigint,p_owner text,p_seq bigint,
  p_nonce text,p_digest text
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
  SET search_path=pg_catalog AS $FUNC$
DECLARE authority_row record;
BEGIN
  IF p_term IS NULL OR p_epoch IS NULL OR p_seq IS NULL
     OR p_term <= 0 OR p_epoch <= 0 OR p_seq <= 0
     OR p_owner IS NULL OR length(p_owner)=0
     OR p_nonce IS NULL OR p_nonce !~ '^[0-9a-f]{32}$'
     OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid_issuer_claim' USING ERRCODE='P0001';
  END IF;
  SELECT term,epoch,owner_id,revoked INTO STRICT authority_row
    FROM fx.authority WHERE resource_id='auto-router-runtime-projection'
    FOR UPDATE;
  IF authority_row.term != p_term OR authority_row.epoch != p_epoch
    OR authority_row.owner_id != p_owner OR authority_row.revoked THEN
    RAISE EXCEPTION 'issuer_stale_or_revoked' USING ERRCODE='P0001';
  END IF;
  INSERT INTO fx.authorizations
    (commit_seq,term,epoch,owner_id,nonce,digest)
    VALUES(p_seq,p_term,p_epoch,p_owner,p_nonce,p_digest);
  RETURN p_seq;
END;
$FUNC$;

CREATE FUNCTION fx.execute_issued(
  p_term bigint,p_epoch bigint,p_owner text,p_seq bigint,
  p_nonce text,p_digest text
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
  SET search_path=pg_catalog AS $FUNC$
DECLARE authority_row record;
DECLARE admitted bigint;
BEGIN
  IF p_term IS NULL OR p_epoch IS NULL OR p_seq IS NULL
     OR p_term <= 0 OR p_epoch <= 0 OR p_seq <= 0
     OR p_owner IS NULL OR length(p_owner)=0
     OR p_nonce IS NULL OR p_nonce !~ '^[0-9a-f]{32}$'
     OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid_executor_claim' USING ERRCODE='P0001';
  END IF;
  SELECT term,epoch,owner_id,revoked INTO STRICT authority_row
    FROM fx.authority WHERE resource_id='auto-router-runtime-projection'
    FOR UPDATE;
  IF authority_row.term != p_term OR authority_row.epoch != p_epoch
    OR authority_row.owner_id != p_owner OR authority_row.revoked THEN
    RAISE EXCEPTION 'executor_stale_or_revoked' USING ERRCODE='P0001';
  END IF;
  UPDATE fx.authorizations SET spent=true
    WHERE commit_seq=p_seq AND term=p_term AND epoch=p_epoch
      AND owner_id=p_owner AND nonce=p_nonce AND digest=p_digest
      AND spent=false RETURNING commit_seq INTO admitted;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'issuer_receipt_missing_or_spent' USING ERRCODE='P0001';
  END IF;
  INSERT INTO fx.applied VALUES(p_seq,p_term,p_epoch,p_owner,p_nonce,p_digest);
  RETURN admitted;
END;
$FUNC$;
RESET ROLE;

-- Key fail-closed change relative to #399: the executor cannot invoke the
-- previous arbitrary-digest effect entrypoint.
REVOKE EXECUTE ON FUNCTION fx.execute_effect(bigint,bigint,text,bigint,text,text)
  FROM fx_executor;
REVOKE EXECUTE ON FUNCTION fx.issue_claim(bigint,bigint,text,bigint,text,text)
  FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION fx.execute_issued(bigint,bigint,text,bigint,text,text)
  FROM PUBLIC;
REVOKE ALL ON fx.authorizations FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fx.issue_claim(bigint,bigint,text,bigint,text,text)
  TO fx_issuer;
GRANT EXECUTE ON FUNCTION fx.execute_issued(bigint,bigint,text,bigint,text,text)
  TO fx_executor;
GRANT SELECT ON fx.authorizations TO fx_observer,fx_reviewer;
COMMIT;
