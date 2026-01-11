# Quickstart: Retry Logic with Exponential Backoff

**Feature Branch**: `002-retry-logic`  
**Date**: 2026-01-11

## Installation

```bash
pip install tenacity
```

## Basic Usage

### S3 Operations with Retry

```python
from wrm_pipeline.retry import with_s3_retry

@with_s3_retry()
def upload_to_s3(bucket: str, key: str, data: bytes) -> str:
    """Upload data to S3 with automatic retry on transient errors."""
    s3_client.put_object(Bucket=bucket, Key=key, Body=data)
    return key

# With custom configuration
@with_s3_retry(
    max_attempts=5,
    base_delay=1.0,
    max_delay=30.0,
)
def upload_large_file(bucket: str, key: str, filepath: str) -> str:
    """Upload large file with custom retry settings."""
    s3_client.upload_file(filepath, bucket, key)
    return key
```

### API Calls with Retry

```python
from wrm_pipeline.retry import with_api_retry

@with_api_retry()
def fetch_weather_data(url: str, params: dict) -> dict:
    """Fetch weather data with automatic retry on server errors."""
    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json()

# With Retry-After header support
@with_api_retry(
    respect_retry_after=True,
    max_attempts=3,
)
def fetch_rate_limited_api(url: str) -> dict:
    """Fetch from rate-limited API, respecting Retry-After headers."""
    response = requests.get(url)
    response.raise_for_status()
    return response.json()
```

### Circuit Breaker

```python
from wrm_pipeline.retry import CircuitBreaker, CircuitBreakerConfiguration

# Configure circuit breaker
cb_config = CircuitBreakerConfiguration(
    failure_threshold=5,
    recovery_timeout_seconds=30.0,
)

# Create circuit breaker for an operation
upload_circuit = CircuitBreaker("s3_upload", config=cb_config)

def upload_with_circuit_breaker(bucket: str, key: str, data: bytes) -> str:
    """Upload with circuit breaker protection."""
    if not upload_circuit.allow_request():
        raise CircuitOpenException("s3_upload", upload_circuit.state)
    
    try:
        s3_client.put_object(Bucket=bucket, Key=key, Body=data)
        upload_circuit.record_success()
        return key
    except Exception as e:
        upload_circuit.record_failure()
        raise
```

## Configuration Reference

### RetryConfiguration Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_attempts` | int | 5 | Maximum number of attempts (including initial) |
| `base_delay` | float | 1.0 | Initial delay between retries (seconds) |
| `max_delay` | float | 30.0 | Maximum delay between retries (seconds) |
| `exponential_base` | float | 2.0 | Base for exponential calculation |
| `jitter` | float | 1.0 | Random jitter to add to delay (seconds) |
| `retry_on_exceptions` | tuple | (ThrottlingException, ...) | Exceptions that trigger retry |
| `ignore_on_exceptions` | tuple | (AccessDeniedException, ...) | Exceptions that are not retried |

### CircuitBreakerConfiguration Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `failure_threshold` | int | 5 | Number of failures before opening circuit |
| `recovery_timeout_seconds` | float | 30.0 | Time in OPEN state before HALF_OPEN |
| `success_threshold` | int | 1 | Successes needed in HALF_OPEN to close |

## Preset Configurations

```python
from wrm_pipeline.retry import RetryPresets

# S3 upload preset
upload_config = RetryPresets.S3_UPLOAD

# API call preset  
api_config = RetryPresets.API_CALL
```

## Error Handling

```python
from wrm_pipeline.retry import RetryExhaustedException

try:
    result = upload_to_s3("my-bucket", "data.json", data)
except RetryExhaustedException as e:
    print(f"Upload failed after {e.attempts} attempts")
    print(f"Last error: {e.last_exception}")
    # Handle permanent failure
except Exception as e:
    # Non-retryable error
    print(f"Non-retryable error: {e}")
```

## Integration with Dagster

```python
from wrm_pipeline.retry import with_s3_retry
from dagster import asset

@asset
def processed_bike_data(context, raw_data: pd.DataFrame) -> pd.DataFrame:
    """Asset that uploads processed data with retry protection."""
    
    @with_s3_retry()
    def upload():
        s3_client.upload_fileobj(
            raw_data.to_parquet(),
            "bike-data-bucket",
            f"processed/{context.run_id}.parquet"
        )
    
    upload()
    return raw_data
```

## Logging

Retry operations emit structured logs:

```python
import logging

logging.basicConfig(level=logging.INFO)

# Example output:
# INFO: Retrying upload_to_s3 in 1.0 seconds (attempt 1/5)
# INFO: Retrying upload_to_s3 in 2.0 seconds (attempt 2/5) 
# INFO: upload_to_s3 succeeded on attempt 3
```

## Best Practices

1. **Use appropriate presets**: `S3_UPLOAD` for S3, `API_CALL` for HTTP
2. **Add jitter**: Always use jitter to prevent thundering herd
3. **Set reasonable limits**: Don't retry indefinitely
4. **Handle permanent failures**: Catch `RetryExhaustedException` for final failures
5. **Monitor circuit breaker state**: Log state transitions for debugging
