# Vercel Deployment Configuration

**Last reviewed: 2026-01-09**

## Overview

The bike-data-flow dashboard is deployed on Vercel, leveraging its serverless platform for Next.js applications with regional deployment in Europe.

## Deployment Configuration

### vercel.json

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

### Configuration Details

- **regions**: Frankfurt (fra1) and Warsaw (waw1) for European users
- **maxDuration**: 30 seconds for API functions
- **memory**: 1024MB for API functions
- **Cache-Control**: Edge caching with stale-while-revalidate

## Build Optimization

### next.config.js

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

## Environment Variables

### Configuration Rules

- Configure in Vercel dashboard or `.env` file
- Use `NEXT_PUBLIC_` prefix for client-side variables
- Never commit secrets to git
- Rotate credentials regularly

### Required Variables

- `DUCKDB_PATH`: Path to DuckDB database file
- `S3_ACCESS_KEY_ID`: S3 access key for HTTPFS
- `S3_SECRET_ACCESS_KEY`: S3 secret key for HTTPFS
- `S3_ENDPOINT`: S3 endpoint URL
- `S3_REGION`: S3 region
- `UPSTASH_REDIS_REST_URL`: Upstash Redis URL
- `UPSTASH_REDIS_REST_TOKEN`: Upstash Redis token

## Regional Deployment

### Best Practices

- Deploy to regions closest to users (fra1, waw1 for Europe)
- Configure edge functions for global distribution
- Use Vercel Edge Network for static assets
- Monitor latency across regions

### Edge Functions

Edge functions are deployed globally and have:
- Lower cold start times
- Smaller execution limits
- No file system access
- Limited runtime capabilities

## Custom Domain & SSL

### Setup Process

1. Add custom domain in Vercel dashboard
2. Configure DNS records (A or CNAME)
3. Verify SSL certificate (automatic via Let's Encrypt)
4. Set up domain redirects if needed

### DNS Configuration

```
A Record: @ → 76.76.21.21
CNAME: www → cname.vercel-dns.com
```

## Deployment Workflow

### Automatic Deployments

- Git push triggers automatic deployment
- Preview deployments for pull requests
- Production deployments on main branch merge

### Deployment Hooks

- Build: `npm run build`
- Start: `npm start`
- Health check: `/api/health`

## Monitoring

### Vercel Analytics

- Performance metrics
- Core Web Vitals
- Real user monitoring
- Error tracking

### Logs

- Real-time logs in Vercel dashboard
- Structured logging with Pino
- Request ID tracing

## References

- [Vercel Documentation](https://vercel.com/docs)
- [Next.js on Vercel](https://vercel.com/docs/frameworks/nextjs)
