# Bike Data Flow Dashboard - Claude Context

**Last updated: 2026-01-09**

## Project Overview

This is a web-based analytics dashboard for visualizing bike station data from Wrocław, Poland. The dashboard provides real-time and historical insights into bike availability, station density, and usage patterns.

## Technology Stack

- **Frontend**: Next.js 14+ (App Router), TypeScript, Plotly.js, Tailwind CSS
- **Backend**: Next.js Route Handlers, DuckDB, Upstash Redis
- **Infrastructure**: Vercel (serverless), S3/MinIO (object storage)

## Memory Files

### Core Documentation

- [`memory/project_overview.md`](memory/project_overview.md) - Complete project overview, architecture, and development workflow

### Architecture (System Structure)

- [`memory/architecture/duckdb_integration.md`](memory/architecture/duckdb_integration.md) - DuckDB connection patterns, serverless considerations, query optimization
- [`memory/architecture/vercel_deployment.md`](memory/architecture/vercel_deployment.md) - Vercel configuration, build optimization, regional deployment

### Conventions (Standard Practices)

- [`memory/conventions/nextjs_api.md`](memory/conventions/nextjs_api.md) - Next.js API best practices, security, error handling
- [`memory/conventions/plotly_optimization.md`](memory/conventions/plotly_optimization.md) - Plotly.js performance optimization, large dataset handling
- [`memory/conventions/upstash_redis.md`](memory/conventions/upstash_redis.md) - Redis caching patterns, rate limiting, session management

### Decisions (Historical Record)

- [`memory/decisions/performance_targets.md`](memory/decisions/performance_targets.md) - Performance targets, monitoring strategy, architecture decisions

## Key Performance Targets

| Metric | Target |
|--------|--------|
| API Response Time | < 500ms (p95) |
| Page Load Time | < 2s (FCP) |
| Time to Interactive | < 3s (TTI) |
| Cache Hit Rate | > 80% |
| Error Rate | < 0.1% |

## Important Conventions

### API Development
- Use Next.js Route Handlers in `app/api/` directory
- Implement structured logging with request IDs
- Validate all inputs using Zod schemas
- Return consistent response format with metadata
- Use connection pooling for DuckDB

### Caching Strategy
- Multi-layer approach: Edge + Redis + Browser
- Tiered TTL based on data volatility
- Use stale-while-revalidate for better UX
- Implement cache invalidation on data updates

### Data Visualization
- Always sample data before rendering (max 10K points)
- Use WebGL for scatter plots and maps
- Implement server-side aggregation in DuckDB
- Use virtual scrolling for long lists
- Lazy load historical data

### Security
- Implement rate limiting (100 req/min)
- Use CSRF protection for mutation endpoints
- Validate all inputs using Zod schemas
- Never expose sensitive information in error responses
- Use Vercel environment variables for secrets

## Environment Variables

### Required
- `DUCKDB_PATH` - Path to DuckDB database file
- `S3_ACCESS_KEY_ID` - S3 access key
- `S3_SECRET_ACCESS_KEY` - S3 secret key
- `S3_ENDPOINT` - S3 endpoint URL
- `S3_REGION` - S3 region
- `UPSTASH_REDIS_REST_URL` - Upstash Redis URL
- `UPSTASH_REDIS_REST_TOKEN` - Upstash Redis token

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

## Development Notes

- The project is currently in the planning phase
- Research findings are documented in [`plans/findings.md`](plans/findings.md)
- Implementation will follow the conventions documented in memory files
- All code should follow the established patterns and best practices

## References

- [Next.js Documentation](https://nextjs.org/docs)
- [DuckDB Documentation](https://duckdb.org/docs)
- [Plotly.js Documentation](https://plotly.com/javascript/)
- [Vercel Documentation](https://vercel.com/docs)
- [Upstash Documentation](https://upstash.com/docs)
