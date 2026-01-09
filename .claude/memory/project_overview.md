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

### Data Pipeline
- **Dagster** - Data orchestration framework
- **Pandas** - Data processing and transformation
- **Pandera** - Data validation and schema enforcement
- **boto3** - S3 client for Hetzner object storage

### Infrastructure
- **Vercel** - Serverless hosting platform
- **Hetzner S3** - Object storage for data files
- **Hetzner VPS** - Compute for Dagster pipeline
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

┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  WRM API    │────▶│  Dagster    │────▶│  Hetzner S3 │
│  (External)  │     │  (Pipeline)  │     │  (Storage)   │
└─────────────┘     └─────────────┘     └─────────────┘
                            │
                            ▼
                     ┌─────────────┐
                     │  Hetzner    │
                     │  VPS        │
                     │  (Compute)   │
                     └─────────────┘
```

## Data Flow

1. **Data Ingestion**: Dagster pipeline fetches station data from WRM API and stores in Hetzner S3
2. **Data Processing**: Dagster assets process, validate, and transform data into Parquet format
3. **Analytics**: DuckDB queries processed data for dashboard analytics
4. **API Layer**: Next.js Route Handlers serve data with Redis caching
5. **Visualization**: Plotly.js renders interactive charts and maps

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
├── wrm_pipeline/          # Dagster data pipeline
│   ├── wrm_pipeline/     # Pipeline code
│   │   ├── assets/       # Dagster assets
│   │   ├── jobs/         # Dagster jobs
│   │   ├── sensors/       # Dagster sensors
│   │   ├── resources/     # Dagster resources
│   │   └── models/       # Data schemas
│   └── wrm_pipeline_tests/ # Pipeline tests
├── storage/                # Storage utilities
│   └── wrm_data/        # WRM data handling
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

### Dagster Pipeline
- [`pipeline-analysis.md`](pipeline-analysis.md) - Dagster pipeline architecture and components
- [`improvement-roadmap.md`](improvement-roadmap.md) - Prioritized improvements for the pipeline
- [`data-engineering-best-practices.md`](data-engineering-best-practices.md) - 2024-2025 data engineering best practices
- [`infrastructure-constraints.md`](infrastructure-constraints.md) - Hetzner S3/VPS constraints and tool recommendations

## Environment Variables

### Required (Dashboard)
- `DUCKDB_PATH` - Path to DuckDB database file
- `S3_ACCESS_KEY_ID` - S3 access key
- `S3_SECRET_ACCESS_KEY` - S3 secret key
- `S3_ENDPOINT` - S3 endpoint URL
- `S3_REGION` - S3 region
- `UPSTASH_REDIS_REST_URL` - Upstash Redis URL
- `UPSTASH_REDIS_REST_TOKEN` - Upstash Redis token

### Required (Dagster Pipeline)
- `HETZNER_ENDPOINT_URL` - Hetzner S3 endpoint URL
- `HETZNER_ACCESS_KEY_ID` - Hetzner S3 access key
- `HETZNER_SECRET_ACCESS_KEY` - Hetzner S3 secret key
- `BUCKET_NAME` - S3 bucket name
- `WRM_STATIONS_S3_PREFIX` - S3 prefix for WRM data

### Optional
- `NEXT_PUBLIC_MAPBOX_TOKEN` - Mapbox access token (if using Mapbox)
- `LOG_LEVEL` - Logging level (default: info)
- `VAULT_URL` - HashiCorp Vault URL (if using Vault)
- `VAULT_TOKEN` - HashiCorp Vault token (if using Vault)

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
- [Dagster Documentation](https://docs.dagster.io)
- [Hetzner Documentation](https://docs.hetzner.com)
- [Hetzner S3 Documentation](https://docs.hetzner.com/storage/object-storage)
