-- DISPOSABLE / RESEARCH ONLY. No production schema, no API integration.
-- Must be installed by an independent bootstrap principal on a private
-- isolated PostgreSQL 17 instance with SCRAM auth. No automatic runtime init.
CREATE SCHEMA assistx_trace_fence_research;
REVOKE ALL ON SCHEMA assistx_trace_fence_research FROM PUBLIC;

CREATE TABLE assistx_trace_fence_research.authority (
  singleton BOOLEAN PRIMARY KEY CHECK (singleton),
  epoch TEXT NOT NULL,
  capacity INTEGER NOT NULL CHECK (capacity BETWEEN 1 AND 16),
  schema_version TEXT NOT NULL
);
CREATE TABLE assistx_trace_fence_research.reservations (
  token TEXT PRIMARY KEY CHECK (token ~ '^[0-9a-f]{32}$'),
  epoch TEXT NOT NULL,
  query_ref TEXT NOT NULL UNIQUE,
  admitted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION assistx_trace_fence_research.admit(
  pinned_epoch TEXT, proposed_token TEXT, requested_ref TEXT
) RETURNS TABLE(accepted BOOLEAN, occupancy INTEGER, reason TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE v_cap INTEGER;
DECLARE v_count INTEGER;
BEGIN
  IF proposed_token IS NULL OR proposed_token !~ '^[0-9a-f]{32}$'
     OR requested_ref IS NULL OR requested_ref !~ '^[A-Za-z0-9_:.-]{1,128}$' THEN
    RETURN QUERY SELECT FALSE, NULL::INTEGER, 'invalid-request'::TEXT;
    RETURN;
  END IF;
  SELECT capacity INTO v_cap
  FROM assistx_trace_fence_research.authority
  WHERE singleton = TRUE AND epoch = pinned_epoch
    AND schema_version = 'assistx-pg-privilege-v1'
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT FALSE, NULL::INTEGER, 'epoch-unavailable'::TEXT;
    RETURN;
  END IF;
  SELECT COUNT(*)::INTEGER INTO v_count
  FROM assistx_trace_fence_research.reservations;
  IF v_count >= v_cap THEN
    RETURN QUERY SELECT FALSE, v_count, 'full'::TEXT;
    RETURN;
  END IF;
  INSERT INTO assistx_trace_fence_research.reservations(token,epoch,query_ref)
    VALUES (proposed_token,pinned_epoch,requested_ref)
    ON CONFLICT DO NOTHING;
  IF NOT FOUND THEN
    RETURN QUERY SELECT FALSE, v_count, 'duplicate'::TEXT;
    RETURN;
  END IF;
  RETURN QUERY SELECT TRUE, v_count + 1, 'admitted'::TEXT;
END;
$$;

CREATE FUNCTION assistx_trace_fence_research.inspect(pinned_epoch TEXT)
RETURNS INTEGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE v_epoch TEXT; v_count INTEGER;
BEGIN
  SELECT epoch INTO v_epoch
  FROM assistx_trace_fence_research.authority
  WHERE singleton=TRUE AND schema_version='assistx-pg-privilege-v1';
  IF NOT FOUND OR v_epoch IS DISTINCT FROM pinned_epoch THEN
    RETURN NULL;
  END IF;
  SELECT COUNT(*)::INTEGER INTO v_count
    FROM assistx_trace_fence_research.reservations;
  RETURN v_count;
END;
$$;

CREATE FUNCTION assistx_trace_fence_research.release_exact(
  pinned_epoch TEXT, exact_token TEXT, exact_ref TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE v_valid BOOLEAN;
DECLARE v_deleted INTEGER;
BEGIN
  -- CRITICAL: EXECUTE is granted ONLY to verifier role; never workers.
  -- The verifier process must check signed Neo4j transaction-closure proof.
  SELECT EXISTS (
    SELECT 1 FROM assistx_trace_fence_research.authority
    WHERE singleton=TRUE AND epoch=pinned_epoch
      AND schema_version='assistx-pg-privilege-v1'
  ) INTO v_valid;
  IF v_valid IS NOT TRUE THEN RETURN FALSE; END IF;
  DELETE FROM assistx_trace_fence_research.reservations
  WHERE token=exact_token AND epoch=pinned_epoch AND query_ref=exact_ref;
  GET DIAGNOSTICS v_deleted = ROW_COUNT;
  RETURN v_deleted=1;
END;
$$;

-- Default PostgreSQL functions grant EXECUTE to PUBLIC! Revoke explicitly.
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA assistx_trace_fence_research FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA assistx_trace_fence_research FROM PUBLIC;
