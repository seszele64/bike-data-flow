"""Circuit breaker implementation for retry logic."""

from enum import Enum
from dataclasses import dataclass


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
