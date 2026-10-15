-- Run only in a separate network-isolated throwaway PostgreSQL cluster.
-- This database intentionally has no resource tables and no source signer.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE hw_owner NOLOGIN;
CREATE ROLE hw_writer LOGIN PASSWORD :'writer_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE hw_reader LOGIN PASSWORD :'reader_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE SCHEMA hw AUTHORIZATION hw_owner;
REVOKE ALL ON SCHEMA hw FROM PUBLIC;
GRANT USAGE ON SCHEMA hw TO hw_writer, hw_reader;

SET ROLE hw_owner;
CREATE TABLE hw.highwater (
  resource_id text PRIMARY KEY, term bigint NOT NULL CHECK(term>0),
  commit_seq bigint NOT NULL CHECK(commit_seq>0),
  last_nonce text NOT NULL
);
INSERT INTO hw.highwater VALUES ('auto-router-runtime-projection',1,1,repeat('f',32));

CREATE FUNCTION hw.advance(
  p_term bigint, p_expected_seq bigint, p_new_term bigint,
  p_new_seq bigint, p_new_nonce text
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $FUNC$
DECLARE accepted bigint;
BEGIN
  IF p_term <= 0 OR p_expected_seq <= 0 OR p_new_term <= 0 OR p_new_seq <= 0
    OR p_new_nonce IS NULL OR length(p_new_nonce) != 32
    OR ROW(p_new_term,p_new_seq) <= ROW(p_term,p_expected_seq) THEN
    RAISE EXCEPTION 'not_monotonic' USING ERRCODE='P0001';
  END IF;
  UPDATE hw.highwater SET term=p_new_term, commit_seq=p_new_seq,
      last_nonce=p_new_nonce
    WHERE resource_id='auto-router-runtime-projection'
      AND term=p_term AND commit_seq=p_expected_seq
    RETURNING commit_seq INTO accepted;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'anchor_stale_or_restored' USING ERRCODE='P0001';
  END IF;
  RETURN accepted;
END;
$FUNC$;
RESET ROLE;
REVOKE EXECUTE ON FUNCTION hw.advance(bigint,bigint,bigint,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hw.advance(bigint,bigint,bigint,bigint,text) TO hw_writer;
GRANT SELECT ON hw.highwater TO hw_reader;
COMMIT;
