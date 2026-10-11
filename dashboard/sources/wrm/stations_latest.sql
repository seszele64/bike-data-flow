-- WRM stations_latest: read from the local main namespace in wrm.duckdb.
-- Rows are dual-written there by wrm_pipeline (local duckdb + S3 parquet);
-- the local table is the reliable source for dashboard builds (S3-03).
SELECT
  station_id,
  name,
  bikes,
  spaces,
  total_docks,
  installed,
  lat,
  lon,
  timestamp
FROM main.stations_latest
