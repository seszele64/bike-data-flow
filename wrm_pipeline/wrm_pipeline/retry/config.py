"""Retry configuration settings."""

from dataclasses import dataclass
from typing import Optional, Tuple, Type


@dataclass
class RetryConfiguration:
    """Configuration for retry behavior with exponential backoff.
    
    Attributes:
        max_attempts: Maximum number of retry attempts (default: 3)
        base_delay: Base delay in seconds between retries (default: 1.0)
        max_delay: Maximum delay cap in seconds (default: 60.0)
        exponential_base: Base for exponential backoff multiplier (default: 2.0)
        jitter: Random jitter added to delay in seconds (default: 0.1)
        retry_on_exceptions: Tuple of exception types to retry on (default: all)
        respect_retry_after: Whether to respect Retry-After headers (default: True)
    """
    
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: float = 0.1
    retry_on_exceptions: Tuple[Type[Exception], ...] = (Exception,)
    respect_retry_after: bool = True
    
    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.base_delay <= 0:
            raise ValueError(f"base_delay must be > 0, got {self.base_delay}")
        if self.max_delay <= 0:
            raise ValueError(f"max_delay must be > 0, got {self.max_delay}")
        if self.exponential_base < 1:
            raise ValueError(f"exponential_base must be >= 1, got {self.exponential_base}")
        if self.jitter < 0:
            raise ValueError(f"jitter must be >= 0, got {self.jitter}")
