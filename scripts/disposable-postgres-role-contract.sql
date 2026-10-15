-- All objects are created ONLY in a fresh ephemeral PG container.
-- No production host, schema, graph, credentials or database operations.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE lab_owner NOLOGIN;
CREATE ROLE lab_anchor_owner NOLOGIN;
CREATE ROLE lab_operator LOGIN PASSWORD :'operator_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE lab_signer LOGIN PASSWORD :'signer_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE lab_verifier LOGIN PASSWORD :'verifier_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE lab_observer LOGIN PASSWORD :'observer_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

CREATE SCHEMA lab AUTHORIZATION lab_owner;
REVOKE ALL ON SCHEMA lab FROM PUBLIC;
GRANT USAGE ON SCHEMA lab TO lab_operator, lab_signer, lab_verifier, lab_observer;

SET ROLE lab_owner;
CREATE TABLE lab.effect_state (
  resource_id text PRIMARY KEY,
  term bigint NOT NULL CHECK (term > 0),
  commit_seq bigint NOT NULL CHECK (commit_seq > 0),
  generation bigint NOT NULL CHECK (generation > 0),
  checksum text NOT NULL,
  revision_sha256 text NOT NULL,
  nonce text NOT NULL
);
CREATE TABLE lab.commit_receipts (
  commit_seq bigint PRIMARY KEY,
  term bigint NOT NULL,
  nonce text NOT NULL UNIQUE,
  generation bigint NOT NULL,
  checksum text NOT NULL,
  revision_sha256 text NOT NULL
);
INSERT INTO lab.effect_state VALUES (
  'auto-router-runtime-projection', 1, 1, 1,
  repeat('a',64), repeat('b',64), repeat('f',32)
);
INSERT INTO lab.commit_receipts VALUES (
  1, 1, repeat('f',32), 1, repeat('a',64), repeat('b',64)
);

CREATE FUNCTION lab.apply_effect(
  p_term bigint, p_expected_seq bigint, p_generation bigint, p_nonce text
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $FUNC$
DECLARE seq_after bigint;
BEGIN
  IF p_term <= 0 OR p_expected_seq <= 0 OR p_generation <= 0
      OR p_nonce IS NULL OR length(p_nonce) != 32 THEN
    RAISE EXCEPTION 'invalid_request' USING ERRCODE='P0001';
  END IF;

  UPDATE lab.effect_state
     SET commit_seq=commit_seq+1, generation=p_generation,
         checksum=repeat('c',64), revision_sha256=repeat('d',64),
         nonce=p_nonce
   WHERE resource_id='auto-router-runtime-projection'
     AND term=p_term AND commit_seq=p_expected_seq
   RETURNING commit_seq INTO seq_after;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'stale_term_or_sequence' USING ERRCODE='P0001';
  END IF;

  INSERT INTO lab.commit_receipts
    (commit_seq,term,nonce,generation,checksum,revision_sha256)
  VALUES
    (seq_after,p_term,p_nonce,p_generation,repeat('c',64),repeat('d',64));
  RETURN seq_after;
END;
$FUNC$;
RESET ROLE;

-- PostgreSQL grants EXECUTE to PUBLIC on newly created functions by default.
-- Revoke it explicitly before assigning the sole allowed caller.
REVOKE EXECUTE ON FUNCTION lab.apply_effect(bigint,bigint,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION lab.apply_effect(bigint,bigint,bigint,text) TO lab_operator;

CREATE TABLE lab.highwater (
 resource_id text PRIMARY KEY, term bigint NOT NULL,
 commit_seq bigint NOT NULL, last_nonce text NOT NULL
);
INSERT INTO lab.highwater VALUES ('auto-router-runtime-projection',1,1,repeat('f',32));
ALTER TABLE lab.highwater OWNER TO lab_anchor_owner;
GRANT SELECT ON lab.highwater TO lab_observer;
GRANT SELECT ON lab.effect_state, lab.commit_receipts TO lab_observer;

COMMIT;
