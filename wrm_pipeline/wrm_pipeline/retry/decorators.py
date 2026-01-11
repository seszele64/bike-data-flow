"""Retry decorator utilities."""

from functools import wraps
from typing import Callable, Type, Optional, Tuple, Any

import tenacity

from .config import RetryConfiguration
from .tenacity_base import (
    get_tenacity_decorator,
    get_wait_strategy,
    get_stop_strategy,
    get_retry_strategy,
)
from .exceptions import RetryExhaustedException


def with_retry(
    config: Optional[RetryConfiguration] = None,
    exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    max_attempts: Optional[int] = None,
    base_delay: Optional[float] = None,
    max_delay: Optional[float] = None,
    exponential_base: Optional[float] = None,
    jitter: Optional[float] = None,
    respect_retry_after: bool = True,
) -> Callable:
    """Decorator to add retry logic with exponential backoff to a function.
    
    Can be used with explicit configuration or individual parameters.
    
    Args:
        config: Optional RetryConfiguration instance
        exceptions: Tuple of exception types to retry on
        max_attempts: Maximum retry attempts (default: 3)
        base_delay: Base delay in seconds (default: 1.0)
        max_delay: Maximum delay cap in seconds (default: 60.0)
        exponential_base: Base for exponential backoff (default: 2.0)
        jitter: Random jitter in seconds (default: 0.1)
        respect_retry_after: Whether to respect Retry-After headers
        
    Returns:
        Decorated function with retry logic
        
    Examples:
        @with_retry(max_attempts=5, base_delay=0.5)
        def unreliable_function():
            ...
            
        @with_retry(RetryConfiguration(max_attempts=10))
        def another_function():
            ...
    """
    def decorator(func: Callable) -> Callable:
        # Build configuration from parameters
        if config is not None:
            effective_config = config
        else:
            effective_config = RetryConfiguration(
                max_attempts=max_attempts or 3,
                base_delay=base_delay or 1.0,
                max_delay=max_delay or 60.0,
                exponential_base=exponential_base or 2.0,
                jitter=jitter or 0.1,
                retry_on_exceptions=exceptions or (Exception,),
                respect_retry_after=respect_retry_after,
            )
        
        tenacity_decorator = get_tenacity_decorator(effective_config)
        
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                return tenacity_decorator(func)(*args, **kwargs)
            except tenacity.RetryError as e:
                # Get the last exception from the RetryError's outcome
                last_exception = e.last_attempt.exception() if e.last_attempt else Exception("Unknown retry error")
                raise RetryExhaustedException(
                    message=f"All {effective_config.max_attempts} retry attempts exhausted",
                    attempts=effective_config.max_attempts,
                    last_exception=last_exception,
                ) from last_exception
        
        return wrapper
    return decorator
