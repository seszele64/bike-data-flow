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

<!-- Linked cross-filter: this ScatterPlot and the BubbleChart below share the exact
     connectGroup="wrm-link", so ECharts `connect()` syncs cursor/zoom/tooltip between them.
     Caveat: `connectGroup` is shipped but undocumented in @evidence-dev/core-components 5.4.2.
     It is wired in ECharts.svelte (export let connectGroup) -> component-utilities/echarts.js
     (chart.group = options.connectGroup; connect(options.connectGroup)). Canvas is the default renderer. -->

### Stations by location

<ScatterPlot
  data={stations}
  x="lon"
  y="lat"
  connectGroup="wrm-link"
  title="Stations by location"
/>

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

### Bike density map

<BubbleChart
  data={density}
  x="grid_lon"
  y="grid_lat"
  size="bike_count"
  connectGroup="wrm-link"
  title="Bike density by grid cell"
/>
