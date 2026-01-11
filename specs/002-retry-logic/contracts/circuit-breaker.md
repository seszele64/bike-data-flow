# Circuit Breaker API Contract

**Module**: `wrm_pipeline.retry`

---

## CircuitState

Enum representing the circuit breaker states.

```python
from enum import Enum

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Fast fail, no operations allowed
    HALF_OPEN = "half_open"  # Testing recovery
```

---

## CircuitBreakerConfiguration

Configuration for circuit breaker behavior.

```python
from dataclasses import dataclass

@dataclass
class CircuitBreakerConfiguration:
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    success_threshold: int = 1
    state_persistence: str = "memory"
    redis_key_prefix: str = "circuit_breaker:"
```

**Configuration Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| failure_threshold | int | 5 | Number of failures before opening circuit |
| recovery_timeout_seconds | float | 30.0 | Time in OPEN state before HALF_OPEN |
| success_threshold | int | 1 | Successes needed in HALF_OPEN to close |
| state_persistence | str | "memory" | "memory" or "redis" |
| redis_key_prefix | str | "circuit_breaker:" | Redis key prefix for persistence |

---

## CircuitBreaker

Thread-safe circuit breaker implementation.

```python
from wrm_pipeline.retry import CircuitBreaker, CircuitBreakerConfiguration, CircuitState

# Create configuration
config = CircuitBreakerConfiguration(
    failure_threshold=5,
    recovery_timeout_seconds=30.0,
)

# Create circuit breaker
circuit = CircuitBreaker(
    operation_name="s3_upload",
    config=config,
)
```

### Constructor

```python
circuit = CircuitBreaker(
    operation_name: str,
    config: CircuitBreakerConfiguration | None = None,
)
```

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| operation_name | str | Yes | Unique identifier for this circuit breaker |
| config | CircuitBreakerConfiguration | No | Configuration instance (uses defaults if None) |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| state | CircuitState | Current state of the circuit breaker |
| operation_name | str | Unique identifier for this circuit breaker |
| failure_count | int | Number of consecutive failures |
| last_failure_time | float \| None | Timestamp of last failure |

### Methods

#### allow_request()

Check if request should be allowed.

```python
if circuit.allow_request():
    # Execute operation
    pass
else:
    raise CircuitOpenException(circuit.operation_name, circuit.state)
```

**Returns:** `bool` - True if request is allowed, False if circuit is open

#### record_success()

Record a successful operation.

```python
try:
    result = operation()
    circuit.record_success()
except Exception:
    circuit.record_failure()
    raise
```

**Side Effects:**
- If in HALF_OPEN state: transitions to CLOSED, resets failure count
- If in CLOSED state: resets failure count

#### record_failure()

Record a failed operation.

```python
try:
    operation()
except Exception as e:
    circuit.record_failure()
    raise
```

**Side Effects:**
- If in HALF_OPEN state: transitions to OPEN
- If in CLOSED state: increments failure count, may transition to OPEN

---

## CircuitOpenException

Exception raised when circuit breaker is open.

```python
from wrm_pipeline.retry import CircuitOpenException

try:
    if not circuit.allow_request():
        raise CircuitOpenException(circuit.operation_name, circuit.state)
except CircuitOpenException as e:
    print(f"Circuit {e.operation_name} is {e.state.value}")
```

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| operation_name | str | Name of the circuit breaker |
| state | CircuitState | Current state of the circuit breaker |

---

## State Machine Transitions

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

---

## Usage Examples

### Basic Usage

```python
from wrm_pipeline.retry import CircuitBreaker, CircuitOpenException

circuit = CircuitBreaker("api_calls")

def safe_api_call(url: str) -> dict:
    if not circuit.allow_request():
        raise CircuitOpenException(circuit.operation_name, circuit.state)
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        circuit.record_success()
        return response.json()
    except Exception as e:
        circuit.record_failure()
        raise
```

### With Decorator Pattern

```python
from wrm_pipeline.retry import CircuitBreaker, with_s3_retry

upload_circuit = CircuitBreaker(
    operation_name="s3_upload",
    failure_threshold=5,
    recovery_timeout_seconds=30.0,
)

@with_s3_retry(circuit_breaker=upload_circuit)
def upload_with_protection(bucket: str, key: str, data: bytes) -> str:
    s3_client.put_object(Bucket=bucket, Key=key, Body=data)
    return key
```

### Monitoring State Changes

```python
import logging

logger = logging.getLogger(__name__)

circuit = CircuitBreaker("critical_operation")

# State changes are automatically logged at INFO level
# Configure logging to see these events:
logging.getLogger("wrm_pipeline.retry").setLevel(logging.INFO)
```
