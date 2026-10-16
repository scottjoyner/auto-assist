-- One ephemeral, network-isolated PostgreSQL 17 cluster. NOT production.
-- ALL login role passwords are synthesized by the opt-in Docker runner.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE fx_owner NOLOGIN;
CREATE ROLE fx_executor LOGIN PASSWORD :'executor_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE fx_observer LOGIN PASSWORD :'observer_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE fx_reviewer LOGIN PASSWORD :'reviewer_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE fx_revoker LOGIN PASSWORD :'revoker_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE SCHEMA fx AUTHORIZATION fx_owner;
REVOKE ALL ON SCHEMA fx FROM PUBLIC;
GRANT USAGE ON SCHEMA fx TO fx_executor,fx_observer,fx_reviewer,fx_revoker;
SET ROLE fx_owner;
CREATE TABLE fx.authority (
  resource_id text PRIMARY KEY,
  term bigint NOT NULL CHECK(term>0),
  epoch bigint NOT NULL CHECK(epoch>0),
  owner_id text NOT NULL,
  revoked boolean NOT NULL
);
CREATE TABLE fx.applied (
  commit_seq bigint PRIMARY KEY CHECK(commit_seq>0),
  term bigint NOT NULL,
  epoch bigint NOT NULL,
  owner_id text NOT NULL,
  nonce text NOT NULL UNIQUE,
  digest text NOT NULL,
  CHECK(length(nonce)=32),
  CHECK(length(digest)=64)
);
INSERT INTO fx.authority VALUES ('auto-router-runtime-projection',4,9,'owner-a',false);
CREATE FUNCTION fx.execute_effect(
  p_term bigint,p_epoch bigint,p_owner text,p_seq bigint,
  p_nonce text,p_digest text
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $FUNC$
DECLARE current_row record;
BEGIN
  IF p_term IS NULL OR p_epoch IS NULL OR p_seq IS NULL
     OR p_term<=0 OR p_epoch<=0 OR p_seq<=0 OR p_owner IS NULL
     OR p_nonce IS NULL OR length(p_nonce) != 32
     OR p_digest IS NULL OR length(p_digest) != 64 THEN
    RAISE EXCEPTION 'invalid_effect_claim' USING ERRCODE='P0001';
  END IF;
  SELECT term,epoch,owner_id,revoked INTO STRICT current_row
    FROM fx.authority WHERE resource_id='auto-router-runtime-projection'
    FOR UPDATE;
  IF current_row.term != p_term OR current_row.epoch != p_epoch
     OR current_row.owner_id != p_owner OR current_row.revoked THEN
    RAISE EXCEPTION 'stale_or_revoked_at_effect_time' USING ERRCODE='P0001';
  END IF;
  INSERT INTO fx.applied VALUES(p_seq,p_term,p_epoch,p_owner,p_nonce,p_digest);
  RETURN p_seq;
END;
$FUNC$;
CREATE FUNCTION fx.rotate(
  p_expected_term bigint,p_expected_epoch bigint,
  p_new_term bigint,p_new_epoch bigint,p_new_owner text,p_revoked boolean
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $FUNC$
DECLARE accepted bigint;
BEGIN
  IF p_expected_term<=0 OR p_expected_epoch<=0 OR p_new_term<=0
      OR p_new_epoch<=0 OR p_new_owner IS NULL OR length(p_new_owner)=0
      OR p_revoked IS NULL OR
      ROW(p_new_term,p_new_epoch) <= ROW(p_expected_term,p_expected_epoch) THEN
    RAISE EXCEPTION 'invalid_authority_advance' USING ERRCODE='P0001';
  END IF;
  UPDATE fx.authority SET term=p_new_term,epoch=p_new_epoch,
      owner_id=p_new_owner,revoked=p_revoked
    WHERE resource_id='auto-router-runtime-projection'
      AND term=p_expected_term AND epoch=p_expected_epoch
    RETURNING epoch INTO accepted;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'stale_authority_advance' USING ERRCODE='P0001';
  END IF;
  RETURN accepted;
END;
$FUNC$;
RESET ROLE;
REVOKE ALL ON fx.authority,fx.applied FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION fx.execute_effect(bigint,bigint,text,bigint,text,text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION fx.rotate(bigint,bigint,bigint,bigint,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fx.execute_effect(bigint,bigint,text,bigint,text,text) TO fx_executor;
GRANT EXECUTE ON FUNCTION fx.rotate(bigint,bigint,bigint,bigint,text,boolean) TO fx_revoker;
GRANT SELECT ON fx.authority,fx.applied TO fx_observer,fx_reviewer;
COMMIT;
