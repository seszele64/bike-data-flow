-- WRM density_grid stub: expose the main-schema table as wrm.density_grid.
-- Executed by the Evidence duckdb connector against this folder's wrm.duckdb.
SELECT
  grid_lat,
  grid_lon,
  bike_count,
  station_count,
  density_per_1000m2
FROM main.density_grid
