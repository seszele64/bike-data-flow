# Next.js Serverless API Best Practices

**Last reviewed: 2026-01-09**

## Overview

All API endpoints in the bike-data-flow dashboard use Next.js 14+ App Router Route Handlers, following established patterns for serverless deployment on Vercel.

## Route Handler Structure

### File Organization

- Route handlers live in `app/api/` directory
- Each endpoint has a `route.ts` file
- Dynamic segments use `[id]` syntax
- Support all HTTP methods (GET, POST, PUT, DELETE, PATCH, OPTIONS)

### Standard Pattern

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

## Security

### CSRF Protection

- Use `@edge-csrf/nextjs` package for automatic CSRF token generation
- Middleware validates tokens on mutation requests (POST, PUT, DELETE, PATCH)
- Exclude public API paths from CSRF protection

### Input Validation

- Use Zod schemas for type-safe validation
- Return detailed error messages for validation failures
- Validate all query parameters and request bodies

```typescript
import { z } from 'zod';

const querySchema = z.object({
  startDate: z.string().datetime(),
  endDate: z.string().datetime(),
});

export async function GET(request: NextRequest) {
  const result = querySchema.safeParse(Object.fromEntries(request.nextUrl.searchParams));
  if (!result.success) {
    return NextResponse.json({ error: result.error }, { status: 400 });
  }
  // ...
}
```

### Rate Limiting

- Implement token bucket algorithm via `@upstash/ratelimit`
- Configure sliding window limits (e.g., 100 requests per minute)
- Track analytics for abuse detection

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

## Error Handling

### Custom Error Classes

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

### Error Handler Pattern

- Catch all errors in try/catch blocks
- Log errors with context (request ID, duration)
- Return user-friendly error messages
- Never expose sensitive information in error responses

## Request Context

### Utilities

- Use `headers()` for request headers
- Use `cookies()` for request cookies
- Use `NextRequest` for enhanced functionality

```typescript
import { headers, cookies } from 'next/headers';

export async function GET(request: NextRequest) {
  const headersList = headers();
  const cookieStore = cookies();
  // ...
}
```

## Response Format

### Standard Response Structure

```typescript
{
  "data": { ... },
  "metadata": {
    "timestamp": "2026-01-09T19:00:00Z",
    "duration": 123,
    "requestId": "abc-123"
  }
}
```

### Error Response Structure

```typescript
{
  "error": {
    "message": "Resource not found",
    "code": "NOT_FOUND",
    "statusCode": 404
  }
}
```

## Performance

### Best Practices

- Use structured logging with request IDs for tracing
- Implement comprehensive error handling with proper status codes
- Return consistent response format with metadata
- Use connection pooling for database connections
- Implement caching for expensive operations

### Performance Targets

- API Response Time: < 500ms (p95)
- Error Rate: < 0.1%

## References

- [Next.js API Routes Documentation](https://nextjs.org/docs/app/building-your-application/routing/route-handlers)
- [MakerKit - Next.js API Best Practices](https://makerkit.dev/blog/tutorials/nextjs-api-routes-best-practices)
