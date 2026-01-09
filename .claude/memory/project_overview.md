# Bike Data Flow Dashboard - Project Overview

**Last reviewed: 2026-01-09**

## Project Description

The bike-data-flow dashboard is a web-based analytics platform for visualizing bike station data from Wrocław, Poland. The dashboard provides real-time and historical insights into bike availability, station density, and usage patterns.

## Technology Stack

### Frontend
- **Next.js 14+** - React framework with App Router
- **TypeScript** - Type-safe development
- **Plotly.js** - Data visualization library
- **Tailwind CSS** - Utility-first CSS framework

### Backend
- **Next.js Route Handlers** - Serverless API endpoints
- **DuckDB** - Analytical database for time-series data
- **Upstash Redis** - Caching and rate limiting

### Infrastructure
- **Vercel** - Serverless hosting platform
- **S3/MinIO** - Object storage for data files
- **GitHub** - Version control and CI/CD

## Key Features

1. **Real-time Station Map** - Interactive map showing current bike availability
2. **Station Density Analysis** - Heatmap visualization of station distribution
3. **Historical Trends** - Time-series charts of bike usage patterns
4. **Performance Monitoring** - Dashboard health and performance metrics

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Browser   │────▶│   Vercel    │────▶│   DuckDB    │
│  (Next.js)  │     │  (Edge/API) │     │  (Analytics)│
└─────────────┘     └─────────────┘     └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  Upstash    │
                    │   Redis     │
                    │  (Cache)    │
                    └─────────────┘
```

## Data Flow

1. **Data Ingestion**: WRM pipeline fetches station data and stores in S3/MinIO
2. **Data Processing**: DuckDB processes and aggregates data for analytics
3. **API Layer**: Next.js Route Handlers serve data with caching
4. **Visualization**: Plotly.js renders interactive charts and maps

## Project Structure

```
bike-data-flow/
├── app/                    # Next.js App Router
│   ├── api/               # API endpoints
│   ├── dashboard/         # Dashboard pages
│   └── layout.tsx         # Root layout
├── lib/                   # Utility libraries
│   ├── db/               # DuckDB connection
│   ├── cache/            # Redis caching
│   └── logger/           # Logging utilities
├── components/            # React components
│   ├── charts/           # Plotly chart components
│   └── ui/               # UI components
├── public/               # Static assets
└── .claude/              # Claude Code memory
    └── memory/           # Project memory files
```

## Development Workflow

1. **Feature Development**: Create feature branch from `main`
2. **Testing**: Run tests locally and verify functionality
3. **Code Review**: Submit pull request for review
4. **Deployment**: Merge to `main` triggers Vercel deployment
5. **Monitoring**: Check Vercel Analytics and logs

## Memory Files

### Architecture
- [`duckdb_integration.md`](architecture/duckdb_integration.md) - DuckDB connection and query patterns
- [`vercel_deployment.md`](architecture/vercel_deployment.md) - Vercel configuration and deployment

### Conventions
- [`nextjs_api.md`](conventions/nextjs_api.md) - Next.js API best practices
- [`plotly_optimization.md`](conventions/plotly_optimization.md) - Plotly.js performance optimization
- [`upstash_redis.md`](conventions/upstash_redis.md) - Redis caching patterns

### Decisions
- [`performance_targets.md`](decisions/performance_targets.md) - Performance targets and monitoring strategy

## Environment Variables

### Required
- `DUCKDB_PATH` - Path to DuckDB database file
- `S3_ACCESS_KEY_ID` - S3 access key
- `S3_SECRET_ACCESS_KEY` - S3 secret key
- `S3_ENDPOINT` - S3 endpoint URL
- `S3_REGION` - S3 region
- `UPSTASH_REDIS_REST_URL` - Upstash Redis URL
- `UPSTASH_REDIS_REST_TOKEN` - Upstash Redis token

### Optional
- `NEXT_PUBLIC_MAPBOX_TOKEN` - Mapbox access token (if using Mapbox)
- `LOG_LEVEL` - Logging level (default: info)

## Performance Targets

| Metric | Target |
|--------|--------|
| API Response Time | < 500ms (p95) |
| Page Load Time | < 2s (FCP) |
| Time to Interactive | < 3s (TTI) |
| Cache Hit Rate | > 80% |
| Error Rate | < 0.1% |

## References

- [Next.js Documentation](https://nextjs.org/docs)
- [DuckDB Documentation](https://duckdb.org/docs)
- [Plotly.js Documentation](https://plotly.com/javascript/)
- [Vercel Documentation](https://vercel.com/docs)
- [Upstash Documentation](https://upstash.com/docs)
