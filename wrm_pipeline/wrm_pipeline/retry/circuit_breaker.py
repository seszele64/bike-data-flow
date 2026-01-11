"""Circuit breaker implementation for preventing cascade failures."""

import threading
import time
import logging
from enum import Enum, auto
from dataclasses import dataclass
from typing import Callable, Optional, Any
from contextlib import contextmanager

from .exceptions import CircuitOpenException


logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states.
    
    Attributes:
        CLOSED: Normal operation, requests pass through
        OPEN: Circuit tripped, requests rejected immediately
        HALF_OPEN: Testing recovery, limited requests allowed
    """
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfiguration:
    """Configuration for circuit breaker behavior.
    
    Attributes:
        failure_threshold: Number of failures before opening circuit (default: 5)
        success_threshold: Successes needed to close from HALF_OPEN (default: 3)
        recovery_timeout: Seconds before attempting recovery (default: 60.0)
        half_open_max_calls: Max calls allowed in HALF_OPEN state (default: 3)
    """
    
    failure_threshold: int = 5
    success_threshold: int = 3
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 3
    
    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if self.failure_threshold < 1:
            raise ValueError(f"failure_threshold must be >= 1, got {self.failure_threshold}")
        if self.success_threshold < 1:
            raise ValueError(f"success_threshold must be >= 1, got {self.success_threshold}")
        if self.recovery_timeout <= 0:
            raise ValueError(f"recovery_timeout must be > 0, got {self.recovery_timeout}")
        if self.half_open_max_calls < 1:
            raise ValueError(f"half_open_max_calls must be >= 1, got {self.half_open_max_calls}")


class CircuitBreaker:
    """Thread-safe circuit breaker implementation.
    
    Implements the circuit breaker pattern to prevent cascade failures
    in distributed systems by tracking consecutive failures and 
    transitioning between states.
    
    State Machine:
    - CLOSED → OPEN: When failure count exceeds threshold
    - OPEN → HALF_OPEN: After recovery timeout elapses
    - HALF_OPEN → CLOSED: On successful operation
    - HALF_OPEN → OPEN: On failed operation
    
    Args:
        name: Unique identifier for this circuit breaker
        config: Circuit breaker configuration
        
    Attributes:
        name: Circuit breaker name
        state: Current state (CLOSED, OPEN, HALF_OPEN)
        failure_count: Consecutive failures in CLOSED state
        success_count: Successful calls in HALF_OPEN state
        last_failure_time: Timestamp of last failure
        next_attempt_time: When to allow next attempt in OPEN state
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfiguration] = None,
    ):
        self.name = name
        self.config = config or CircuitBreakerConfiguration()
        
        # State management (thread-safe)
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._next_attempt_time: float = 0.0
        self._lock = threading.RLock()
        
        logger.info(
            f"Circuit breaker '{name}' initialized in CLOSED state "
            f"(failure_threshold={self.config.failure_threshold}, "
            f"recovery_timeout={self.config.recovery_timeout}s)"
        )
    
    @property
    def state(self) -> CircuitState:
        """Get current state (thread-safe)."""
        with self._lock:
            return self._state
    
    @property
    def failure_count(self) -> int:
        """Get consecutive failure count."""
        with self._lock:
            return self._failure_count
    
    @property
    def success_count(self) -> int:
        """Get success count in HALF_OPEN state."""
        with self._lock:
            return self._success_count
    
    @property
    def last_failure_time(self) -> Optional[float]:
        """Get timestamp of last failure."""
        with self._lock:
            return self._last_failure_time
    
    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r}, "
            f"state={self._state.name}, "
            f"failures={self._failure_count})"
        )
    
    def _update_state_for_attempt(self) -> None:
        """Check and update state before attempting an operation."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                current_time = time.monotonic()
                if current_time >= self._next_attempt_time:
                    # Transition to HALF_OPEN for testing recovery
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info(
                        f"Circuit breaker '{self.name}' transitioned from OPEN to HALF_OPEN "
                        f"(recovery timeout elapsed)"
                    )
                else:
                    # Still open, reject request
                    remaining = self._next_attempt_time - current_time
                    raise CircuitOpenException(
                        message=f"Circuit breaker '{self.name}' is OPEN. "
                               f"Retry after {remaining:.1f}s.",
                        failure_count=self._failure_count,
                    )
    
    def _on_success(self) -> None:
        """Handle successful operation."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # Success in HALF_OPEN transitions to CLOSED
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                logger.info(
                    f"Circuit breaker '{self.name}' transitioned from HALF_OPEN to CLOSED "
                    f"(recovery successful)"
                )
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0
    
    def _on_failure(self, exception: Exception) -> None:
        """Handle failed operation."""
        with self._lock:
            self._last_failure_time = time.monotonic()
            
            if self._state == CircuitState.HALF_OPEN:
                # Failure in HALF_OPEN transitions back to OPEN
                self._state = CircuitState.OPEN
                self._next_attempt_time = time.monotonic() + self.config.recovery_timeout
                logger.warning(
                    f"Circuit breaker '{self.name}' transitioned from HALF_OPEN to OPEN "
                    f"(failure during recovery test)"
                )
            elif self._state == CircuitState.CLOSED:
                # Increment failure count
                self._failure_count += 1
                if self._failure_count >= self.config.failure_threshold:
                    # Threshold exceeded, transition to OPEN
                    self._state = CircuitState.OPEN
                    self._next_attempt_time = time.monotonic() + self.config.recovery_timeout
                    logger.warning(
                        f"Circuit breaker '{self.name}' transitioned from CLOSED to OPEN "
                        f"(failure count {self._failure_count} >= threshold {self.config.failure_threshold})"
                    )
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute a function through the circuit breaker.
        
        Args:
            func: Function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func
            
        Returns:
            Result from func
            
        Raises:
            CircuitOpenException: If circuit is OPEN
            Exception: Any exception from func
        """
        self._update_state_for_attempt()
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            raise
    
    @contextmanager
    def context(self):
        """Context manager for circuit breaker operations.
        
        Usage:
            with circuit_breaker.context():
                result = some_operation()
        
        Yields:
            None
            
        Raises:
            CircuitOpenException: If circuit is OPEN
            Exception: Any exception from wrapped operation
        """
        self._update_state_for_attempt()
        try:
            yield
            self._on_success()
        except Exception as e:
            self._on_failure(e)
            raise
    
    def reset(self) -> None:
        """Reset circuit breaker to initial CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
            self._next_attempt_time = 0.0
            logger.info(f"Circuit breaker '{self.name}' has been reset to CLOSED state")
    
    def get_metrics(self) -> dict[str, Any]:
        """Get circuit breaker metrics.
        
        Returns:
            Dictionary with current metrics
        """
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.name,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout": self.config.recovery_timeout,
                "is_open": self._state == CircuitState.OPEN,
                "is_half_open": self._state == CircuitState.HALF_OPEN,
                "is_closed": self._state == CircuitState.CLOSED,
                "time_until_retry": max(0, self._next_attempt_time - time.monotonic())
                    if self._state == CircuitState.OPEN else 0,
            }
    
    def __enter__(self):
        """Context manager entry."""
        self._update_state_for_attempt()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if exc_type is None:
            self._on_success()
        else:
            self._on_failure(exc_val)
        return False  # Don't suppress exceptions
