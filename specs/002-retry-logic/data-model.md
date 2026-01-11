# Data Model: Retry Logic with Exponential Backoff

**Feature Branch**: `002-retry-logic`  
**Date**: 2026-01-11  
**Status**: Draft

## Entities Overview

```
┌─────────────────────┐         ┌──────────────────────┐
│  RetryConfiguration │         │ CircuitBreakerConfig │
├─────────────────────┤         ├──────────────────────┤
│ - max_attempts      │         │ - failure_threshold  │
│ - base_delay        │         │ - recovery_timeout   │
│ - max_delay         │         │ - success_threshold  │
│ - exponential_base  │         └──────────────────────┘
│ - jitter            │                  │
│ - retry_on_exceptions│                 │
│ - ignore_on_exceptions│                ▼
└─────────────────────┘         ┌──────────────────────┐
          │                     │    CircuitBreaker    │
          ▼                     ├──────────────────────┤
┌─────────────────────┐         │ - state: CLOSED|     │
│   RetryDecorator    │         │          OPEN|       │
├─────────────────────┤         │          HALF_OPEN  │
│ - config: RetryConfig│        │ - failure_count     │
│ - circuit_breaker   │         │ - last_failure_time │
│ - attempt_counter   │         │ - operation_name    │
└─────────────────────┘         └──────────────────────┘
          │
          ▼
┌─────────────────────┐
│   RetryResult       │
├─────────────────────┤
│ - success: bool     │
│ - attempts: int     │
│ - last_exception    │
│ - total_time_ms     │
└─────────────────────┘
```

## RetryConfiguration

Configuration parameters for retry behavior.

```python
@dataclass
class RetryConfiguration:
    """Configuration for retry behavior with exponential backoff."""
    
    # Core retry parameters
    max_attempts: int = 5
    base_delay: float = 1.0  # seconds
    max_delay: float = 30.0  # seconds
    exponential_base: float = 2.0
    jitter: float = 1.0  # seconds
    
    # Exception handling
    retry_on_exceptions: tuple[type[Exception], ...] = (
        # S3 exceptions
        "ThrottlingException",
        "ProvisionedThroughputExceededException",
        "InternalError",
        "ServiceUnavailable",
        # HTTP exceptions  
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.HTTPError,
    )
    ignore_on_exceptions: tuple[type[Exception], ...] = (
        # Non-retryable exceptions
        "AccessDeniedException",
        "NoSuchKey",
        "InvalidSignatureException",
        ValueError,
        TypeError,
    )
    
    # Logging and callbacks
    log_level: str = "WARNING"
    before_retry_hook: Callable | None = None
    after_retry_hook: Callable | None = None
```

### Validation Rules

- `max_attempts`: Must be >= 1 (default: 5)
- `base_delay`: Must be >= 0 (default: 1.0)
- `max_delay`: Must be >= base_delay (default: 30.0)
- `exponential_base`: Must be >= 1.0 (default: 2.0)
- `jitter`: Must be >= 0 (default: 1.0)

### Preset Configurations

```python
class RetryPresets:
    """Preset configurations for common operation types."""
    
    S3_UPLOAD = RetryConfiguration(
        max_attempts=5,
        base_delay=1.0,
        max_delay=30.0,
        jitter=1.0,
    )
    
    S3_DOWNLOAD = RetryConfiguration(
        max_attempts=5,
        base_delay=1.0,
        max_delay=30.0,
        jitter=1.0,
    )
    
    API_CALL = RetryConfiguration(
        max_attempts=3,
        base_delay=0.5,
        max_delay=10.0,
        jitter=0.5,
    )
```

## CircuitBreakerConfiguration

Configuration parameters for circuit breaker behavior.

```python
@dataclass
class CircuitBreakerConfiguration:
    """Configuration for circuit breaker state machine."""
    
    failure_threshold: int = 5  # Open after N failures
    recovery_timeout_seconds: float = 30.0  # Time in OPEN state before HALF_OPEN
    success_threshold: int = 1  # Successes needed in HALF_OPEN to close
    
    # State persistence
    state_persistence: str = "memory"  # "memory" or "redis"
    redis_key_prefix: str = "circuit_breaker:"
```

### State Transitions

```
                    ┌─────────────────────────────────────────┐
                    │              FAILURE                    │
                    ▼                                         │
    ┌─────────┐ ──────────> ┌─────────┐                      │
    │  CLOSED │             │  OPEN   │ <─────────────────────┘
    │  ▲      │             │  ▲  │   │                      │
    │  │      │ SUCCESS     │  │   │   │ FAILURE             │
    │  │      │             │  │   │   │                     │
    └─────────┘             └─────────┘                      │
           │                     │                           │
           │ timeout             │ timeout                   │
           │ (recovery_timeout)  │ (recovery_timeout)        │
           ▼                     ▼                           │
    ┌─────────┐             ┌─────────┐                      │
    │ HALF_   │ ──────────> │  OPEN   │ ────────────────────┘
    │  OPEN   │   SUCCESS   │         │    FAILURE
    └─────────┘             └─────────┘
```

