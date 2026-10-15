-- WRM density_grid: read from the local main namespace in wrm.duckdb.
-- Rows are dual-written there by wrm_pipeline (local duckdb + S3 parquet);
-- the local table is the reliable source for dashboard builds (S3-03).
SELECT
  grid_lat,
  grid_lon,
  bike_count,
  station_count,
  density_per_1000m2
FROM main.density_grid
