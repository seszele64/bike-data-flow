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

<!-- No ECharts cross-filter here: PointMap (below) and BubbleMap are Leaflet maps rendered
     via EvidenceMap.js/BaseMap.svelte, not ECharts, so ECharts `connect()` and its
     `connectGroup` option do not apply to them. `connectGroup` is still shipped (undocumented)
     in @evidence-dev/core-components 5.4.2 (ECharts.svelte -> component-utilities/echarts.js),
     but no component on this page sets it; Canvas is the default renderer.
     Tiles: BaseMap's `basemap` prop is a URL template, not a style name (e.g. "mono"/"blank").
     Both maps below set it explicitly to OpenStreetMap
     https://tile.openstreetmap.org/{z}/{x}/{y}.png, so tiles need network access to
     tile.openstreetmap.org. The template must be a braces-wrapped JS string
     (basemap={"..."}) so mdsvex does not treat {z}/{x}/{y} as Svelte expressions.
     OSM's tile usage policy (https://operations.osmfoundation.org/policies/tiles/)
     requires attribution, hence the `attribution` prop on each map. -->

### Stations by location

<PointMap
  data={stations}
  lat="lat"
  long="lon"
  height="400"
  title="Stations by location"
  basemap={"https://tile.openstreetmap.org/{z}/{x}/{y}.png"}
  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
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

<BubbleMap
  data={density}
  lat="grid_lat"
  long="grid_lon"
  size="bike_count"
  height="400"
  title="Bike density by grid cell"
  basemap={"https://tile.openstreetmap.org/{z}/{x}/{y}.png"}
  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
/>