## CircuitBreaker

State machine for preventing cascade failures.

```python
class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenException(Exception):
    """Raised when circuit breaker is open."""
    def __init__(self, operation_name: str, state: CircuitState):
        self.operation_name = operation_name
        self.state = state
        super().__init__(f"Circuit breaker open for {operation_name}")


class CircuitBreaker:
    """Thread-safe circuit breaker implementation."""
    
    def __init__(
        self,
        operation_name: str,
        config: CircuitBreakerConfiguration | None = None,
    ):
        self.operation_name = operation_name
        self.config = config or CircuitBreakerConfiguration()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._lock = threading.Lock()
    
    @property
    def state(self) -> CircuitState:
        """Get current state, transitioning if needed."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_transition_to_half_open():
                    self._transition_to(CircuitState.HALF_OPEN)
            return self._state
    
    def record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._failure_count = 0
                self._transition_to(CircuitState.CLOSED)
            else:
                self._failure_count = 0
    
    def record_failure(self) -> None:
        """Record a failed operation."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.OPEN)
            elif self._failure_count >= self.config.failure_threshold:
                self._transition_to(CircuitState.OPEN)
    
    def allow_request(self) -> bool:
        """Check if request should be allowed."""
        return self.state != CircuitState.OPEN
    
    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to new state with logging."""
        old_state = self._state
        self._state = new_state
        logger.info(
            f"Circuit breaker '{self.operation_name}' transitioned "
            f"from {old_state.value} to {new_state.value}"
        )
    
    def _should_transition_to_half_open(self) -> bool:
        """Check if enough time has passed to try recovery."""
        if self._last_failure_time is None:
            return True
        elapsed = time.time() - self._last_failure_time
        return elapsed >= self.config.recovery_timeout_seconds
```

## RetryResult

Result of a retry-decorated operation.

```python
@dataclass
class RetryResult:
    """Result of a retry-decorated operation."""
    
    success: bool
    attempts: int
    last_exception: Exception | None = None
    total_time_ms: float = 0.0
    
    @property
    def succeeded_on_first_attempt(self) -> bool:
        """Check if operation succeeded without retries."""
        return self.success and self.attempts == 1
    
    @property
    def failed_after_retries(self) -> bool:
        """Check if operation failed after exhausting retries."""
        return not self.success and self.attempts > 1
```

## Retry Decorators

### S3 Retry Decorator

```python
def with_s3_retry(
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 1.0,
    circuit_breaker: CircuitBreaker | None = None,
):
    """
    Retry decorator for S3 operations with exponential backoff.
    
    Args:
        max_attempts: Maximum number of attempts (including initial)
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)
        jitter: Random jitter to add to delay (seconds)
        circuit_breaker: Optional circuit breaker instance
    
    Returns:
        Decorated function with retry logic
    """
    config = RetryConfiguration(
        max_attempts=max_attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        jitter=jitter,
    )
    
    def decorator(func: Callable[T, R]) -> Callable[T, R]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> R:
            # ... retry logic implementation
            pass
        return wrapper
    return decorator
```

### API Retry Decorator

```python
def with_api_retry(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    jitter: float = 0.5,
    respect_retry_after: bool = True,
):
    """
    Retry decorator for HTTP API calls with exponential backoff.
    
    Args:
        max_attempts: Maximum number of attempts (including initial)
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)
        jitter: Random jitter to add to delay (seconds)
        respect_retry_after: Respect Retry-After header in 429 responses
    
    Returns:
        Decorated function with retry logic
    """
    # ... implementation
```

## Exception Types

### Retryable Exceptions

| Exception | Code | Source | Retryable |
|-----------|------|--------|-----------|
| `ThrottlingException` | 429 | S3 | Yes |
| `ProvisionedThroughputExceededException` | 400 | S3 | Yes |
| `InternalError` | 500 | S3 | Yes |
| `ServiceUnavailable` | 503 | S3/API | Yes |
| `ConnectionError` | - | requests | Yes |
| `Timeout` | - | requests | Yes |
| `HTTPError` (5xx) | 5xx | requests | Yes |

### Non-Retryable Exceptions

| Exception | Code | Source | Reason |
|-----------|------|--------|--------|
| `AccessDeniedException` | 403 | S3 | Permission issue |
| `NoSuchKey` | 404 | S3 | Resource doesn't exist |
| `InvalidSignatureException` | 403 | S3 | Auth issue |
| `HTTPError` (4xx, !=429) | 4xx | requests | Client error |
| `ValueError` | - | Application | Validation error |
| `TypeError` | - | Application | Programming error |
