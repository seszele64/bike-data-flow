-- WRM stations_latest stub: expose the main-schema table as wrm.stations_latest.
-- Executed by the Evidence duckdb connector against this folder's wrm.duckdb.
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
