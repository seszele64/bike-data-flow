# Retry Decorator API Contract

**Module**: `wrm_pipeline.retry`

---

## Decorators

### with_s3_retry

Decorator for S3 operations with automatic retry on transient errors.

```python
from wrm_pipeline.retry import with_s3_retry

@with_s3_retry(
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 1.0,
    circuit_breaker: CircuitBreaker | None = None,
)
def upload_to_s3(bucket: str, key: str, data: bytes) -> str:
    """Upload data to S3 with automatic retry on transient errors."""
```

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| max_attempts | int | No | 5 | Maximum number of attempts (including initial) |
| base_delay | float | No | 1.0 | Initial delay between retries (seconds) |
| max_delay | float | No | 30.0 | Maximum delay between retries (seconds) |
| jitter | float | No | 1.0 | Random jitter to add to delay (seconds) |
| circuit_breaker | CircuitBreaker | No | None | Optional circuit breaker instance |

**Raises:**

- `RetryExhaustedException`: When all retries are exhausted
- `AccessDeniedException`: When permission denied (not retried)
- `NoSuchKey`: When key doesn't exist (not retried)

**Retryable Exceptions:**

- `ThrottlingException` (429)
- `ProvisionedThroughputExceededException`
- `InternalError` (500)
- `ServiceUnavailable` (503)
- `requests.exceptions.ConnectionError`
- `requests.exceptions.Timeout`

---

### with_api_retry

Decorator for HTTP API calls with automatic retry on transient errors.

```python
from wrm_pipeline.retry import with_api_retry

@with_api_retry(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    jitter: float = 0.5,
    respect_retry_after: bool = True,
)
def fetch_api_data(url: str, params: dict = None) -> dict:
    """Fetch data from API with automatic retry on transient errors."""
```

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| max_attempts | int | No | 3 | Maximum number of attempts (including initial) |
| base_delay | float | No | 0.5 | Initial delay between retries (seconds) |
| max_delay | float | No | 10.0 | Maximum delay between retries (seconds) |
| jitter | float | No | 0.5 | Random jitter to add to delay (seconds) |
| respect_retry_after | bool | No | True | Respect Retry-After header in 429 responses |

**Raises:**

- `RetryExhaustedException`: When all retries are exhausted
- `requests.HTTPError`: On 4xx errors (not retried, except 429)

**Retryable Exceptions:**

- `requests.exceptions.ConnectionError`
- `requests.exceptions.Timeout`
- `requests.exceptions.HTTPError` with 5xx status code
- HTTP 429 with Retry-After header

---

## Exceptions

### RetryExhaustedException

Raised when all retry attempts are exhausted.

```python
class RetryExhaustedException(Exception):
    attempts: int
    last_exception: Exception
    total_time_ms: float
```

### CircuitOpenException

Raised when circuit breaker is open and request is not allowed.

```python
class CircuitOpenException(Exception):
    operation_name: str
    state: CircuitState
```

---

## Usage Examples

### Basic S3 Upload

```python
from wrm_pipeline.retry import with_s3_retry

@with_s3_retry()
def upload_data(bucket: str, key: str, data: bytes) -> str:
    s3_client.put_object(Bucket=bucket, Key=key, Body=data)
    return key

result = upload_data("my-bucket", "data.json", b"hello world")
```

### S3 Upload with Circuit Breaker

```python
from wrm_pipeline.retry import with_s3_retry, CircuitBreaker

cb = CircuitBreaker("upload", failure_threshold=5)

@with_s3_retry(circuit_breaker=cb)
def upload_with_protection(bucket: str, key: str, data: bytes) -> str:
    s3_client.put_object(Bucket=bucket, Key=key, Body=data)
    return key
```

### API Call with Retry-After

```python
from wrm_pipeline.retry import with_api_retry

@with_api_retry(respect_retry_after=True)
def fetch_rate_limited(url: str) -> dict:
    response = requests.get(url)
    response.raise_for_status()
    return response.json()
```
