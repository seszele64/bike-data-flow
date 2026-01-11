# Research: Retry Logic with Exponential Backoff

**Feature Branch**: `002-retry-logic`  
**Date**: 2026-01-11  
**Status**: Complete

## Open Clarifications Resolved

### 1. Retry Defaults

**Decision**: Adopt AWS SDK v2 (boto3) default retry behavior with enhanced configuration

| Operation Type | Max Attempts | Base Delay | Max Delay | Jitter |
|----------------|--------------|------------|-----------|--------|
| S3 Operations | 5 (total) | 1 second | 30 seconds | Yes |
| API Calls | 3 (total) | 0.5 seconds | 10 seconds | Yes |

**Rationale**:
- AWS SDK default is 3 total attempts (1 initial + 2 retries)
- S3 operations warrant more attempts due to rate limiting (429) being common
- Base delay of 1 second for S3 aligns with AWS best practices
- Max delay caps prevent excessive wait times
- Jitter is essential to prevent thundering herd when multiple clients retry simultaneously

**Alternatives Considered**:
- AWS SDK defaults only: Too aggressive for rate limiting scenarios
- Custom exponential without jitter: Risk of synchronized retries

### 2. Circuit Breaker Storage

**Decision**: In-memory only for first implementation, with optional Redis integration for distributed scenarios

**Rationale**:
- Dagster pipelines are typically single-instance during execution
- In-memory provides lowest latency
- Redis integration can be added when multi-instance deployment is required
- In-memory circuit breaker is sufficient for preventing cascade failures within a single pipeline run

**Alternatives Considered**:
- Persistent storage: Overkill for current deployment model
- External database: Adds latency and complexity

### 3. Monitoring Integration

**Decision**: Emit structured logs with retry metrics; optional Prometheus metrics via Dagster telemetry

**Rationale**:
- Logs are already integrated with Dagster's observability stack
- Structured logging format enables easy parsing by log aggregators
- Prometheus metrics can be added via Dagster's metrics integration
- Circuit breaker state changes logged at INFO level for debugging

**Implementation**:
```
Log format:
{"event": "retry_attempt", "attempt": 2, "max_attempts": 5, "exception": "ThrottlingException"}
{"event": "circuit_breaker_open", "operation": "s3_upload", "failure_count": 5}
```

**Alternatives Considered**:
- Direct Prometheus metrics: Requires additional setup
- Datadog/New Relic integration: Overkill for current needs

## Technology Decisions

### Tenacity Library Selection

**Decision**: Use Tenacity for retry logic (already Apache 2.0 licensed, battle-tested)

**Configuration**:
```python
from tenacity import retry, wait_exponential_jitter, stop_after_attempt, retry_if_exception_type

# S3 retry decorator
s3_retry = retry(
    wait=wait_exponential_jitter(initial=1, max=30, jitter=1),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type((
        boto3.exceptions.ClientError,  # ThrottlingException, ProvisionedThroughputExceededException
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    )),
    reraise=True,
)
```

**Alternatives Considered**:
- Backoff: Less feature-rich than Tenacity
- Custom implementation: High maintenance burden

### Circuit Breaker Implementation

**Decision**: Implement custom circuit breaker based on Martin Fowler's pattern, leveraging tenacity's `before_sleep` hook for state tracking

**State Machine**:
- CLOSED: Normal operation, failures counted
- OPEN: Fast fail, no operations executed
- HALF_OPEN: Test recovery with limited operations

**Configuration**:
```python
CIRCUIT_BREAKER_CONFIG = {
    "failure_threshold": 5,          # Open after 5 failures
    "recovery_timeout_seconds": 30,  # Transition to HALF_OPEN after 30s
    "half_open_success_threshold": 1, # Close after 1 success in HALF_OPEN
}
```

**Alternatives Considered**:
- PyBreaker: No longer maintained
- Tenacity circuit breaker: Not available in current version
- Custom state machine: Full control, matches tenacity patterns

## Best Practices Applied

### AWS S3 Retry Best Practices

1. **Exponential Backoff with Jitter**: Wait time = min(initial × 2^n + random(0, jitter), max)
2. **Retryable Exceptions**:
   - `ThrottlingException` (429)
   - `ProvisionedThroughputExceededException` (400)
   - `InternalError` (500)
   - `ServiceUnavailable` (503)
3. **Non-Retryable Exceptions**:
   - `AccessDeniedException`
   - `InvalidObjectState`
   - `NoSuchKey`

### HTTP API Retry Best Practices

1. **Retry on**:
   - 5xx Server Errors (500, 502, 503, 504)
   - 429 Rate Limit (respect Retry-After header)
   - Network timeouts
   - Connection errors
2. **Do Not Retry on**:
   - 4xx Client Errors (except 429)
   - Authentication failures (401)
   - Authorization failures (403)

## Dependencies

| Dependency | Version | Purpose |
|------------|---------|---------|
| tenacity | >=8.0.0 | Retry decorators with exponential backoff |
| boto3 | 1.33.13 | AWS SDK (already in requirements) |
| requests | latest | HTTP client for API calls |

## References

- [Tenacity Documentation](https://tenacity.readthedocs.io/)
- [AWS SDK Retry Behavior](https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html)
- [AWS Well-Architected - Control and Limit Retry Calls](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/rel_mitigate_interaction_failure_limit_retries.html)
- [AWS Builders Library - Timeouts, Retries and Backoff with Jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/)
