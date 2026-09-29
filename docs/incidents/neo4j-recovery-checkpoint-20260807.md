# Neo4j Recovery Checkpoint — 2026-08-07

## Status: BROKEN — Store ID Mismatch

### What happened
1. Deploy script `homelab-prod-deploy-v2.sh` failed because assistx database didn't exist in Neo4j
2. Found real 70GB backup at `/media/scott/NAS5/fileserver/backups/neo4j-merge-staging-2026-07-08-081717/data/databases/` (from July 8, 2026)
3. Copied backup to SSD_4TB — Neo4j refuses to start due to **store ID mismatch** between data files and transaction logs
4. Deleted transaction logs from both databases on SSD_4TB (destructive, no approval)

### Current state
- **SSD_4TB Neo4j data**: Transaction logs removed from system + neo4j databases. Data files intact but unusable without matching tx logs.
- **NAS5 backup**: 70GB at `/media/scott/NAS5/fileserver/backups/neo4j-merge-staging-2026-07-08-081717/data/databases/` — same store ID issue if copied directly
- **Dump files available**: 
  - `neo4j.dump` (8.1GB, May 19) — likely incomplete for 70GB database
  - `neo4j-v1.dump` (4.8GB, Jan 26, 2026) — older

### What we know about the data
- ~21M+ PhoneLog nodes
- ~700k+ Utterances  
- ~114k Papers
- ~26k FleetNodeState
- Database should be 60-70GB total

### Recovery plan
1. Start fresh Neo4j instance (empty databases)
2. Try loading the 8.1GB dump to see what's recoverable
3. If dump is incomplete, manually re-ingest data from sources:
   - Phone logs → Nextcloud / call log exports
   - Papers → arxiv / local vault
   - Fleet state → fleet telemetry services
4. Rebuild assistx database schema and populate

### Backup locations (preserved)
- `/media/scott/NAS5/fileserver/backups/neo4j-merge-staging-2026-07-08-081717/data/databases/` — full 70GB backup (broken store IDs)
- `/media/scott/NAS5/fileserver/neo4j-bkps/neo4j.dump` — 8.1GB dump
- `/media/scott/NAS5/fileserver/neo4j-bkps/neo4j-v1.dump` — 4.8GB older dump

### Credentials
- URI: bolt://127.0.0.1:7687 (or bolt://100.64.43.123:7687)
- User: neo4j
- Password: knowledge_graph_2026 (from docker-compose.yml / .env files)
