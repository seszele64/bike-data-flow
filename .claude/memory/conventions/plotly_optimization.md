# Plotly.js Dashboard Optimization

**Last reviewed: 2026-01-09**

## Overview

Plotly.js is used for data visualization in the bike-data-flow dashboard. Multiple optimization strategies are implemented to handle large datasets efficiently and ensure smooth user experience.

## Performance Improvements

### Typed Array Support

- Plotly.js supports typed arrays for data transfer
- Reduces serialization overhead by 50%
- Improves memory performance for large datasets
- Automatic in Plotly.py with base64 encoding

**Performance Impact:**
- Local rendering: 50% improvement
- Network transmission: Up to 40 seconds faster for 500K points on 4G
- No code changes required for users

## React Integration

### Best Practices

- Use `react-plotly.js` for React components
- Implement dynamic imports for code splitting
- Use `useResizeHandler` for responsive charts
- Optimize bundle size with tree-shaking

### Code Pattern

```typescript
import dynamic from 'next/dynamic';

const Plot = dynamic(() => import('react-plotly.js'), {
  ssr: false,
  loading: () => <ChartSkeleton />,
});

export function StationsMap({ stations }: { stations: Station[] }) {
  const data = [{
    type: 'scattermapbox',
    lat: stations.map(s => s.lat),
    lon: stations.map(s => s.lon),
    mode: 'markers',
    marker: {
      size: stations.map(s => Math.sqrt(s.bikes) * 5),
      color: stations.map(s => s.bikes),
      colorscale: 'RdYlGn',
    },
  }];

  const layout = {
    mapbox: { style: 'open-street-map', center: { lat: 51.1079, lon: 17.0385 }, zoom: 12 },
    margin: { l: 0, r: 0, t: 0, b: 0 },
    height: 600,
  };

  return <Plot data={data} layout={layout} useResizeHandler />;
}
```

## Large Dataset Handling

### Optimization Strategies

1. **Data Sampling** - Implement intelligent sampling for datasets > 10K points
2. **WebGL Rendering** - Configure Plotly.js to use WebGL for scatter plots and maps
3. **Virtual Scrolling** - Implement virtual scrolling for long lists
4. **Lazy Loading** - Load chart data on demand

### Data Sampling

```typescript
function sampleData(data: any[], maxPoints: number = 10000) {
  if (data.length <= maxPoints) return data;
  const step = Math.ceil(data.length / maxPoints);
  return data.filter((_, i) => i % step === 0);
}
```

### Advanced Data Sampling (Preserves Peaks and Outliers)

```typescript
function adaptiveSampleData(
  data: { x: number; y: number }[],
  maxPoints: number = 10000
): { x: number; y: number }[] {
  if (data.length <= maxPoints) return data;

  // Calculate step size
  const step = Math.ceil(data.length / maxPoints);
  
  // Sample evenly distributed points
  const sampled = data.filter((_, i) => i % step === 0);
  
  // Add peaks and outliers that might have been missed
  const values = data.map(d => d.y);
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const stdDev = Math.sqrt(
    values.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / values.length
  );
  
  // Add outliers (values beyond 2 standard deviations)
  const outliers = data.filter(
    (d, i) => Math.abs(d.y - mean) > 2 * stdDev && i % step !== 0
  );
  
  // Combine and sort by x value
  return [...sampled, ...outliers].sort((a, b) => a.x - b.x);
}
```

### WebGL Configuration

```typescript
const webglLayout = {
  scene: {
    xaxis: { type: 'linear' },
    yaxis: { type: 'linear' },
  },
  plot_bgcolor: 'rgba(0,0,0,0)',
  paper_bgcolor: 'rgba(0,0,0,0)',
};

const webglConfig = {
  responsive: true,
  displayModeBar: false,
  displaylogo: false,
  // Enable WebGL for better performance
  type: 'webgl',
};
```

### Time Series Aggregation

```typescript
function aggregateTimeSeries(
  data: { timestamp: Date; value: number }[],
  interval: 'hour' | 'day' | 'week'
): { timestamp: Date; value: number }[] {
  const grouped = new Map<string, number[]>();
  
  data.forEach(point => {
    const key = getAggregationKey(point.timestamp, interval);
    if (!grouped.has(key)) {
      grouped.set(key, []);
    }
    grouped.get(key)!.push(point.value);
  });
  
  return Array.from(grouped.entries()).map(([key, values]) => ({
    timestamp: parseAggregationKey(key, interval),
    value: values.reduce((a, b) => a + b, 0) / values.length,
  }));
}

function getAggregationKey(date: Date, interval: 'hour' | 'day' | 'week'): string {
  const d = new Date(date);
  switch (interval) {
    case 'hour':
      return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}-${d.getHours()}`;
    case 'day':
      return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    case 'week':
      const weekStart = new Date(d);
      weekStart.setDate(d.getDate() - d.getDay());
      return `${weekStart.getFullYear()}-${weekStart.getMonth()}-${weekStart.getDate()}`;
  }
}
```

## Best Practices for Datasets with 100K+ Points

1. **Always sample data before rendering** - Never send more than 10K points to Plotly.js
2. **Use WebGL for scatter plots and maps** - Set `type: 'webgl'` in trace configuration
3. **Implement server-side aggregation** - Pre-aggregate data in DuckDB queries
4. **Use virtual scrolling for lists** - Only render visible DOM elements
5. **Lazy load historical data** - Load data in chunks as user scrolls or zooms
6. **Cache aggregated results** - Store pre-computed aggregations in Redis
7. **Monitor memory usage** - Use browser DevTools to track memory consumption
8. **Implement progressive rendering** - Show skeleton states while data loads

## Memory Optimization Strategies

### Data Sampling
- Implement intelligent sampling for datasets > 10K points
- Use adaptive sampling based on zoom level
- Preserve important data points (peaks, outliers)

### WebGL Rendering
- Configure Plotly.js to use WebGL for scatter plots and maps
- Reduces memory footprint significantly
- Better performance for large datasets

### Virtual Scrolling
- Implement virtual scrolling for long lists
- Only render visible items
- Reduces DOM node count

### Lazy Loading
- Load chart data on demand
- Implement pagination for historical data
- Cache loaded data segments

## References

- [Plotly.js Documentation](https://plotly.com/javascript/)
- [Plotly Blog - Performance Update (Typed Arrays)](https://plotly.com/blog/typed-arrays/)
