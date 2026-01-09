# Bike Data Flow Dashboard - Research Findings

## Research Summary

This document contains research findings gathered during the planning phase for the bike-data-flow dashboard implementation. Research focused on Next.js serverless APIs, DuckDB integration, Plotly.js optimization, Vercel deployment, and Redis/Upstash caching strategies.

---

## 1. Next.js Serverless API Best Practices

### 1.1 Route Handlers (App Router)

**Key Findings**:
- Route Handlers live in `app/api/` directory with `route.ts` files
- Support all HTTP methods (GET, POST, PUT, DELETE, PATCH, OPTIONS)
- Use `NextRequest` and `NextResponse` for enhanced functionality
- Dynamic route segments use `[id]` syntax in directory structure

**Best Practices**:
- Use structured logging with request IDs for tracing
- Implement comprehensive error handling with proper status codes
- Validate input data using Zod schemas
- Use `headers()` and `cookies()` utilities for request context
- Return consistent response format with metadata

**Code Pattern**:
```typescript
// app/api/stations/latest/route.ts
import { NextRequest, NextResponse } from 'next/server';
import { getRequestLogger } from '@/lib/logger/request';

export async function GET(request: NextRequest) {
  const log = getRequestLogger();
  const startTime = performance.now();

  try {
    log.info({ query: Object.fromEntries(request.nextUrl.searchParams) }, 'processing request');
    const data = await fetchData();
    const duration = Math.round(performance.now() - startTime);
    log.info({ duration }, 'request successful');
    return NextResponse.json(data);
  } catch (error) {
    const duration = Math.round(performance.now() - startTime);
    log.error({ error, duration }, 'request failed');
    return NextResponse.json({ error: 'Internal Server Error' }, { status: 500 });
  }
}
```

### 1.2 Security Considerations

**CSRF Protection**:
- Use `@edge-csrf/nextjs` package for automatic CSRF token generation
- Middleware validates tokens on mutation requests (POST, PUT, DELETE, PATCH)
- Exclude public API paths from CSRF protection

**Input Validation**:
- Use Zod schemas for type-safe validation
- Return detailed error messages for validation failures
- Validate all query parameters and request bodies

**Rate Limiting**:
- Implement token bucket algorithm via `@upstash/ratelimit`
- Configure sliding window limits (e.g., 100 requests per minute)
- Track analytics for abuse detection

### 1.3 Error Handling

**Custom Error Classes**:
```typescript
export class APIError extends Error {
  constructor(message: string, public statusCode: number = 500, public code: string = 'INTERNAL_ERROR') {
    super(message);
  }
}

export class DatabaseError extends APIError {
  constructor(message: string) {
    super(message, 503, 'DATABASE_ERROR');
  }
}

export class NotFoundError extends APIError {
  constructor(resource: string) {
    super(`${resource} not found`, 404, 'NOT_FOUND');
  }
}
```

**Error Handler Pattern**:
- Catch all errors in try/catch blocks
- Log errors with context (request ID, duration)
- Return user-friendly error messages
- Never expose sensitive information in error responses

---

## 2. DuckDB Integration with Next.js

### 2.1 Serverless DuckDB Patterns

**Key Findings**:
- DuckDB can run in serverless environments (AWS Lambda, Vercel Functions)
- HTTPFS extension enables S3/MinIO data access
- Connection pooling is essential for performance
- Typed array support improves serialization and memory performance

**Serverless Considerations**:
- Cold starts require connection initialization
- Use connection pooling to reuse connections
- Configure appropriate function timeouts (30s default on Vercel)
- Memory limits affect query performance

### 2.2 Connection Management

**Connection Pattern**:
```typescript
import duckdb from 'duckdb';

const DB_PATH = process.env.DUCKDB_PATH || './db/analytics.duckdb';

export function getDuckDBConnection() {
  const conn = duckdb.connect(DB_PATH);

  // Configure S3 access
  conn.exec(`
    INSTALL httpfs;
    LOAD httpfs;
    SET s3_region='auto';
    SET s3_access_key_id='${process.env.S3_ACCESS_KEY_ID}';
    SET s3_secret_access_key='${process.env.S3_SECRET_ACCESS_KEY}';
    SET s3_endpoint='${process.env.S3_ENDPOINT}';
    SET s3_use_ssl='true';
    SET s3_url_style='path';
  `);

  return conn;
}
```

**Connection Pooling**:
```typescript
import { Pool } from 'generic-pool';

const pool = Pool({
  create: () => getDuckDBConnection(),
  destroy: (conn) => conn.close(),
  max: 10,
  min: 2,
  idleTimeoutMillis: 30000,
});

export async function queryDuckDB(sql: string, params: any[] = []) {
  const conn = await pool.acquire();
  try {
    return conn.all(sql, ...params);
  } finally {
    pool.release(conn);
  }
}
```

### 2.3 Query Optimization

**Best Practices**:
- Use materialized views for expensive queries
- Implement query result caching
- Optimize SQL with proper indexing
- Use parameterized queries to prevent injection

