# Separate archive, projection, and backup contracts

TCKDB will use `tckdb.archive.v1` only for a versioned, integrity-checked package that restores declared state into an empty compatible database. Consumer-oriented NDJSON, CHEMKIN, and ML outputs remain export projections, while disaster recovery remains a PostgreSQL plus object-store operational backup; combining these contracts would either make the archive lossy or expose deployment-only secrets and runtime state.
