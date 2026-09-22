---
title: Bike Data Flow Dashboard
---

# Bike Data Flow Dashboard

> **Live data:** rendered from the current `wrm.stations_latest` and
> `wrm.density_grid` snapshots.

## Stations

```sql stations
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
FROM wrm.stations_latest
ORDER BY timestamp DESC, name
```

<DataTable data={stations} />

### Bikes per station

<BarChart data={stations} x="name" y="bikes" />

### Bikes over time

<LineChart data={stations} x="timestamp" y="bikes" />

## Spatial density

```sql density
SELECT
  grid_lat,
  grid_lon,
  bike_count,
  station_count,
  density_per_1000m2
FROM wrm.density_grid
ORDER BY density_per_1000m2 DESC, grid_lat, grid_lon
```

<DataTable data={density} />

### Density per 1000 m² grid cell

<BarChart data={density} x="grid_lat" y="density_per_1000m2" />