**Materialized Views**:
```sql
-- Pre-compute expensive queries
CREATE MATERIALIZED VIEW mv_station_latest AS
SELECT * FROM wrm_stations_latest;

-- Refresh strategy
REFRESH MATERIALIZED VIEW mv_station_latest;
```

---

## 3. Plotly.js Dashboard Optimization

### 3.1 Performance Improvements

**Typed Array Support**:
- Plotly.js now supports typed arrays for data transfer
- Reduces serialization overhead by 50%
- Improves memory performance for large datasets
- Automatic in Plotly.py with base64 encoding

**Performance Impact**:
- Local rendering: 50% improvement
- Network transmission: Up to 40 seconds faster for 500K points on 4G
- No code changes required for users

### 3.2 React Integration

**Best Practices**:
- Use `react-plotly.js` for React components
- Implement dynamic imports for code splitting
- Use `useResizeHandler` for responsive charts
- Optimize bundle size with tree-shaking

**Code Pattern**:
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

### 3.3 Large Dataset Handling

**Optimization Strategies**:
- Implement data sampling for very large datasets
- Use WebGL rendering for better performance
- Lazy load chart data on scroll
- Implement virtual scrolling for long lists

**Data Sampling**:
```typescript
function sampleData(data: any[], maxPoints: number = 10000) {
  if (data.length <= maxPoints) return data;
  const step = Math.ceil(data.length / maxPoints);
  return data.filter((_, i) => i % step === 0);
}
```

#### Memory Optimization Strategies

**Data Sampling**:
- Implement intelligent sampling for datasets > 10K points
- Use adaptive sampling based on zoom level
- Preserve important data points (peaks, outliers)

**WebGL Rendering**:
- Configure Plotly.js to use WebGL for scatter plots and maps
- Reduces memory footprint significantly
- Better performance for large datasets

**Virtual Scrolling**:
- Implement virtual scrolling for long lists
- Only render visible items
- Reduces DOM node count

**Lazy Loading**:
- Load chart data on demand
- Implement pagination for historical data
- Cache loaded data segments

**Code Example - Advanced Data Sampling**:
```typescript
// Adaptive sampling that preserves peaks and outliers
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

// WebGL configuration for Plotly.js
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

// Time series aggregation utility
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

**Best Practices for Datasets with 100K+ Points**:
1. **Always sample data before rendering** - Never send more than 10K points to Plotly.js
2. **Use WebGL for scatter plots and maps** - Set `type: 'webgl'` in trace configuration
3. **Implement server-side aggregation** - Pre-aggregate data in DuckDB queries
4. **Use virtual scrolling for lists** - Only render visible DOM elements
5. **Lazy load historical data** - Load data in chunks as user scrolls or zooms
6. **Cache aggregated results** - Store pre-computed aggregations in Redis
7. **Monitor memory usage** - Use browser DevTools to track memory consumption
8. **Implement progressive rendering** - Show skeleton states while data loads

---

## 4. Vercel Deployment Strategies

### 4.1 Deployment Configuration

**vercel.json Configuration**:
```json
{
  "buildCommand": "npm run build",
  "outputDirectory": ".next",
  "framework": "nextjs",
  "regions": ["fra1", "waw1"],
  "functions": {
    "app/api/**/*.ts": {
      "maxDuration": 30,
      "memory": 1024
    }
  },
  "headers": [
    {
      "source": "/api/(.*)",
      "headers": [
        {
          "key": "Cache-Control",
          "value": "public, s-maxage=30, stale-while-revalidate=60"
        }
      ]
    }
  ]
}
```

**Environment Variables**:
- Configure in Vercel dashboard or `.env` file
- Use `NEXT_PUBLIC_` prefix for client-side variables
- Never commit secrets to git
- Rotate credentials regularly

### 4.2 Build Optimization

**next.config.js**:
```javascript
module.exports = {
  images: {
    domains: ['your-cdn-domain.com'],
    formats: ['image/avif', 'image/webp'],
  },
  compress: true,
  swcMinify: true,
  build: {
    outDir: process.env.VERCEL_GITHUB_COMMIT_REF ? 'next-build' : 'build',
  },
};
```

### 4.3 Regional Deployment

**Best Practices**:
- Deploy to regions closest to users (fra1, waw1 for Europe)
- Configure edge functions for global distribution
- Use Vercel Edge Network for static assets
- Monitor latency across regions

### 4.4 Custom Domain & SSL

**Setup Process**:
1. Add custom domain in Vercel dashboard
2. Configure DNS records (A or CNAME)
3. Verify SSL certificate (automatic via Let's Encrypt)
4. Set up domain redirects if needed

---

## 5. Redis/Upstash Caching Strategies

### 5.1 Upstash Integration

**Setup**:
```bash
npm install @upstash/redis
```

**Configuration**:
```typescript
import { Redis } from "@upstash/redis";

const redis = Redis.fromEnv();

