# DuckDB Integration with Next.js

**Last reviewed: 2026-01-09**

## Overview

DuckDB is used as the analytical database for the bike-data-flow dashboard, running in serverless environments (Vercel Functions) with HTTPFS extension for S3/MinIO data access.

## Serverless Considerations

- **Cold starts**: Connection initialization required on cold starts
- **Connection pooling**: Essential for performance - reuse connections across requests
- **Function timeouts**: Configure appropriately (30s default on Vercel)
- **Memory limits**: Affect query performance - monitor and optimize

## Connection Management

### Basic Connection Pattern

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

### Connection Pooling

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

## Query Optimization

### Best Practices

- Use materialized views for expensive queries
- Implement query result caching (via Upstash Redis)
- Optimize SQL with proper indexing
- Use parameterized queries to prevent injection

### Materialized Views

```sql
-- Pre-compute expensive queries
CREATE MATERIALIZED VIEW mv_station_latest AS
SELECT * FROM wrm_stations_latest;

-- Refresh strategy
REFRESH MATERIALIZED VIEW mv_station_latest;
```

## Performance Considerations

- **Typed array support**: Improves serialization and memory performance
- **HTTPFS extension**: Enables S3/MinIO data access
- **Connection reuse**: Pooling reduces cold start impact
- **Query caching**: Reduces database load for repeated queries

## Environment Variables Required

- `DUCKDB_PATH`: Path to DuckDB database file
- `S3_ACCESS_KEY_ID`: S3 access key for HTTPFS
- `S3_SECRET_ACCESS_KEY`: S3 secret key for HTTPFS
- `S3_ENDPOINT`: S3 endpoint URL
- `S3_REGION`: S3 region (or 'auto')

## References

- [DuckDB Node.js Documentation](https://duckdb.org/docs/stable/clients/nodejs/reference)
- [Serverless DuckDB GitHub Repository](https://github.com/duckdb/duckdb-node)
