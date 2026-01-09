# Upstash Redis Caching Strategies

**Last reviewed: 2026-01-09**

## Overview

Upstash Redis is used for caching API responses, rate limiting, and session management in the bike-data-flow dashboard. It provides a serverless Redis solution with HTTP API access.

## Setup

### Installation

```bash
npm install @upstash/redis
```

### Configuration

```typescript
import { Redis } from "@upstash/redis";

const redis = Redis.fromEnv();

// Environment variables
// UPSTASH_REDIS_REST_URL=<YOUR_URL>
// UPSTASH_REDIS_REST_TOKEN=<YOUR_TOKEN>
```

## Caching Patterns

### API Response Caching

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

### Cache Configuration

```typescript
const CACHE_CONFIG = {
  '/api/stations/latest': { edge: 30, redis: 30 },
  '/api/analytics/density': { edge: 300, redis: 300 },
  '/api/analytics/trends': { edge: 900, redis: 900 },
  '/api/health': { edge: 10, redis: 10 },
};
```

### Usage Example

```typescript
export async function GET(request: NextRequest) {
  const cacheKey = `stations:latest`;
  const data = await cachedFetch(
    cacheKey,
    () => fetchStationsFromDuckDB(),
    30 // 30 seconds TTL
  );
  return NextResponse.json(data);
}
```

## Rate Limiting

### Implementation

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

### Usage in API Handler

```typescript
export async function GET(request: NextRequest) {
  const ip = request.headers.get('x-forwarded-for') || 'unknown';
  
  if (!(await checkRateLimit(ip))) {
    return NextResponse.json(
      { error: 'Rate limit exceeded' },
      { status: 429 }
    );
  }
  
  // ... rest of handler
}
```

## Session Management

### Use Cases

- User session storage
- Temporary data (OTP codes, pending appointments)
- Real-time state synchronization
- Rate limiting and abuse prevention

### Session Storage Pattern

```typescript
export async function setSession(userId: string, sessionData: any) {
  const key = `session:${userId}`;
  await redis.set(key, JSON.stringify(sessionData), { ex: 3600 }); // 1 hour
}

export async function getSession(userId: string) {
  const key = `session:${userId}`;
  const data = await redis.get(key);
  return data ? JSON.parse(data as string) : null;
}

export async function deleteSession(userId: string) {
  const key = `session:${userId}`;
  await redis.del(key);
}
```

## Cache Invalidation

### Strategies

1. **Time-based expiration** - Set TTL on cache entries
2. **Manual invalidation** - Delete cache keys on data updates
3. **Cache versioning** - Use version numbers in cache keys

### Manual Invalidation

```typescript
export async function invalidateCache(pattern: string) {
  const keys = await redis.keys(pattern);
  if (keys.length > 0) {
    await redis.del(...keys);
  }
}

// Usage: Invalidate all station-related cache
await invalidateCache('stations:*');
```

### Cache Versioning

```typescript
let cacheVersion = 1;

export async function incrementCacheVersion() {
  cacheVersion++;
}

export function getCacheKey(baseKey: string) {
  return `${baseKey}:v${cacheVersion}`;
}
```

## Best Practices

### Key Naming

- Use colons (`:`) as separators
- Include resource type and identifier
- Use consistent naming convention

```
stations:latest
stations:123
analytics:density:2026-01-09
session:user:456
ratelimit:ip:192.168.1.1
```

### TTL Configuration

- Set appropriate TTL based on data volatility
- Use shorter TTL for frequently changing data
- Use longer TTL for static or rarely changing data

### Error Handling

```typescript
export async function safeCacheFetch<T>(
  key: string,
  fetcher: () => Promise<T>,
  ttl: number
): Promise<T> {
  try {
    const cached = await redis.get(key);
    if (cached) return JSON.parse(cached) as T;
  } catch (error) {
    // Log error but continue to fetch
    console.error('Cache read error:', error);
  }

  const data = await fetcher();
  
  try {
    await redis.set(key, JSON.stringify(data), { ex: ttl });
  } catch (error) {
    // Log error but don't fail the request
    console.error('Cache write error:', error);
  }
  
  return data;
}
```

## Performance Considerations

- **Cache hit rate**: Target > 80% to reduce database load
- **TTL tuning**: Balance freshness vs. performance
- **Key size**: Keep keys short and descriptive
- **Value size**: Consider compression for large values

## Monitoring

### Metrics to Track

- Cache hit/miss ratios
- Average cache response time
- Error rates
- Memory usage

### Analytics

Upstash provides built-in analytics for:
- Request counts
- Error rates
- Response times
- Memory usage

## References

- [Upstash Redis Documentation](https://upstash.com/docs/redis/overall/getstarted)
- [Upstash Next.js with Redis](https://upstash.com/docs/redis/sdks/nextjs)