// Environment variables
// UPSTASH_REDIS_REST_URL=<YOUR_URL>
// UPSTASH_REDIS_REST_TOKEN=<YOUR_TOKEN>
```

### 5.2 Caching Patterns

**API Response Caching**:
```typescript
export async function cachedFetch<T>(
  key: string,
  fetcher: () => Promise<T>,
  ttl: number
): Promise<T> {
  const cached = await redis.get(key);
  if (cached) return JSON.parse(cached) as T;

  const data = await fetcher();
  await redis.set(key, JSON.stringify(data), { ex: ttl });
  return data;
}
```

**Cache Configuration**:
```typescript
const CACHE_CONFIG = {
  '/api/stations/latest': { edge: 30, redis: 30 },
  '/api/analytics/density': { edge: 300, redis: 300 },
  '/api/analytics/trends': { edge: 900, redis: 900 },
  '/api/health': { edge: 10, redis: 10 },
};
```

### 5.3 Rate Limiting

**Implementation**:
```typescript
import { Ratelimit } from '@upstash/ratelimit';

const ratelimit = new Ratelimit({
  redis: Redis.fromEnv(),
  limiter: Ratelimit.slidingWindow(100, '1m'),
  analytics: true,
});

export async function checkRateLimit(ip: string) {
  const { success } = await ratelimit.limit(ip);
  return success;
}
```

### 5.4 Session Management

**Use Cases**:
- User session storage
- Temporary data (OTP codes, pending appointments)
- Real-time state synchronization
- Rate limiting and abuse prevention

---

## 6. Key Insights & Recommendations

### 6.1 Architecture Decisions

**Serverless Choice**:
- Vercel provides optimal Next.js integration
- Automatic scaling reduces operational overhead
- Edge functions enable global distribution
- Cost-effective for variable traffic patterns

**Caching Strategy**:
- Multi-layer approach (Edge + Redis + Browser)
- Tiered TTL based on data volatility
- Stale-while-revalidate for better UX
- Cache invalidation on data updates

**Database Choice**:
- DuckDB provides excellent analytical performance
- HTTPFS enables S3/MinIO integration
- Connection pooling essential for serverless
- Materialized views for expensive queries

### 6.2 Performance Targets

| Metric | Target | Rationale |
|--------|--------|-----------|
| API Response Time | < 500ms (p95) | User experience threshold |
| Page Load Time | < 2s (FCP) | Core Web Vitals standard |
| Time to Interactive | < 3s (TTI) | Core Web Vitals standard |
| Cache Hit Rate | > 80% | Reduces database load |
| Error Rate | < 0.1% | Production reliability |

### 6.3 Security Recommendations

1. **Input Validation**: Validate all inputs using Zod schemas
2. **Rate Limiting**: Implement token bucket algorithm (100 req/min)
3. **CORS Configuration**: Allow only necessary origins
4. **Secret Management**: Use Vercel environment variables
5. **S3 Access**: Read-only credentials for dashboard
6. **CSRF Protection**: Implement for mutation endpoints

### 6.4 Monitoring Strategy

**Metrics to Track**:
- API response times and error rates
- Cache hit/miss ratios
- Database query performance
- User engagement and page views
- System health and uptime

**Tools**:
- Vercel Analytics for performance
- Pino for structured logging
- Custom error tracking (Sentry)
- Uptime monitoring (Pingdom/UptimeRobot)

---

## 7. Open Questions & Risks

### 7.1 Open Questions

1. What is the expected concurrent user load?
2. Are there specific compliance requirements (GDPR, etc.)?
3. What is the budget for hosting and infrastructure?
4. Are there existing authentication systems to integrate?

### 7.2 Identified Risks

| Risk | Impact | Mitigation |
|-------|---------|------------|
| DuckDB cold start latency | Medium | Connection pooling, warm-up queries |
| Vercel function timeouts | High | Optimize queries, increase timeout |
| Cache invalidation complexity | Medium | Implement cache versioning |
| Large dataset rendering | Medium | Data sampling, lazy loading |
| S3/MinIO availability | High | Monitor uptime, implement fallbacks |

---

## 8. References

### 8.1 Documentation
- [Next.js API Routes Documentation](https://nextjs.org/docs/app/building-your-application/routing/route-handlers)
- [DuckDB Node.js Documentation](https://duckdb.org/docs/stable/clients/nodejs/reference)
- [Plotly.js Documentation](https://plotly.com/javascript/)
- [Vercel Documentation](https://vercel.com/docs)
- [Upstash Redis Documentation](https://upstash.com/docs/redis/overall/getstarted)

### 8.2 Research Sources
- MakerKit - Next.js API Best Practices
- Plotly Blog - Performance Update (Typed Arrays)
- Serverless DuckDB GitHub Repository
- Upstash Documentation - Next.js with Redis
- The Pi Guy Blog - Vercel Deployment Guide

---

*Last Updated: 2026-01-09*
*Research Phase: Complete*
